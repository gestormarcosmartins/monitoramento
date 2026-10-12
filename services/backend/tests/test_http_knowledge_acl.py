import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

from central_monitor.auth import Identity, current_identity
from central_monitor.main import app


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def identity_from_test_header(x_test_user: str = Header(alias="X-Test-User")) -> Identity:
    return Identity(subject=x_test_user, email=f"{x_test_user}@example.test", display_name=x_test_user)


class HttpKnowledgeAclTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[current_identity] = identity_from_test_header
        self.client = TestClient(app)
        self.executions: list[tuple[str, str, tuple]] = []

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.client.close()

    @staticmethod
    def authorize(identity: Identity, tenant_id: str, _roles=None):
        grants = {
            "operator-a": (TENANT_A, "operator"),
            "client-a": (TENANT_A, "client"),
            "operator-b": (TENANT_B, "operator"),
            "master": (tenant_id, "master"),
        }
        allowed_tenant, role = grants.get(identity.subject, (None, None))
        if allowed_tenant != tenant_id:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        return {"id": identity.subject, "role": role, "is_master": role == "master"}

    @contextmanager
    def tenant_db(self, tenant_id: str):
        connection = MagicMock()

        def execute(query, params):
            self.executions.append((tenant_id, query, params))
            role = params[1]
            rows = {
                (TENANT_A, "operator"): [{"id": "a-internal", "title": "VPN interna", "visibility": "internal", "status": "published", "version": 1}],
                (TENANT_A, "client"): [{"id": "a-customer", "title": "Portal do cliente", "visibility": "customer", "status": "published", "version": 1}],
                (TENANT_B, "operator"): [{"id": "b-internal", "title": "Banco filial", "visibility": "internal", "status": "published", "version": 1}],
                (TENANT_A, "master"): [
                    {"id": "a-internal", "title": "VPN interna", "visibility": "internal", "status": "published", "version": 1},
                    {"id": "a-customer", "title": "Portal do cliente", "visibility": "customer", "status": "published", "version": 1},
                ],
            }.get((tenant_id, role), [])
            cursor = MagicMock()
            cursor.fetchall.return_value = rows
            return cursor

        connection.execute.side_effect = execute
        yield connection

    def get(self, user: str, tenant_id: str, query: str = ""):
        return self.client.get(
            f"/api/v1/knowledge?q={query}",
            headers={"X-Test-User": user, "X-Tenant-ID": tenant_id},
        )

    def test_company_and_role_acl_are_applied_before_search(self) -> None:
        with patch("central_monitor.governance.authorize", side_effect=self.authorize), patch(
            "central_monitor.governance.tenant_connection", side_effect=self.tenant_db
        ):
            operator_a = self.get("operator-a", TENANT_A, "VPN")
            client_a = self.get("client-a", TENANT_A, "Portal")
            operator_b = self.get("operator-b", TENANT_B, "Banco")

        self.assertEqual([row["id"] for row in operator_a.json()], ["a-internal"])
        self.assertEqual([row["id"] for row in client_a.json()], ["a-customer"])
        self.assertEqual([row["id"] for row in operator_b.json()], ["b-internal"])
        self.assertTrue(all("ANY(acl_roles)" in query and "ILIKE" in query for _, query, _ in self.executions))
        self.assertEqual({tenant for tenant, _, _ in self.executions}, {TENANT_A, TENANT_B})
        self.assertEqual({params[0] for _, _, params in self.executions}, {TENANT_A, TENANT_B})

    def test_cross_company_access_is_rejected_before_database_query(self) -> None:
        tenant_db = MagicMock()
        with patch("central_monitor.governance.authorize", side_effect=self.authorize), patch(
            "central_monitor.governance.tenant_connection", tenant_db
        ):
            response = self.get("operator-a", TENANT_B, "VPN")
        self.assertEqual(response.status_code, 403)
        tenant_db.assert_not_called()

    def test_master_explicitly_bypasses_article_role_acl_inside_selected_company(self) -> None:
        with patch("central_monitor.governance.authorize", side_effect=self.authorize), patch(
            "central_monitor.governance.tenant_connection", side_effect=self.tenant_db
        ):
            response = self.get("master", TENANT_A)
        self.assertEqual({row["id"] for row in response.json()}, {"a-internal", "a-customer"})
        self.assertEqual(self.executions[-1][2][1:3], ("master", "master"))


if __name__ == "__main__":
    unittest.main()
