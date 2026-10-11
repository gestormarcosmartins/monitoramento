import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import HTTPException

from central_monitor.app_incident_api import (
    AppIncidentCreate,
    SCOPES,
    _hash_token,
    _request_hash,
    app_create_incident,
    require_scope,
    revoke_api_client,
    router,
)

CLIENT_ID = "11111111-1111-4111-8111-111111111111"


class AppIncidentApiTests(unittest.TestCase):
    @patch("central_monitor.app_incident_api.get_settings")
    def test_tokens_are_hashed_with_the_server_secret(self, settings) -> None:
        settings.return_value.central_secret_key = "x" * 32
        self.assertEqual(_hash_token("token"), _hash_token("token"))
        self.assertNotEqual(_hash_token("token"), _hash_token("other"))

    def test_request_hash_is_canonical(self) -> None:
        first = AppIncidentCreate(title="Falha SQL", description="Banco indisponível")
        second = AppIncidentCreate(description="Banco indisponível", title="Falha SQL")
        self.assertEqual(_request_hash(first), _request_hash(second))

    def test_scope_is_required_per_operation(self) -> None:
        require_scope({"scopes": ["incidents:read"]}, "incidents:read")
        with self.assertRaises(HTTPException) as raised:
            require_scope({"scopes": ["incidents:read"]}, "incidents:finish")
        self.assertEqual(raised.exception.status_code, 403)

    def test_declared_scopes_and_routes_cover_initial_app(self) -> None:
        self.assertEqual(
            SCOPES,
            {"incidents:read", "incidents:create", "incidents:attend", "incidents:finish"},
        )
        routes = {(route.path, next(iter(route.methods))) for route in router.routes}
        paths = {path for path, _method in routes}
        self.assertIn("/api/v1/app/incidents", paths)
        self.assertIn("/api/v1/app/incidents/{incident_id}", paths)
        self.assertIn("/api/v1/app/incidents/{incident_id}/attend", paths)
        self.assertIn("/api/v1/app/incidents/{incident_id}/finish", paths)


def _connection_returning(row):
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = row
    context = MagicMock()
    context.__enter__.return_value = connection
    return context, connection


class ApiClientRevocationTests(unittest.TestCase):
    @patch("central_monitor.app_incident_api.tenant_connection")
    @patch("central_monitor.app_incident_api.authorize")
    def test_revoke_deactivates_client_and_audits(self, authorize, tenant_connection) -> None:
        authorize.return_value = {"id": "admin-id"}
        context, connection = _connection_returning({"id": CLIENT_ID})
        tenant_connection.return_value = context

        self.assertIsNone(revoke_api_client(UUID(CLIENT_ID), "tenant-id", MagicMock()))

        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertIn("UPDATE cm_api_clients SET active=false", statements[0])
        self.assertIn("api_client.revoke", statements[1])

    @patch("central_monitor.app_incident_api.tenant_connection")
    @patch("central_monitor.app_incident_api.authorize")
    def test_revoke_unknown_or_already_revoked_client_returns_404(
        self, authorize, tenant_connection
    ) -> None:
        authorize.return_value = {"id": "admin-id"}
        context, connection = _connection_returning(None)
        tenant_connection.return_value = context

        with self.assertRaises(HTTPException) as raised:
            revoke_api_client(UUID(CLIENT_ID), "tenant-id", MagicMock())
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(connection.execute.call_count, 1)


class AppIncidentTeamValidationTests(unittest.TestCase):
    @patch("central_monitor.app_incident_api.tenant_connection")
    def test_create_rejects_inactive_or_unknown_team(self, tenant_connection) -> None:
        context, connection = _connection_returning(None)
        tenant_connection.return_value = context
        client = {"tenant_id": "tenant-id", "id": CLIENT_ID, "scopes": ["incidents:create"]}
        payload = AppIncidentCreate(
            title="Falha SQL",
            description="Banco indisponível",
            assignee_team_id=UUID(CLIENT_ID),
        )

        with self.assertRaises(HTTPException) as raised:
            app_create_incident(payload, "app-ocorrencia-1", client)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertFalse(
            any("INSERT INTO cm_incidents" in c.args[0] for c in connection.execute.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
