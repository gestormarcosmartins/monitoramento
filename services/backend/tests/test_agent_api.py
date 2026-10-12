import unittest
from unittest.mock import patch

from central_monitor.agent_api import EnrollRequest, HeartbeatRequest, _hash


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


if __name__ == "__main__":
    unittest.main()
