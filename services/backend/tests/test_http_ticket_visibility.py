import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import Header
from fastapi.testclient import TestClient

from central_monitor.auth import Identity, current_identity
from central_monitor.main import app


TENANT = "11111111-1111-1111-1111-111111111111"
TICKET = "33333333-3333-3333-3333-333333333333"


def identity_from_header(x_test_user: str = Header(alias="X-Test-User")) -> Identity:
    return Identity(x_test_user, f"{x_test_user}@example.test", x_test_user)


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class VisibilityConnection:
    def __init__(self, requester="client-a"):
        self.requester = requester
        self.queries = []

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        self.queries.append(sql)
        if "FROM cm_incidents" in sql and "requester_user_id=%s" in sql:
            return Result(row={"id": TICKET, "title": "Portal"} if params[2] == self.requester else None)
        if "FROM cm_incidents" in sql:
            if "requester_user_id=%s" in sql and params[2] != self.requester:
                return Result(row=None)
            return Result(row={"id": TICKET, "title": "Operação"})
        if "FROM cm_incident_comments" in sql:
            if "NOT is_internal" in sql:
                return Result(rows=[{"id": "public", "body": "Resposta pública"}])
            return Result(rows=[
                {"id": "public", "body": "Resposta pública", "is_internal": False},
                {"id": "private", "body": "SEGREDO INTERNO", "is_internal": True},
            ])
        if "FROM cm_ticket_attachments" in sql:
            return Result(rows=[{"id": "clean", "original_name": "relatorio.pdf", "scan_status": "clean"}])
        if "FROM cm_ticket_tasks" in sql or "FROM cm_ticket_solutions" in sql or "FROM cm_incident_history" in sql:
            return Result(rows=[])
        if "FROM cm_ticket_duplicates" in sql:
            return Result(row=None)
        return Result(rows=[])


class HttpTicketVisibilityTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity] = identity_from_header
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    @staticmethod
    def actor(identity, _tenant_id, _roles=("admin", "manager", "operator", "viewer", "client", "auditor")):
        role = "operator" if identity.subject == "operator-a" else "client"
        return {"id": identity.subject, "role": role, "row_scope": "company" if role == "operator" else "own", "active": True, "is_master": False}

    def test_portal_http_dto_excludes_private_notes_and_unreleased_attachments(self):
        connection = VisibilityConnection()

        @contextmanager
        def scoped(_tenant_id):
            yield connection

        with patch("central_monitor.catalog.authorize", side_effect=self.actor), patch(
            "central_monitor.catalog.tenant_connection", side_effect=scoped
        ):
            response = self.client.get(
                f"/api/v1/portal/tickets/{TICKET}",
                headers={"X-Test-User": "client-a", "X-Tenant-ID": TENANT},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([entry["body"] for entry in payload["entries"]], ["Resposta pública"])
        self.assertEqual([item["id"] for item in payload["attachments"]], ["clean"])
        serialized = response.text
        self.assertNotIn("SEGREDO INTERNO", serialized)
        self.assertNotIn("quarantine", serialized)
        self.assertTrue(any("NOT is_internal" in sql for sql in connection.queries))
        self.assertTrue(any("scan_status='clean'" in sql for sql in connection.queries))

    def test_another_client_receives_not_found_without_ticket_metadata(self):
        connection = VisibilityConnection()

        @contextmanager
        def scoped(_tenant_id):
            yield connection

        with patch("central_monitor.catalog.authorize", side_effect=self.actor), patch(
            "central_monitor.catalog.tenant_connection", side_effect=scoped
        ):
            response = self.client.get(
                f"/api/v1/portal/tickets/{TICKET}",
                headers={"X-Test-User": "client-b", "X-Tenant-ID": TENANT},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Ticket not found")
        self.assertEqual(response.json()["code"], "not_found")
        self.assertNotIn("Portal", response.text)

    def test_operator_http_detail_keeps_authorized_private_note(self):
        connection = VisibilityConnection()

        @contextmanager
        def scoped(_tenant_id):
            yield connection

        with patch("central_monitor.ticketing.authorize", side_effect=self.actor), patch(
            "central_monitor.ticketing.tenant_connection", side_effect=scoped
        ):
            response = self.client.get(
                f"/api/v1/tickets/{TICKET}",
                headers={"X-Test-User": "operator-a", "X-Tenant-ID": TENANT},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("SEGREDO INTERNO", response.text)
        comment_queries = [sql for sql in connection.queries if "FROM cm_incident_comments" in sql]
        self.assertEqual(len(comment_queries), 1)
        self.assertNotIn("NOT is_internal", comment_queries[0])

    def test_operational_detail_also_hides_another_clients_ticket(self):
        connection = VisibilityConnection()

        @contextmanager
        def scoped(_tenant_id):
            yield connection

        with patch("central_monitor.ticketing.authorize", side_effect=self.actor), patch(
            "central_monitor.ticketing.tenant_connection", side_effect=scoped
        ):
            response = self.client.get(
                f"/api/v1/tickets/{TICKET}",
                headers={"X-Test-User": "client-b", "X-Tenant-ID": TENANT},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Ticket not found")
        self.assertEqual(response.json()["code"], "not_found")
        self.assertNotIn("Operação", response.text)


if __name__ == "__main__":
    unittest.main()
