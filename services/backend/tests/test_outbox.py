import unittest
from contextlib import contextmanager
from unittest.mock import patch

from central_monitor.worker import process_outbox, retry_delay_seconds


class Result:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, handler):
        self.handler = handler
        self.statements = []

    def execute(self, query, params=()):
        normalized = " ".join(str(query).split())
        self.statements.append((normalized, params))
        return self.handler(normalized, params)


class OutboxStateMachineTests(unittest.TestCase):
    def test_retry_delay_uses_bounded_exponential_backoff(self) -> None:
        self.assertEqual(retry_delay_seconds(1), 2)
        self.assertEqual(retry_delay_seconds(4), 16)
        self.assertEqual(retry_delay_seconds(50), 512)

    def test_revoked_requester_is_rejected_before_remote_side_effect(self) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        command = {
            "id": 71,
            "tenant_id": tenant_id,
            "topic": "incident.created",
            "payload": {"incident_id": "incident-9"},
            "attempts": 0,
            "requested_by": "user-revoked",
        }
        global_connection = Connection(lambda _query, _params: Result(rows=[{"id": tenant_id}]))
        claim_connection = Connection(
            lambda query, _params: Result(rows=[command]) if "RETURNING o.id" in query else Result()
        )

        def authorization_handler(query, _params):
            if query.startswith("SELECT 1 FROM cm_users"):
                return Result(row=None)
            return Result()

        authorization_connection = Connection(authorization_handler)

        @contextmanager
        def global_db():
            yield global_connection

        scoped_connections = iter([claim_connection, authorization_connection])

        @contextmanager
        def scoped_db(requested_tenant):
            self.assertEqual(requested_tenant, tenant_id)
            yield next(scoped_connections)

        with patch("central_monitor.worker.connect", side_effect=global_db), patch(
            "central_monitor.worker.tenant_connection", side_effect=scoped_db
        ), patch("central_monitor.worker.sync_incident") as remote_effect:
            process_outbox()

        remote_effect.assert_not_called()
        executed_sql = "\n".join(statement for statement, _ in authorization_connection.statements)
        self.assertIn("state='rejected'", executed_sql)
        self.assertIn("requester_access_revoked", executed_sql)
        self.assertIn("outbox.authorization_rejected", executed_sql)

    def test_lost_remote_response_becomes_uncertain_and_requires_reconciliation(self) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        command = {
            "id": 72,
            "tenant_id": tenant_id,
            "topic": "incident.created",
            "payload": {"incident_id": "incident-10"},
            "attempts": 9,
            "requested_by": "user-authorized",
        }
        global_connection = Connection(lambda _query, _params: Result(rows=[{"id": tenant_id}]))
        claim_connection = Connection(
            lambda query, _params: Result(rows=[command]) if "RETURNING o.id" in query else Result()
        )

        def authorization_handler(query, _params):
            if query.startswith("SELECT 1 FROM cm_users"):
                return Result(row={"authorized": 1})
            return Result()

        authorization_connection = Connection(authorization_handler)
        failure_connection = Connection(lambda _query, _params: Result())
        incident_connection = Connection(lambda _query, _params: Result())

        @contextmanager
        def global_db():
            yield global_connection

        scoped_connections = iter([
            claim_connection,
            authorization_connection,
            failure_connection,
            incident_connection,
        ])

        @contextmanager
        def scoped_db(requested_tenant):
            self.assertEqual(requested_tenant, tenant_id)
            yield next(scoped_connections)

        with patch("central_monitor.worker.connect", side_effect=global_db), patch(
            "central_monitor.worker.tenant_connection", side_effect=scoped_db
        ), patch("central_monitor.worker.sync_incident", side_effect=TimeoutError("response lost")) as remote_effect:
            process_outbox()

        remote_effect.assert_called_once_with(tenant_id, "incident-10")
        outbox_updates = [
            params for sql, params in failure_connection.statements
            if "UPDATE cm_outbox SET attempts=" in sql
        ]
        self.assertEqual(len(outbox_updates), 1)
        self.assertEqual(outbox_updates[0][0], 10)
        self.assertEqual(outbox_updates[0][2], "uncertain")
        self.assertEqual(outbox_updates[0][3], "attempts_exhausted_effect_requires_reconciliation")
        self.assertTrue(any("outcome=%s" in sql and params[0] == "uncertain" for sql, params in failure_connection.statements))
        self.assertTrue(any("sync_status='failed'" in sql for sql, _ in incident_connection.statements))


if __name__ == "__main__":
    unittest.main()
