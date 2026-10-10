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
        glpi_status = {"new": 1, "acknowledged": 2, "in_progress": 2, "resolved": 5, "ignored": 6}[status]
        self._request("PUT", f"Ticket/{ticket_id}", {"input": {"id": ticket_id, "status": glpi_status}})

    def add_followup(self, ticket_id: int, body: str, private: bool = False) -> int:
        result = self._request("POST", "ITILFollowup", {"input": {"itemtype": "Ticket", "items_id": ticket_id, "content": body, "is_private": int(private)}})
        return int(result["id"])

    def status(self) -> dict[str, Any]:
        token = self._session()
        headers = {**self._base_headers(), "Session-Token": token}
        try:
            response = httpx.get(f"{self.url}/getFullSession", headers=headers, timeout=10)
            response.raise_for_status()
            return {"status": "up"}
        finally:
            httpx.get(f"{self.url}/killSession", headers=headers, timeout=10)
