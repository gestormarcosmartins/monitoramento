import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize
from .settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["agents"])


def _hash(value: str) -> str:
    key = get_settings().central_secret_key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


class EnrollmentTokenCreate(BaseModel):
    description: str | None = Field(default=None, max_length=300)
    valid_hours: int | None = Field(default=24, ge=1, le=8760)


class EnrollRequest(BaseModel):
    enrollment_token: str
    name: str = Field(min_length=2, max_length=160)
    machine_id: str = Field(min_length=3, max_length=300)
    version: str | None = Field(default=None, max_length=50)
    capabilities: list[str] = Field(default_factory=list)


class HeartbeatRequest(BaseModel):
    version: str | None = Field(default=None, max_length=50)
    capabilities: list[str] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)
    heartbeat_interval_seconds: int = Field(default=30, ge=10, le=3600)
    services_interval_seconds: int = Field(default=60, ge=10, le=86400)


class AgentUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    status: Literal["online", "offline", "disabled"] = "offline"
    configuration: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    monitor_id: UUID
    ok: bool
    status: Literal["up", "down", "unknown"] | None = None
    message: str = Field(max_length=4000)
    collected_at: datetime | None = None


class SnapshotRequest(BaseModel):
    execution_id: str = Field(min_length=1, max_length=200)
    collected_at: datetime
    results: list[AgentResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentLogItem(BaseModel):
    level: Literal["debug", "info", "warning", "error", "critical"]
    message: str = Field(min_length=1, max_length=10000)
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


def agent_identity(
    x_tenant_id: str = Header(alias="X-Tenant-ID"),
    x_agent_id: str = Header(alias="X-Agent-ID"),
    x_agent_token: str = Header(alias="X-Agent-Token"),
) -> dict[str, Any]:
    with tenant_connection(x_tenant_id) as connection:
        agent = connection.execute(
            "SELECT * FROM cm_agents WHERE tenant_id=%s AND id=%s",
            (x_tenant_id, x_agent_id),
        ).fetchone()
    if not agent or agent["status"] == "disabled" or not hmac.compare_digest(agent["token_hash"], _hash(x_agent_token)):
        raise HTTPException(status_code=401, detail="Invalid agent credentials")
    return agent


@router.post("/agents/enrollment-tokens", status_code=201)
def create_enrollment_token(payload: EnrollmentTokenCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize(identity, tenant_id, ("admin", "manager"))
    secret = secrets.token_urlsafe(32)
    token = f"{tenant_id}.{secret}"
    expires_at = datetime.now(UTC) + timedelta(hours=payload.valid_hours) if payload.valid_hours is not None else None
    with tenant_connection(tenant_id) as connection:
        row = connection.execute(
            "INSERT INTO cm_agent_enrollment_tokens(tenant_id,token_hash,description,expires_at,created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id,description,expires_at,created_at",
            (tenant_id, _hash(token), payload.description, expires_at, user["id"]),
        ).fetchone()
    return {**row, "enrollment_token": token}


_SQL_SERVER_MONITORS = (
    # code, suffix, severity, interval_seconds — code must match what
    # Nexora.Agent/SqlServerCollector.cs reads from configuration->>'code'.
    ("sql-connectivity", "Conectividade SQL", "disaster", 600),
    ("database-status", "Status dos bancos", "high", 600),
    ("blocking", "Bloqueios", "high", 600),
    ("long-transactions", "Transações longas", "average", 600),
    ("sql-performance", "Desempenho (queries)", "average", 21600),
)
_HOST_MONITORS = (
    # code, suffix, severity, interval_seconds — code must match what
    # Nexora.Agent/HostCollector.cs (Operational) reads from configuration->>'code'.
    ("host-cpu", "CPU", "high", 300),
    ("host-memory", "Memória", "high", 300),
    ("host-disk", "Espaço em disco", "high", 300),
    ("host-services", "Serviços essenciais", "disaster", 300),
)


def _ensure_asset(connection: Any, tenant_id: str, agent_name: str) -> Any:
    """One environment per agent (machine), shared by its host and SQL monitors."""
    return connection.execute(
        """INSERT INTO cm_assets(tenant_id,name,kind,address,criticality,metadata)
        VALUES (%s,%s,'windows',%s,'critical',%s)
        ON CONFLICT(tenant_id,name) DO UPDATE SET active=true
        RETURNING id""",
        (tenant_id, agent_name, agent_name, Jsonb({"source": "windows-agent-direct"})),
    ).fetchone()


def _ensure_monitors(connection: Any, tenant_id: str, asset_id: Any, agent_name: str, items: tuple) -> None:
    # GET /agent/config returns every 'push' monitor for the whole tenant, not just this
    # agent's. A bare code like "host-cpu" collides across every Windows agent in the same
    # tenant, so each agent's own name is folded into the code to keep it unique
    # (Nexora.Agent's HostCollector.Operational / SqlServerCollector.OperationalAsync look
    # up "<code>:<agent name>" to match).
    for code, label, severity, interval_seconds in items:
        connection.execute(
            """INSERT INTO cm_monitors(tenant_id,asset_id,name,monitor_type,configuration,
                interval_seconds,severity,active,last_status,last_message)
            VALUES (%s,%s,%s,'push',%s,%s,%s,true,'unknown','Aguardando primeiro envio do agente.')
            ON CONFLICT(tenant_id,name) DO UPDATE SET asset_id=excluded.asset_id,monitor_type=excluded.monitor_type,
                configuration=excluded.configuration,active=true""",
            (tenant_id, asset_id, f"{agent_name} - {label}", Jsonb({"source": "windows-agent-direct", "code": f"{code}:{agent_name}"}), interval_seconds, severity),
        )


def _ensure_sql_monitors(connection: Any, tenant_id: str, agent_name: str) -> None:
    """Auto-provision the push monitors a SQL-capable agent needs to report against.

    Without a matching 'push' monitor per code, GET /api/v1/agent/config returns
    nothing and the agent has no monitor_id to attach its SQL results to — this used
    to require running a one-off SQL script by hand after every new SQL agent.
    """
    asset = _ensure_asset(connection, tenant_id, agent_name)
    _ensure_monitors(connection, tenant_id, asset["id"], agent_name, _SQL_SERVER_MONITORS)


def _ensure_host_monitors(connection: Any, tenant_id: str, agent_name: str) -> None:
    """Auto-provision the push monitors every Windows agent needs for CPU/memory/disk/services."""
    asset = _ensure_asset(connection, tenant_id, agent_name)
    _ensure_monitors(connection, tenant_id, asset["id"], agent_name, _HOST_MONITORS)


@router.post("/agent/enroll", status_code=201)
def enroll(payload: EnrollRequest) -> dict[str, Any]:
    try:
        tenant_id = str(UUID(payload.enrollment_token.split(".", 1)[0]))
    except (ValueError, IndexError) as error:
        raise HTTPException(401, "Invalid enrollment token") from error
    now = datetime.now(UTC)
    agent_token = secrets.token_urlsafe(48)
    with tenant_connection(tenant_id) as connection:
        enrollment = connection.execute(
            """UPDATE cm_agent_enrollment_tokens SET used_at=%s
            WHERE tenant_id=%s AND token_hash=%s AND used_at IS NULL
              AND (expires_at IS NULL OR expires_at>%s) RETURNING id""",
            (now, tenant_id, _hash(payload.enrollment_token), now),
        ).fetchone()
        if not enrollment:
            raise HTTPException(401, "Enrollment token expired, invalid or already used")
        agent = connection.execute(
            """INSERT INTO cm_agents(tenant_id,name,machine_id,token_hash,version,status,capabilities,last_seen_at)
            VALUES (%s,%s,%s,%s,%s,'online',%s,%s)
            ON CONFLICT(tenant_id,machine_id) DO UPDATE SET name=excluded.name,token_hash=excluded.token_hash,
            version=excluded.version,status='online',capabilities=excluded.capabilities,last_seen_at=excluded.last_seen_at,updated_at=now()
            RETURNING id,tenant_id,name,machine_id,configuration""",
            (tenant_id,payload.name,payload.machine_id,_hash(agent_token),payload.version,Jsonb(payload.capabilities),now),
        ).fetchone()
        if "windows-services" in payload.capabilities:
            _ensure_host_monitors(connection, tenant_id, payload.name)
        if "sql-server-direct" in payload.capabilities:
            _ensure_sql_monitors(connection, tenant_id, payload.name)
    agent_id = agent.pop("id")
    return {**agent, "agent_id": agent_id, "agent_token": agent_token, "server_url": get_settings().central_public_url}


@router.get("/agents")
def list_agents(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize(identity, tenant_id, ("admin", "manager", "operator", "auditor"))
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute("""SELECT id,name,machine_id,version,
            CASE WHEN status='disabled' THEN status
                 WHEN last_seen_at < now() - make_interval(secs => heartbeat_interval_seconds * 3 + heartbeat_grace_seconds) THEN 'offline'
                 ELSE status END AS status,
            heartbeat_interval_seconds,services_interval_seconds,heartbeat_grace_seconds,
            capabilities,configuration,last_health,last_seen_at,created_at FROM cm_agents WHERE tenant_id=%s ORDER BY name""",(tenant_id,)).fetchall())


@router.get("/agents/{agent_id}/logs")
def list_agent_logs(agent_id: UUID, limit: int = 100, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize(identity, tenant_id, ("admin", "manager", "operator", "auditor"))
    with tenant_connection(tenant_id) as connection:
        exists = connection.execute("SELECT id FROM cm_agents WHERE tenant_id=%s AND id=%s", (tenant_id, agent_id)).fetchone()
        if not exists:
            raise HTTPException(404, "Agent not found")
        return list(connection.execute(
            "SELECT id,level,message,details,occurred_at,received_at FROM cm_agent_logs WHERE tenant_id=%s AND agent_id=%s ORDER BY occurred_at DESC LIMIT %s",
            (tenant_id, agent_id, max(1, min(limit, 500))),
        ).fetchall())


@router.put("/agents/{agent_id}")
def update_agent(agent_id: UUID, payload: AgentUpdate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize(identity, tenant_id, ("admin", "manager"))
    with tenant_connection(tenant_id) as connection:
        row = connection.execute(
            """UPDATE cm_agents SET name=%s,status=%s,configuration=%s,updated_at=now()
            WHERE tenant_id=%s AND id=%s
            RETURNING id,name,machine_id,version,status,capabilities,configuration,last_seen_at,created_at""",
            (payload.name, payload.status, Jsonb(payload.configuration), tenant_id, agent_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    return row


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    actor=authorize(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        row = connection.execute("UPDATE cm_agents SET status='disabled',updated_at=now() WHERE tenant_id=%s AND id=%s AND status<>'disabled' RETURNING id", (tenant_id, agent_id)).fetchone()
        if row:
            connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','agent',%s)",(tenant_id,actor["id"],str(agent_id)))
    if not row:
        raise HTTPException(404, "Agent not found")


@router.post("/agent/heartbeat")
def heartbeat(payload: HeartbeatRequest, agent: dict[str, Any] = Depends(agent_identity)) -> dict[str, Any]:
    tenant_id = str(agent["tenant_id"])
    with tenant_connection(tenant_id) as connection:
        connection.execute("UPDATE cm_agents SET status='online',version=COALESCE(%s,version),capabilities=%s,last_health=%s,heartbeat_interval_seconds=%s,services_interval_seconds=%s,last_seen_at=now(),updated_at=now() WHERE tenant_id=%s AND id=%s",(payload.version,Jsonb(payload.capabilities),Jsonb(payload.health),payload.heartbeat_interval_seconds,payload.services_interval_seconds,tenant_id,agent["id"]))
    return {"status":"ok","server_time":datetime.now(UTC),"next_heartbeat_seconds":60}


@router.get("/agent/config")
def agent_config(agent: dict[str, Any] = Depends(agent_identity)) -> dict[str, Any]:
    tenant_id = str(agent["tenant_id"])
    with tenant_connection(tenant_id) as connection:
        monitors = list(connection.execute("""SELECT id,name,monitor_type,configuration,interval_seconds,severity
            FROM cm_monitors WHERE tenant_id=%s AND active AND monitor_type='push' ORDER BY name""",(tenant_id,)).fetchall())
    return {"agent_id":agent["id"],"configuration":agent["configuration"],"monitors":monitors,"server_url":get_settings().central_public_url}


@router.post("/agent/snapshots", status_code=202)
def receive_snapshot(payload: SnapshotRequest, agent: dict[str, Any] = Depends(agent_identity)) -> dict[str, Any]:
    tenant_id = str(agent["tenant_id"])
    try:
        with tenant_connection(tenant_id) as connection:
            connection.execute("INSERT INTO cm_agent_snapshots(tenant_id,agent_id,execution_id,collected_at,payload) VALUES (%s,%s,%s,%s,%s)",(tenant_id,agent["id"],payload.execution_id,payload.collected_at,Jsonb(payload.model_dump(mode="json"))))
            monitors = {str(row["id"]):row for row in connection.execute("SELECT * FROM cm_monitors WHERE tenant_id=%s AND id=ANY(%s)",(tenant_id,[result.monitor_id for result in payload.results])).fetchall()}
        from .worker import record_result
        for result in payload.results:
            monitor = monitors.get(str(result.monitor_id))
            if not monitor:
                continue
            if result.status == "unknown":
                with tenant_connection(tenant_id) as connection:
                    connection.execute(
                        """UPDATE cm_monitors SET last_checked_at=%s,last_status='unknown',last_message=%s
                        WHERE tenant_id=%s AND id=%s""",
                        (result.collected_at or payload.collected_at, result.message, tenant_id, result.monitor_id),
                    )
            else:
                record_result(monitor, result.status == "up" if result.status else result.ok, result.message)
    except UniqueViolation:
        return {"status":"duplicate","execution_id":payload.execution_id}
    return {"status":"accepted","execution_id":payload.execution_id,"results":len(payload.results)}


@router.post("/agent/events", status_code=202)
def receive_events(results: list[AgentResult], agent: dict[str, Any] = Depends(agent_identity)) -> dict[str, Any]:
    payload = SnapshotRequest(execution_id=f"event-{secrets.token_hex(12)}",collected_at=datetime.now(UTC),results=results)
    return receive_snapshot(payload,agent)


@router.post("/agent/logs", status_code=202)
def receive_logs(items: list[AgentLogItem], agent: dict[str, Any] = Depends(agent_identity)) -> dict[str, Any]:
    tenant_id = str(agent["tenant_id"])
    with tenant_connection(tenant_id) as connection:
        for item in items[:500]:
            connection.execute("INSERT INTO cm_agent_logs(tenant_id,agent_id,level,message,details,occurred_at) VALUES (%s,%s,%s,%s,%s,%s)",(tenant_id,agent["id"],item.level,item.message,Jsonb(item.details),item.occurred_at))
    return {"status":"accepted","logs":min(len(items),500)}
