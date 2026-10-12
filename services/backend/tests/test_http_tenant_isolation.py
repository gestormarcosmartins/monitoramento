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
    return Identity(
        subject=x_test_user,
        email=f"{x_test_user}@example.test",
        display_name=x_test_user,
    )


class HttpTenantIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[current_identity] = identity_from_test_header
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.client.close()

    @staticmethod
    def authorize(identity: Identity, tenant_id: str, roles=("admin", "operator", "viewer")):
        allowed = {"user-a": TENANT_A, "user-b": TENANT_B}
        if allowed.get(identity.subject) != tenant_id:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        return {"id": identity.subject, "role": "viewer", "is_master": False}

    @staticmethod
    @contextmanager
    def tenant_db(tenant_id: str):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"id": f"asset-{tenant_id[-1]}", "tenant_id": tenant_id, "name": f"Asset {tenant_id[-1]}"}
        ]
        connection = MagicMock()
        connection.execute.return_value = cursor
        yield connection

    def test_two_http_identities_only_receive_their_authorized_company(self) -> None:
        with patch("central_monitor.main.authorize_tenant", side_effect=self.authorize), patch(
            "central_monitor.main.tenant_connection", side_effect=self.tenant_db
        ):
            response_a = self.client.get(
                "/api/v1/assets", headers={"X-Test-User": "user-a", "X-Tenant-ID": TENANT_A}
            )
            response_b = self.client.get(
                "/api/v1/assets", headers={"X-Test-User": "user-b", "X-Tenant-ID": TENANT_B}
            )

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual({row["tenant_id"] for row in response_a.json()}, {TENANT_A})
        self.assertEqual({row["tenant_id"] for row in response_b.json()}, {TENANT_B})
        self.assertTrue({row["id"] for row in response_a.json()}.isdisjoint({row["id"] for row in response_b.json()}))

    def test_cross_company_header_is_rejected_before_query(self) -> None:
        tenant_db = MagicMock()
        with patch("central_monitor.main.authorize_tenant", side_effect=self.authorize), patch(
            "central_monitor.main.tenant_connection", tenant_db
        ):
            response = self.client.get(
                "/api/v1/assets", headers={"X-Test-User": "user-a", "X-Tenant-ID": TENANT_B}
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Tenant access denied")
        tenant_db.assert_not_called()

    def test_company_header_is_mandatory(self) -> None:
        response = self.client.get("/api/v1/assets", headers={"X-Test-User": "user-a"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
