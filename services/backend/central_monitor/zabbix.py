from typing import Any

import httpx

from .settings import get_settings


class ZabbixClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = settings.zabbix_api_url
        self.token = settings.zabbix_api_token
        self.username = settings.zabbix_username
        self.password = settings.zabbix_password

    def _login(self) -> str:
        payload = {"jsonrpc":"2.0","method":"user.login","params":{"username":self.username,"password":self.password},"id":1}
        response=httpx.post(self.url,json=payload,timeout=10); response.raise_for_status(); body=response.json()
        if "error" in body: raise RuntimeError(body["error"].get("data") or body["error"].get("message"))
        return body["result"]

    def call(self, method: str, params: dict[str, Any]) -> Any:
        token = self.token or self._login()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        response = httpx.post(
            self.url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
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

    def problems(self) -> list[dict[str, Any]]:
        return self.call("problem.get", {"output":"extend","selectTags":"extend","recent":True,"sortfield":["eventid"],"sortorder":"DESC","limit":100})

    def provision_http_monitor(self, asset_id: str, asset_name: str, monitor_id: str, monitor_name: str, url: str) -> dict[str, str]:
        groups=self.call("hostgroup.get",{"filter":{"name":["Nexora"]}})
        group_id=groups[0]["groupid"] if groups else self.call("hostgroup.create",{"name":"Nexora"})["groupids"][0]
        host_key=f"nexora-{asset_id}"
        hosts=self.call("host.get",{"filter":{"host":[host_key]}})
        host_id=hosts[0]["hostid"] if hosts else self.call("host.create",{"host":host_key,"name":asset_name,"groups":[{"groupid":group_id}]})["hostids"][0]
        key=f"web.page.get[{monitor_id}]"
        items=self.call("item.get",{"hostids":[host_id],"filter":{"key_":[key]}})
        item_id=items[0]["itemid"] if items else self.call("item.create",{"hostid":host_id,"name":monitor_name,"key_":key,"type":19,"value_type":4,"url":url,"delay":"60s"})["itemids"][0]
        return {"host_id":str(host_id),"item_id":str(item_id)}

