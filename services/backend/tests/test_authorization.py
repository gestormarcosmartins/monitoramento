import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from central_monitor.auth import Identity
from central_monitor.main import authorize_tenant
from central_monitor.operations import authorize, users


IDENTITY = Identity(subject="operator-1", email="operator@example.test", display_name="Operator")
USER = {"id": "user-1", "active": True, "is_master": False}


class AuthorizationRlsTests(unittest.TestCase):
    def test_all_granted_roles_can_use_default_company_read_authorization(self):
        for role in ("admin", "manager", "operator", "auditor", "viewer", "client"):
            with self.subTest(role=role):
                connection = MagicMock()
                connection.execute.return_value.fetchone.return_value = {"role": role}

                @contextmanager
                def scoped(tenant_id):
                    self.assertEqual(tenant_id, "tenant-1")
                    yield connection

                with patch("central_monitor.main.ensure_user", return_value=USER), patch(
                    "central_monitor.main.tenant_connection", side_effect=scoped
                ):
                    actor = authorize_tenant(IDENTITY, "tenant-1")

                self.assertEqual(actor["role"], role)

    def test_read_only_roles_are_rejected_from_operator_mutations(self):
        for role in ("auditor", "viewer", "client"):
            with self.subTest(role=role):
                connection = MagicMock()
                connection.execute.return_value.fetchone.return_value = {"role": role}

                @contextmanager
                def scoped(_tenant_id):
                    yield connection

                with patch("central_monitor.main.ensure_user", return_value=USER), patch(
                    "central_monitor.main.tenant_connection", side_effect=scoped
                ):
                    with self.assertRaisesRegex(Exception, "403"):
                        authorize_tenant(IDENTITY, "tenant-1", ("admin", "manager", "operator"))

    def test_main_authorization_reads_grant_inside_tenant_context(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"role": "operator"}

        @contextmanager
        def scoped(tenant_id):
            self.assertEqual(tenant_id, "tenant-1")
            yield connection

        with patch("central_monitor.main.ensure_user", return_value=USER), patch(
            "central_monitor.main.tenant_connection", side_effect=scoped
        ) as tenant_db:
            actor = authorize_tenant(IDENTITY, "tenant-1")

        self.assertEqual(actor["role"], "operator")
        tenant_db.assert_called_once_with("tenant-1")

    def test_shared_authorization_reads_grant_inside_tenant_context(self):
        global_connection = MagicMock()
        global_connection.execute.return_value.fetchone.return_value = USER
        tenant_connection = MagicMock()
        tenant_connection.execute.return_value.fetchone.return_value = {"role": "viewer"}

        @contextmanager
        def global_db():
            yield global_connection

        @contextmanager
        def scoped(tenant_id):
            self.assertEqual(tenant_id, "tenant-1")
            yield tenant_connection

        with patch("central_monitor.operations.connect", side_effect=global_db), patch(
            "central_monitor.operations.tenant_connection", side_effect=scoped
        ) as tenant_db:
            actor = authorize(IDENTITY, "tenant-1")

        self.assertEqual(actor["role"], "viewer")
        tenant_db.assert_called_once_with("tenant-1")

    def test_master_user_listing_collects_grants_per_tenant_context(self):
        user_cursor = MagicMock()
        user_cursor.fetchall.return_value = [{"id": "user-1", "display_name": "User"}]
        tenant_cursor = MagicMock()
        tenant_cursor.fetchall.return_value = [{"id": "tenant-1"}]
        global_connection = MagicMock()
        global_connection.execute.side_effect = [user_cursor, tenant_cursor]
        grant_cursor = MagicMock()
        grant_cursor.fetchall.return_value = [{"user_id": "user-1", "tenant_id": "tenant-1"}]
        tenant_db = MagicMock()
        tenant_db.execute.return_value = grant_cursor

        @contextmanager
        def global_db():
            yield global_connection

        @contextmanager
        def scoped(tenant_id):
            self.assertEqual(tenant_id, "tenant-1")
            yield tenant_db

        with patch("central_monitor.operations.authorize", return_value={"is_master": True}), patch(
            "central_monitor.operations.connect", side_effect=global_db
        ), patch("central_monitor.operations.tenant_connection", side_effect=scoped):
            result = users("tenant-1", IDENTITY)

        self.assertEqual(result[0]["tenant_ids"], ["tenant-1"])


if __name__ == "__main__":
    unittest.main()
