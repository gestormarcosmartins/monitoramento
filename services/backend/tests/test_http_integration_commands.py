import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from central_monitor.auth import Identity, current_identity
from central_monitor.main import app


TENANT = "11111111-1111-1111-1111-111111111111"


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class IntegrationConnection:
    def __init__(self):
        self.statements = []

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        self.statements.append((sql, params))
        if sql.startswith("SELECT id,topic,state"):
            return Result(rows=[{
                "id": 41,
                "topic": "incident.created",
                "state": "uncertain",
                "state_reason": "remote_response_lost",
                "correlation_id": "33333333-3333-3333-3333-333333333333",
                "attempts": 2,
                "available_at": datetime(2026, 10, 11, tzinfo=timezone.utc),
                "locked_at": None,
                "worker_id": None,
                "last_error": "timeout",
                "created_at": datetime(2026, 10, 11, tzinfo=timezone.utc),
                "processed_at": None,
            }])
        if sql.startswith("SELECT state,count(*)"):
            return Result(rows=[{"state": "uncertain", "total": 1}])
        if "oldest_queue_seconds" in sql:
            return Result(row={
                "queued": 0,
                "uncertain": 1,
                "conflicts": 0,
                "rejected": 0,
                "oldest_queue_seconds": 0,
                "seconds_since_success": 0,
            })
        if sql.startswith("UPDATE cm_outbox SET state='pending'"):
            return Result(row={
                "id": 41,
                "topic": "incident.created",
                "state": "pending",
                "attempts": 2,
                "available_at": datetime(2026, 10, 11, tzinfo=timezone.utc),
            })
        return Result()


class HttpIntegrationCommandTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity] = lambda: Identity(
            "master", "master@example.test", "Master"
        )
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    @staticmethod
    @contextmanager
    def scoped(connection):
        yield connection

    def test_list_uses_tenant_scoped_connection(self):
        connection = IntegrationConnection()
        with patch("central_monitor.operations.authorize", return_value={"id": "master"}), patch(
            "central_monitor.operations.tenant_connection",
            return_value=self.scoped(connection),
        ) as tenant_connection, patch("central_monitor.operations.connect") as unscoped:
            response = self.client.get(
                "/api/v1/integration-commands", headers={"X-Tenant-ID": TENANT}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["state"], "uncertain")
        self.assertEqual(response.json()["metrics"]["uncertain"], 1)
        tenant_connection.assert_called_once_with(TENANT)
        unscoped.assert_not_called()

    def test_retry_uses_tenant_scope_for_update_and_audit(self):
        connection = IntegrationConnection()
        with patch("central_monitor.operations.authorize", return_value={"id": "master"}), patch(
            "central_monitor.operations.tenant_connection",
            side_effect=[self.scoped(connection), self.scoped(connection)],
        ) as tenant_connection, patch(
            "central_monitor.feature_flags.require_feature"
        ) as require_feature, patch("central_monitor.operations.connect") as unscoped:
            response = self.client.post(
                "/api/v1/integration-commands/41/retry",
                headers={"X-Tenant-ID": TENANT},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "pending")
        self.assertEqual(tenant_connection.call_count, 2)
        require_feature.assert_called_once()
        unscoped.assert_not_called()
        self.assertTrue(any("INSERT INTO cm_audit_log" in sql for sql, _ in connection.statements))


if __name__ == "__main__":
    unittest.main()
