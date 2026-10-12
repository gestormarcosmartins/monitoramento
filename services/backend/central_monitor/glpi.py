from typing import Any

import httpx

from .settings import get_settings


class GlpiClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = settings.glpi_api_url.rstrip("/")
        self.app_token = settings.glpi_app_token
        self.user_token = settings.glpi_user_token
        self.username = settings.glpi_username
        self.password = settings.glpi_password

    def _base_headers(self) -> dict[str, str]:
        return {"App-Token": self.app_token} if self.app_token else {}

    def _session(self) -> str:
        headers = self._base_headers()
        auth = None
        if self.user_token:
            headers["Authorization"] = f"user_token {self.user_token}"
        elif self.username and self.password:
            auth = (self.username, self.password)
        else:
            raise RuntimeError("GLPI credentials are not configured")
        response = httpx.get(f"{self.url}/initSession", headers=headers, auth=auth, timeout=15)
        response.raise_for_status()
        return response.json()["session_token"]

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        token = self._session()
        headers = {**self._base_headers(), "Session-Token": token}
        try:
            response = httpx.request(method, f"{self.url}/{path.lstrip('/')}", headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            return response.json() if response.content else None
        finally:
            httpx.get(f"{self.url}/killSession", headers=headers, timeout=10)

    def create_ticket(self, incident: dict[str, Any]) -> int:
        severity = incident.get("severity", "warning")
        priority = {"info": 2, "warning": 3, "average": 4, "high": 5, "disaster": 6}.get(severity, 3)
        tenant = incident.get("tenant_name", incident["tenant_id"])
        content = (
            f"Incidente criado automaticamente pelo Nexora.\n"
            f"Empresa: {tenant}\n"
            f"Incidente Nexora: {incident['id']}\n"
            f"Detalhes: {incident.get('message') or incident['title']}"
        )
        result = self._request("POST", "Ticket", {"input": {"name": incident["title"], "content": content, "status": 1, "urgency": priority, "impact": priority, "priority": priority}})
        return int(result["id"])

    def update_ticket_status(self, ticket_id: int, status: str) -> None:
        glpi_status = {"new": 1, "triaged": 1, "assigned": 2, "acknowledged": 2, "in_progress": 2,
                       "waiting_customer": 4, "waiting_supplier": 4, "resolved": 5, "closed": 6,
                       "cancelled": 6, "duplicate": 6, "ignored": 6}[status]
        self._request("PUT", f"Ticket/{ticket_id}", {"input": {"id": ticket_id, "status": glpi_status}})

    def add_followup(self, ticket_id: int, body: str, private: bool = False) -> int:
        result = self._request("POST", "ITILFollowup", {"input": {"itemtype": "Ticket", "items_id": ticket_id, "content": body, "is_private": int(private)}})
        return int(result["id"])

    def add_task(self, ticket_id: int, body: str, minutes: int) -> int:
        result = self._request("POST", "TicketTask", {"input": {
            "itemtype": "Ticket", "items_id": ticket_id, "content": body,
            "actiontime": minutes * 60, "state": 2,
        }})
        return int(result["id"])

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        result = self._request("GET", f"Ticket/{ticket_id}")
        if not isinstance(result, dict):
            raise RuntimeError("GLPI returned an invalid ticket payload")
        return result

    def status(self) -> dict[str, Any]:
        token = self._session()
        headers = {**self._base_headers(), "Session-Token": token}
        try:
            response = httpx.get(f"{self.url}/getFullSession", headers=headers, timeout=10)
            response.raise_for_status()
            session = response.json()
            return {"status": "up", "version": self._version_from_session(session)}
        finally:
            httpx.get(f"{self.url}/killSession", headers=headers, timeout=10)

    @staticmethod
    def _version_from_session(session: dict[str, Any]) -> str | None:
        values = session.get("session", session)
        for key in ("glpi_version", "version", "GLPI_VERSION"):
            if values.get(key):
                return str(values[key])
        return None

    def capabilities(self) -> dict[str, Any]:
        """Discover the GLPI features used by Nexora without assuming a plugin set."""
        token = self._session()
        headers = {**self._base_headers(), "Session-Token": token}
        manifest: dict[str, Any] = {
            "integration": "glpi",
            "auth_mode": "user_token" if self.user_token else "basic",
            "resources": {},
            "plugins": [],
        }
        try:
            session_response = httpx.get(f"{self.url}/getFullSession", headers=headers, timeout=10)
            session_response.raise_for_status()
            manifest["version"] = self._version_from_session(session_response.json())
            for resource in ("Ticket", "ITILFollowup", "User", "Entity", "SLA"):
                response = httpx.get(f"{self.url}/listSearchOptions/{resource}", headers=headers, timeout=10)
                manifest["resources"][resource] = {
                    "available": response.status_code < 400,
                    "status_code": response.status_code,
                }
            plugin_response = httpx.get(f"{self.url}/Plugin", headers=headers, timeout=10)
            if plugin_response.status_code < 400:
                plugins = plugin_response.json()
                if isinstance(plugins, list):
                    manifest["plugins"] = [
                        {"id": item.get("id"), "name": item.get("name"), "state": item.get("state")}
                        for item in plugins
                    ]
            manifest["supports"] = {
                "tickets": manifest["resources"]["Ticket"]["available"],
                "followups": manifest["resources"]["ITILFollowup"]["available"],
                "entities": manifest["resources"]["Entity"]["available"],
                "sla": manifest["resources"]["SLA"]["available"],
            }
            return manifest
        finally:
            httpx.get(f"{self.url}/killSession", headers=headers, timeout=10)
