import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from central_monitor.auth import Identity, current_identity, tenant_header
from central_monitor.main import app

TENANT = "00000000-0000-0000-0000-000000000000"


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, master_row):
        self.master_row = master_row
        self.statements: list[str] = []

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        self.statements.append(sql)
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return Result()
        if sql.startswith("INSERT INTO cm_users(external_subject,email,display_name,is_master) VALUES (%s,%s,%s,NOT EXISTS"):
            return Result(row=self.master_row)
        if sql.startswith("INSERT INTO cm_users(external_subject,email,display_name,is_master) VALUES (%s,%s,%s,%s) RETURNING"):
            return Result(row={
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "external_subject": params[0],
                "email": params[1],
                "display_name": params[2],
                "active": True,
                "is_master": params[3],
            })
        if sql.startswith("SELECT role,row_scope"):
            return Result(row=None)
        if sql.startswith("INSERT INTO cm_user_tenants") or sql.startswith("INSERT INTO cm_access_grants") or sql.startswith("INSERT INTO cm_audit_log"):
            return Result(row=None)
        raise AssertionError(f"Unexpected statement: {sql}")


class CreateUserTests(unittest.TestCase):
    def setUp(self):
        self.master_row = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "external_subject": "master-subject",
            "email": "master@nexora.local",
            "display_name": "Master",
            "active": True,
            "is_master": True,
        }
        app.dependency_overrides[current_identity] = lambda: Identity(
            subject="master-subject", email="master@nexora.local", display_name="Master",
        )
        app.dependency_overrides[tenant_header] = lambda: TENANT

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_create_user_provisions_keycloak_and_grants_access(self):
        connection = FakeConnection(self.master_row)

        @contextmanager
        def scoped(_tenant_id=None):
            yield connection

        client = TestClient(app)
        try:
            with patch("central_monitor.operations.connect", side_effect=scoped), \
                 patch("central_monitor.operations.tenant_connection", side_effect=scoped), \
                 patch("central_monitor.operations.KeycloakAdminClient") as keycloak_cls, \
                 patch("central_monitor.operations.send_email", side_effect=RuntimeError("SMTP not configured")):
                keycloak_cls.return_value.create_user.return_value = {
                    "id": "keycloak-user-id", "temporary_password": "Temp1234!",
                }
                response = client.post(
                    "/api/v1/users",
                    headers={"X-Tenant-ID": TENANT},
                    json={
                        "email": "new.user@example.com",
                        "display_name": "New User",
                        "is_master": False,
                        "tenant_ids": [TENANT],
                    },
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["external_subject"], "keycloak-user-id")
        self.assertEqual(body["temporary_password"], "Temp1234!")
        self.assertFalse(body["email_sent"])
        self.assertTrue(any(s.startswith("INSERT INTO cm_access_grants") for s in connection.statements))

    def test_create_user_requires_master(self):
        connection = FakeConnection({**self.master_row, "is_master": False})

        @contextmanager
        def scoped(_tenant_id=None):
            yield connection

        client = TestClient(app)
        try:
            with patch("central_monitor.operations.connect", side_effect=scoped), \
                 patch("central_monitor.operations.tenant_connection", side_effect=scoped):
                response = client.post(
                    "/api/v1/users",
                    headers={"X-Tenant-ID": TENANT},
                    json={"email": "x@example.com", "display_name": "X", "is_master": False, "tenant_ids": [TENANT]},
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 403)

    def test_create_user_requires_master_flag_or_tenant(self):
        client = TestClient(app)
        try:
            response = client.post(
                "/api/v1/users",
                headers={"X-Tenant-ID": TENANT},
                json={"email": "x@example.com", "display_name": "X", "is_master": False, "tenant_ids": []},
            )
        finally:
            client.close()
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
