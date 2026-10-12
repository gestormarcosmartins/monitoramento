import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import Header
from fastapi.testclient import TestClient

from central_monitor.auth import Identity, current_identity
from central_monitor.main import app


TENANT = "11111111-1111-1111-1111-111111111111"


def identity_from_header(x_test_user: str = Header(alias="X-Test-User")) -> Identity:
    return Identity(x_test_user, f"{x_test_user}@example.test", x_test_user)


class Result:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class TicketListConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        self.calls.append((sql, tuple(params)))
        if "count(*) AS total" in sql:
            return Result(row={"total": 12})
        return Result(rows=[{
            "id": "33333333-3333-3333-3333-333333333333",
            "tenant_id": TENANT,
            "title": "Falha filtrada",
            "status": "new",
            "priority": "high",
            "requester_name": "Solicitante",
            "assignee_team_name": None,
            "assignee_name": None,
        }])


class HttpTicketListTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity] = identity_from_header
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    @staticmethod
    def actor(identity, _tenant_id, _roles=("admin", "manager", "operator", "viewer", "client", "auditor")):
        return {
            "id": identity.subject,
            "role": "client",
            "row_scope": "own",
            "active": True,
            "is_master": False,
        }

    def test_list_contract_exposes_pagination_filters_cutoff_and_sync_state(self):
        connection = TicketListConnection()

        @contextmanager
        def scoped(_tenant_id):
            yield connection

        with patch("central_monitor.ticketing.authorize", side_effect=self.actor), patch(
            "central_monitor.ticketing.tenant_connection", side_effect=scoped
        ):
            response = self.client.get(
                "/api/v1/tickets?q=Falha&status=new&priority=high&page=2&page_size=5",
                headers={"X-Test-User": "client-a", "X-Tenant-ID": TENANT},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["page_size"], 5)
        self.assertEqual(payload["total"], 12)
        self.assertEqual(payload["pages"], 3)
        self.assertEqual(payload["sync_state"], "projected")
        self.assertTrue(payload["data_as_of"])
        self.assertEqual(payload["filters_applied"], {"q": "Falha", "status": "new", "priority": "high", "actionable": False})
        self.assertEqual(payload["items"][0]["tenant_id"], TENANT)

        count_sql, count_params = connection.calls[0]
        list_sql, list_params = connection.calls[1]
        self.assertIn("requester_user_id=%s OR i.assignee_user_id=%s", count_sql)
        self.assertIn("LIMIT %s OFFSET %s", list_sql)
        self.assertEqual(count_params[:3], (TENANT, "client-a", "client-a"))
        self.assertEqual(list_params[-2:], (5, 5))

    def test_openapi_publishes_named_ticket_list_response(self):
        schema = self.client.get("/api/openapi.json").json()
        operation = schema["paths"]["/api/v1/tickets"]["get"]
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(response_schema["$ref"], "#/components/schemas/TicketListResponse")
        required = set(schema["components"]["schemas"]["TicketListResponse"]["required"])
        self.assertTrue({"items", "page", "page_size", "total", "pages", "data_as_of", "filters_applied"}.issubset(required))


if __name__ == "__main__":
    unittest.main()
