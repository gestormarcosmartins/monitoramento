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
        token = response.json()["session_token"]
        # Company tickets live in per-company child entities: the session must see all of them.
        try:
            httpx.post(f"{self.url}/changeActiveEntities", headers={**self._base_headers(), "Session-Token": token},
                       json={"entities_id": "all", "is_recursive": True}, timeout=15)
        except httpx.HTTPError:
            pass
        return token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 params: dict[str, Any] | None = None) -> Any:
        token = self._session()
        headers = {**self._base_headers(), "Session-Token": token}
        try:
            response = httpx.request(method, f"{self.url}/{path.lstrip('/')}", headers=headers, json=payload,
                                     params=params, timeout=15)
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
        ticket = {"name": incident["title"], "content": content, "status": 1, "urgency": priority, "impact": priority, "priority": priority}
        if incident.get("glpi_entity_id"):
            ticket["entities_id"] = int(incident["glpi_entity_id"])
        result = self._request("POST", "Ticket", {"input": ticket})
        return int(result["id"])

    def find_entity(self, tenant_id: str, name: str) -> int | None:
        """Entity previously created for this company: matched by the Nexora tenant id kept in the comment."""
        found = self._request("GET", "Entity", None, {"searchText[name]": name.split(" (")[0], "range": "0-200"})
        for item in found if isinstance(found, list) else []:
            if f"nexora-tenant:{tenant_id}" in (item.get("comment") or ""):
                return int(item["id"])
        return None

    def create_entity(self, tenant_id: str, name: str) -> int:
        existing = self._request("GET", "Entity", None, {"searchText[name]": name, "range": "0-200"})
        taken = any(item.get("name") == name for item in existing) if isinstance(existing, list) else False
        # GLPI entity names are unique under their parent: disambiguate instead of failing.
        final_name = f"{name} ({tenant_id[:8]})" if taken else name
        result = self._request("POST", "Entity", {"input": {
            "name": final_name, "entities_id": 0,
            "comment": f"Empresa do Nexora. nexora-tenant:{tenant_id}"}})
        return int(result["id"])

    BUSINESS_CATEGORY = "Negócio"
    _category_cache: dict[str, int] = {}

    def ensure_category(self, name: str = BUSINESS_CATEGORY) -> int:
        """Return the id of the ITIL category, creating it once if GLPI does not have it."""
        if name in self._category_cache:
            return self._category_cache[name]
        found = self._request("GET", "ITILCategory", None, {"searchText[name]": name, "range": "0-200"})
        for item in found if isinstance(found, list) else []:
            if item.get("name") == name:
                self._category_cache[name] = int(item["id"])
                return int(item["id"])
        result = self._request("POST", "ITILCategory", {"input": {
            "name": name, "entities_id": 0, "is_recursive": 1,  # root + recursive: usable by every company entity
            "is_helpdeskvisible": 1, "is_incident": 1, "is_request": 1,
            "comment": "Chamados originados pelo Nexora Business Monitor (processos e indicadores de negócio).",
        }})
        self._category_cache[name] = int(result["id"])
        return int(result["id"])

    def find_ticket_by_marker(self, marker: str) -> int | None:
        """Idempotency: a retry after a lost response must not open a second ticket."""
        found = self._request("GET", "search/Ticket", None, {
            "criteria[0][field]": 1, "criteria[0][searchtype]": "contains", "criteria[0][value]": marker,
            "forcedisplay[0]": 2, "range": "0-1"})
        rows = found.get("data") if isinstance(found, dict) else None
        return int(rows[0]["2"]) if rows else None

    def create_business_ticket(self, occurrence: dict[str, Any]) -> int:
        marker = occurrence["marker"]
        existing = self.find_ticket_by_marker(marker)
        if existing:
            return existing
        level = 5 if occurrence["severity"] == "critico" else 3
        content = (
            "Ocorrência de negócio aberta automaticamente pelo Nexora Business Monitor.\n"
            f"Empresa: {occurrence['tenant_name']}\n"
            f"Indicador: {occurrence['indicator_name']} ({occurrence['indicator_code']})\n"
            f"Severidade: {occurrence['severity']}\n"
            f"Ocorrência Nexora: {occurrence['id']}\n"
            "Valores e detalhes do negócio permanecem no Nexora, visíveis apenas a usuários autorizados."
        )
        ticket = {
            "name": f"[Negócio] {occurrence['indicator_name']} - {marker}", "content": content, "type": 1,
            "status": 1, "urgency": level, "impact": level, "priority": level,
            "itilcategories_id": self.ensure_category(),
        }
        if occurrence.get("glpi_entity_id"):
            ticket["entities_id"] = int(occurrence["glpi_entity_id"])
        result = self._request("POST", "Ticket", {"input": ticket})
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
