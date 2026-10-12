import unittest
from unittest.mock import Mock, patch

from central_monitor.glpi import GlpiClient


class GlpiClientTests(unittest.TestCase):
    def client(self) -> GlpiClient:
        client = GlpiClient.__new__(GlpiClient)
        client.url = "http://glpi/apirest.php"
        client.app_token = ""
        client.user_token = ""
        client.username = "nexora"
        client.password = "secret"
        return client

    @patch("central_monitor.glpi.httpx.get")
    def test_starts_session_with_internal_credentials(self, get: Mock) -> None:
        get.return_value.json.return_value = {"session_token": "session"}
        get.return_value.raise_for_status.return_value = None
        self.assertEqual(self.client()._session(), "session")
        self.assertEqual(get.call_args.kwargs["auth"], ("nexora", "secret"))

    def test_maps_critical_incident_to_highest_glpi_priority(self) -> None:
        client = self.client()
        client._request = Mock(return_value={"id": 42})
        ticket_id = client.create_ticket({"id": "incident-1", "tenant_id": "tenant-1", "tenant_name": "Cliente", "title": "API indisponivel", "message": "HTTP 500", "severity": "disaster"})
        self.assertEqual(ticket_id, 42)
        payload = client._request.call_args.args[2]
        self.assertEqual(payload["input"]["priority"], 6)

    def test_extracts_version_from_full_session(self) -> None:
        self.assertEqual(GlpiClient._version_from_session({"session": {"glpi_version": "11.0.1"}}), "11.0.1")

    @patch("central_monitor.glpi.httpx.get")
    def test_discovers_required_capabilities(self, get: Mock) -> None:
        def response_for(url: str, **_kwargs: object) -> Mock:
            response = Mock(status_code=200, content=b"{}")
            response.raise_for_status.return_value = None
            if url.endswith("/getFullSession"):
                response.json.return_value = {"session": {"glpi_version": "11.0.1"}}
            elif url.endswith("/Plugin"):
                response.json.return_value = [{"id": 7, "name": "Formcreator", "state": 1}]
            else:
                response.json.return_value = {}
            return response

        client = self.client()
        client._session = Mock(return_value="session")
        get.side_effect = response_for
        manifest = client.capabilities()
        self.assertEqual(manifest["version"], "11.0.1")
        self.assertTrue(manifest["supports"]["tickets"])
        self.assertEqual(manifest["plugins"][0]["name"], "Formcreator")


if __name__ == "__main__":
    unittest.main()
