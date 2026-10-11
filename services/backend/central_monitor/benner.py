import hmac
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["benner-sql"])


def _agent_authorized(token: str) -> None:
    configured = get_settings().benner_ingest_token
    if not configured:
        raise HTTPException(status_code=503, detail="Benner ingestion token is not configured")
    if not hmac.compare_digest(token, configured):
        raise HTTPException(status_code=401, detail="Invalid agent token")


def _check_data(item: dict[str, Any]) -> tuple[int, str, str, str, str]:
    check = item.get("check") or {}
    number = int(check.get("id") or item.get("check_id") or 0)
    name = str(check.get("name") or item.get("name") or f"Diagnóstico {number}")
    category = str(check.get("category") or item.get("category") or "SQL Server")
    state = str(item.get("status") or item.get("severity") or "info").lower()
    message = str(item.get("error") or check.get("description") or state)
    return number, name, category, state, message


def _is_ok_state(state: str) -> bool:
    return state in ("ok", "normal", "info", "success", "healthy", "up")


@router.post("/agents/benner/snapshots", status_code=status.HTTP_202_ACCEPTED)
def ingest_snapshot(
    payload: dict[str, Any],
    tenant_id: str = Depends(tenant_header),
    x_agent_token: str = Header(alias="X-Agent-Token"),
    activate_alerts: bool = Query(default=True),
) -> dict[str, Any]:
    _agent_authorized(x_agent_token)
    execution_id = str(payload.get("execution_id") or "")
    if not execution_id or not payload.get("timestamp"):
        raise HTTPException(status_code=422, detail="execution_id and timestamp are required")
    target = payload.get("target") or {}
    server = str(target.get("server") or "SQL Server")
    database = str(target.get("database") or "master")
    asset_name = f"Benner SQL - {server}"
    monitors: list[tuple[dict[str, Any], bool, str]] = []
    try:
        with tenant_connection(tenant_id) as connection:
            tenant = connection.execute("SELECT id FROM cm_tenants WHERE id=%s AND active", (tenant_id,)).fetchone()
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found")
            asset = connection.execute(
                """INSERT INTO cm_assets(tenant_id,name,kind,address,criticality,metadata)
                VALUES (%s,%s,'database',%s,'critical',%s)
                ON CONFLICT (tenant_id,name) DO UPDATE SET address=excluded.address,metadata=excluded.metadata,active=true
                RETURNING *""",
                (tenant_id, asset_name, server, Jsonb({"database": database, "collector": "benner-health-monitor"})),
            ).fetchone()
            snapshot = connection.execute(
                """INSERT INTO cm_benner_snapshots
                (tenant_id,asset_id,execution_id,collected_at,finished_at,duration_ms,target,summary,backup,metrics,raw_payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,received_at""",
                (tenant_id, asset["id"], execution_id, payload["timestamp"], payload.get("finished_at"),
                 payload.get("duration_ms"), Jsonb(target), Jsonb(payload.get("summary") or {}),
                 Jsonb(payload.get("backup")) if payload.get("backup") is not None else None,
                 Jsonb(payload.get("metrics") or {}), Jsonb(payload)),
            ).fetchone()
            for item in payload.get("items") or []:
                number, name, category, state, message = _check_data(item)
                if number <= 0:
                    continue
                monitor_name = f"Benner SQL {number:02d} - {name}"
                severity = {"error": "high", "critical": "disaster", "warning": "warning", "attention": "warning"}.get(state, "info")
                monitor = connection.execute(
                    """INSERT INTO cm_monitors(tenant_id,asset_id,name,monitor_type,configuration,interval_seconds,severity)
                    VALUES (%s,%s,%s,'push',%s,1800,%s)
                    ON CONFLICT (tenant_id,name) DO UPDATE SET asset_id=excluded.asset_id,configuration=excluded.configuration,
                    severity=excluded.severity,active=true RETURNING *""",
                    (tenant_id, asset["id"], monitor_name,
                     Jsonb({"source": "benner-health-monitor", "check_id": number, "category": category}), severity),
                ).fetchone()
                ok = _is_ok_state(state)
                connection.execute(
                    "UPDATE cm_monitors SET last_checked_at=%s,last_status=%s,last_message=%s WHERE id=%s",
                    (payload["timestamp"], "up" if ok else "down", message[:1000], monitor["id"]),
                )
                connection.execute(
                    """INSERT INTO cm_benner_checks
                    (tenant_id,snapshot_id,monitor_id,check_number,name,category,status,severity,duration_ms,message,result_sets)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, snapshot["id"], monitor["id"], number, name, category, state, severity,
                     item.get("duration_ms"), message, Jsonb(item.get("result_sets") or [])),
                )
                monitors.append((monitor, ok, message))
        if activate_alerts:
            from .worker import record_result
            for monitor, ok, message in monitors:
                record_result(monitor, ok, message)
        return {"status": "accepted", "snapshot_id": snapshot["id"], "checks": len(monitors), "alerts_evaluated": activate_alerts}
    except UniqueViolation:
        return {"status": "duplicate", "execution_id": execution_id}


@router.get("/benner/latest")
def latest_snapshot(
    tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)
) -> dict[str, Any]:
    from .main import authorize_tenant
    authorize_tenant(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        snapshot = connection.execute(
            "SELECT * FROM cm_benner_snapshots WHERE tenant_id=%s ORDER BY collected_at DESC LIMIT 1", (tenant_id,)
        ).fetchone()
        if not snapshot:
            return {"snapshot": None, "checks": []}
        checks = connection.execute(
            "SELECT * FROM cm_benner_checks WHERE tenant_id=%s AND snapshot_id=%s ORDER BY check_number",
            (tenant_id, snapshot["id"]),
        ).fetchall()
        return {"snapshot": snapshot, "checks": list(checks)}


@router.get("/benner/history")
def snapshot_history(
    limit: int = Query(default=50, ge=1, le=500), tenant_id: str = Depends(tenant_header),
    identity: Identity = Depends(current_identity),
) -> list[dict[str, Any]]:
    from .main import authorize_tenant
    authorize_tenant(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute(
            """SELECT id,execution_id,collected_at,finished_at,duration_ms,target,summary,backup,received_at
            FROM cm_benner_snapshots WHERE tenant_id=%s ORDER BY collected_at DESC LIMIT %s""", (tenant_id, limit)
        ).fetchall())
