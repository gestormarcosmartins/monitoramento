from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    timezone: str = "America/Sao_Paulo"


class AssetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    kind: Literal["linux", "windows", "database", "network", "website", "api", "service", "other"]
    address: str | None = Field(default=None, max_length=500)
    criticality: Literal["low", "medium", "high", "critical"] = "medium"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MonitorCreate(BaseModel):
    asset_id: UUID
    name: str = Field(min_length=2, max_length=160)
    monitor_type: Literal["http", "tcp", "zabbix", "event_deadman"]
    configuration: dict[str, Any]
    interval_seconds: int = Field(default=60, ge=30, le=86400)
    severity: Literal["info", "warning", "average", "high", "disaster"] = "warning"

    @model_validator(mode="after")
    def validate_configuration(self) -> "MonitorCreate":
        if self.monitor_type == "http":
            url = self.configuration.get("url")
            if not url:
                raise ValueError("http monitor requires configuration.url")
            AnyHttpUrl(url)
        elif self.monitor_type == "tcp":
            if not self.configuration.get("host"):
                raise ValueError("tcp monitor requires configuration.host")
            port = self.configuration.get("port")
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("tcp monitor requires a valid configuration.port")
        elif self.monitor_type == "event_deadman":
            if int(self.configuration.get("max_silence_seconds", 0)) < 60:
                raise ValueError("event_deadman requires max_silence_seconds >= 60")
        return self


class CustomEvent(BaseModel):
    event_id: str = Field(alias="eventId", min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    asset_external_key: str | None = Field(default=None, alias="assetExternalKey")
    event_type: str = Field(alias="eventType", min_length=1, max_length=120)
    occurred_at: datetime = Field(alias="occurredAt")
    severity: Literal["info", "warning", "average", "high", "disaster"]
    attributes: dict[str, Any] = Field(default_factory=dict)


class IncidentUpdate(BaseModel):
    status: Literal["acknowledged", "in_progress", "resolved", "ignored"]
    version: int = Field(ge=1)


class ActionExecutionRequest(BaseModel):
    asset_id: UUID
    action_code: Literal["rerun_idempotent_job", "restart_stateless_worker", "collect_diagnostics"]
    idempotency_key: str = Field(min_length=16, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionGrantCreate(BaseModel):
    asset_id: UUID
    action_code: Literal["rerun_idempotent_job", "restart_stateless_worker", "collect_diagnostics"]
    enabled: bool = False
    max_per_hour: int = Field(default=1, ge=1, le=10)
    cooldown_seconds: int = Field(default=1800, ge=60, le=86400)

