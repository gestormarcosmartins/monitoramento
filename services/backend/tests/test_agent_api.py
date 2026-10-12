import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from central_monitor.agent_api import EnrollRequest, HeartbeatRequest, _hash
from central_monitor.main import app

TENANT = "00000000-0000-0000-0000-000000000000"


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class EnrollConnection:
    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        if sql.startswith("UPDATE cm_agent_enrollment_tokens"):
            return Result(row={"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})
        if sql.startswith("INSERT INTO cm_agents"):
            return Result(row={
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "tenant_id": TENANT,
                "name": "Servidor SQL",
                "machine_id": "SERVER01:Windows",
                "configuration": {},
            })
        raise AssertionError(f"Unexpected statement: {sql}")


class AgentApiTests(unittest.TestCase):
    @patch("central_monitor.agent_api.get_settings")
    def test_agent_credentials_are_hashed_with_server_secret(self, settings) -> None:
        settings.return_value.central_secret_key = "x" * 32
        self.assertEqual(_hash("token"), _hash("token"))
        self.assertNotEqual(_hash("token"), _hash("other-token"))

    def test_enrollment_requires_machine_identity(self) -> None:
        request = EnrollRequest(
            enrollment_token="00000000-0000-0000-0000-000000000000.secret",
            name="Servidor SQL",
            machine_id="SERVER01:Windows",
        )
        self.assertEqual(request.capabilities, [])

    def test_heartbeat_carries_individual_intervals(self) -> None:
        heartbeat = HeartbeatRequest(
            heartbeat_interval_seconds=60,
            services_interval_seconds=120,
            health={"machine": "SERVER01"},
        )
        self.assertEqual(heartbeat.heartbeat_interval_seconds, 60)
        self.assertEqual(heartbeat.services_interval_seconds, 120)

    @patch("central_monitor.agent_api.get_settings")
    def test_enroll_response_exposes_agent_id(self, settings) -> None:
        settings.return_value.central_secret_key = "x" * 32
        settings.return_value.central_public_url = "https://nexora.example"
        # Regressão: o agente (Credentials.agent_id) exige a chave "agent_id" na resposta.
        # Devolver apenas "id" (a coluna crua do banco) quebra EnrollAsync em todo agente real.
        connection = EnrollConnection()

        @contextmanager
        def scoped(_tenant_id):
            yield connection

        client = TestClient(app)
        try:
            with patch("central_monitor.agent_api.tenant_connection", side_effect=scoped):
                response = client.post(
                    "/api/v1/agent/enroll",
                    json={
                        "enrollment_token": f"{TENANT}.secret",
                        "name": "Servidor SQL",
                        "machine_id": "SERVER01:Windows",
                    },
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["agent_id"], "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        self.assertNotIn("id", body)
        self.assertIn("agent_token", body)


if __name__ == "__main__":
    unittest.main()
