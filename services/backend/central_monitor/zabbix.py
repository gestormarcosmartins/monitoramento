from typing import Any

import httpx

from .settings import get_settings


class ZabbixClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = settings.zabbix_api_url
        self.token = settings.zabbix_api_token

    def call(self, method: str, params: dict[str, Any]) -> Any:
        if not self.token:
            raise RuntimeError("Zabbix API token is not configured")
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        response = httpx.post(
            self.url,
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            raise RuntimeError(result["error"].get("data") or result["error"].get("message"))
        return result["result"]

    def status(self) -> dict[str, Any]:
        hosts = self.call("host.get", {"countOutput": True})
        return {"status": "up", "hosts": int(hosts)}

