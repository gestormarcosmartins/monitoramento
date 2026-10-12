"""Read-only environment (cm_assets) and service (cm_monitors) lists across the companies the caller may access (CD-09).

Same model as ``consolidated_tickets``/``consolidated_alerts``: RLS is per connection, so the consolidation is done
here, one company at a time, with the caller's own grant for each. Unlike ``GET /assets`` and ``GET /monitors`` these
routes use a MINIMAL projection: ``configuration``, ``metadata`` and ``address`` are never selected or returned.
The inventories are small, so they paginate by page; the state of each item is computed here by pure functions.
There is intentionally no write route under these prefixes.
"""
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from uuid import UUID

from .auth import Identity, current_identity
from .consolidated_tickets import _bound_statement, _fan_out, accessible_tenants, parse_tenant_ids
from .db import tenant_connection

logger = logging.getLogger("central-monitor-api")
assets_router = APIRouter(prefix="/api/v1/assets/consolidated", tags=["assets"])
monitors_router = APIRouter(prefix="/api/v1/monitors/consolidated", tags=["monitors"])

MAX_ROWS_PER_TENANT = 1000
STALE_FACTOR = 3
STALE_MIN_SECONDS = 180
LAST_MESSAGE_LIMIT = 200
ENVIRONMENT_STATUSES = ("healthy", "attention", "critical", "unavailable", "unknown", "inactive")
SERVICE_STATES = ("healthy", "unavailable", "unknown", "inactive")
ASSET_KINDS = ("linux", "windows", "database", "network", "website", "api", "service", "other")
ENVIRONMENT_ORDER = {"unavailable": 0, "critical": 1, "attention": 2, "unknown": 3, "healthy": 4, "inactive": 5}
SERVICE_ORDER = {"unavailable": 0, "unknown": 1, "healthy": 2, "inactive": 3}

# ---- State policy (provisional: product decisions are open, see docs/NEXORA_CONSOLIDATED_INVENTORY_PROPOSAL.md)


def is_stale(last_checked_at: datetime | None, interval_seconds: int | None, now: datetime) -> bool:
    """Telemetry is stale when it never ran or is older than max(3 x interval, 180 s)."""
    if last_checked_at is None:
        return True
    window = max(STALE_FACTOR * int(interval_seconds or 60), STALE_MIN_SECONDS)
    return (now - last_checked_at).total_seconds() > window


def service_state(row: dict[str, Any], now: datetime) -> str:
    """inactive > unknown (stale or no result) > unavailable > healthy. Unknown or stale NEVER counts as healthy."""
    if not row["active"]:
        return "inactive"
    if is_stale(row.get("last_checked_at"), row.get("interval_seconds"), now) or row.get("last_status") not in ("up", "down"):
        return "unknown"
    return "unavailable" if row["last_status"] == "down" else "healthy"


def environment_status(*, active: bool, services: dict[str, int], alerts: dict[str, int]) -> str:
    """inactive > unavailable > critical > attention > unknown > healthy, from service states and OPEN alerts."""
    if not active:
        return "inactive"
    if services["down"] > 0:
        return "unavailable"
    if alerts["critical"] > 0:
        return "critical"
    if alerts["attention"] > 0:
        return "attention"
    if services["up"] + services["unknown"] == 0 or services["unknown"] > 0:
        return "unknown"
    return "healthy"


class InventoryList(BaseModel):
    items: list[dict[str, Any]]
    page: int
    page_size: int
    pages: int
    total: int
    counts: dict[str, int]
    totals_by_tenant: list[dict[str, Any]]
    tenants_failed: list[dict[str, Any]]
    tenants_truncated: list[dict[str, Any]]
    partial: bool
    read_only: Literal[True] = True
    data_as_of: datetime
    sync_state: Literal["projected"] = "projected"
    filters_applied: dict[str, str | None]


def _parse_choices(value: str | None, allowed: tuple[str, ...], label: str) -> list[str] | None:
    if value is None or not value.strip():
        return None
    parsed: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in allowed:
            raise HTTPException(422, f"Invalid {label}: {part}")
        if part not in parsed:
            parsed.append(part)
    return parsed or None


def _alert_counts(rows: list[dict[str, Any]], key: str) -> dict[Any, dict[str, int]]:
    return {row[key]: {"critical": int(row["critical"]), "attention": int(row["attention"]), "info": int(row["info"])} for row in rows}


ALERT_COUNTS_SQL = """count(*) FILTER (WHERE a.severity IN ('high','disaster')) AS critical,
    count(*) FILTER (WHERE a.severity IN ('warning','average')) AS attention,
    count(*) FILTER (WHERE a.severity NOT IN ('high','disaster','warning','average')) AS info"""
NO_ALERTS = {"critical": 0, "attention": 0, "info": 0}


def _run(identity: Identity, tenant_ids: str | None, work: Any, *, page: int, page_size: int, order_key: Any, state_key: str, states: tuple[str, ...], selected: list[str] | None, applied: dict[str, str | None]) -> InventoryList:
    started = time.monotonic()
    tenants = [tenant for tenant in accessible_tenants(identity, parse_tenant_ids(tenant_ids)) if tenant["actor"].get("role") != "client"]
    now = datetime.now(UTC)
    results, failed = _fan_out(tenants, lambda tenant: work(tenant, now))
    merged: list[dict[str, Any]] = []
    truncated: list[dict[str, Any]] = []
    for tenant, value in results:
        merged.extend({**row, "tenant_id": tenant["id"], "tenant_name": tenant["name"]} for row in value["rows"])
        if value["truncated"]:
            truncated.append({"tenant_id": tenant["id"], "tenant_name": tenant["name"]})
    if selected:
        merged = [row for row in merged if row[state_key] in selected]
    counts = {state: sum(1 for row in merged if row[state_key] == state) for state in states}
    per_tenant: dict[str, dict[str, Any]] = {tenant["id"]: {"tenant_id": tenant["id"], "tenant_name": tenant["name"], "total": 0} for tenant, _ in results}
    for row in merged:
        per_tenant[row["tenant_id"]]["total"] += 1
    merged.sort(key=order_key)
    total = len(merged)
    pages = max(1, (total + page_size - 1) // page_size)
    items = merged[(page - 1) * page_size: page * page_size]
    logger.info("consolidated inventory: %d companies, %d failed, %d of %d items, %d ms", len(tenants), len(failed), len(items), total, (time.monotonic() - started) * 1000)
    return InventoryList(items=items, page=page, page_size=page_size, pages=pages, total=total, counts=counts, totals_by_tenant=list(per_tenant.values()),
                         tenants_failed=failed, tenants_truncated=truncated, partial=bool(failed), data_as_of=now, filters_applied=applied)


@assets_router.get("")
def list_consolidated_environments(
    environment_status_filter: str | None = Query(None, alias="status"),
    kind: str | None = None,
    q: str | None = None,
    sort: Literal["status", "name"] = "status",
    tenant_ids: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    identity: Identity = Depends(current_identity),
) -> InventoryList:
    selected = _parse_choices(environment_status_filter, ENVIRONMENT_STATUSES, "status")
    kinds = _parse_choices(kind, ASSET_KINDS, "kind")

    def work(tenant: dict[str, Any], now: datetime) -> dict[str, Any]:
        clauses, params = ["a.tenant_id=%s"], [tenant["id"]]
        if kinds:
            clauses.append("a.kind = ANY(%s)")
            params.append(kinds)
        if q and q.strip():
            clauses.append("a.name ILIKE %s")
            params.append(f"%{q.strip()}%")
        with tenant_connection(tenant["id"]) as connection:
            _bound_statement(connection)
            assets = list(connection.execute(
                f"SELECT a.id,a.name,a.kind,a.criticality,a.active FROM cm_assets a WHERE {' AND '.join(clauses)} ORDER BY a.name LIMIT %s",
                tuple(params + [MAX_ROWS_PER_TENANT + 1]),
            ).fetchall())
            monitors = list(connection.execute(
                "SELECT m.asset_id,m.active,m.last_status,m.last_checked_at,m.interval_seconds FROM cm_monitors m WHERE m.tenant_id=%s",
                (tenant["id"],),
            ).fetchall())
            alerts = _alert_counts(list(connection.execute(
                f"""SELECT m.asset_id,{ALERT_COUNTS_SQL} FROM cm_alerts a JOIN cm_monitors m ON m.tenant_id=a.tenant_id AND m.id=a.monitor_id
                WHERE a.tenant_id=%s AND a.status='open' AND a.inactive_at IS NULL GROUP BY m.asset_id""",
                (tenant["id"],),
            ).fetchall()), "asset_id")
        by_asset: dict[Any, dict[str, int]] = {}
        for monitor in monitors:
            counters = by_asset.setdefault(monitor["asset_id"], {"up": 0, "down": 0, "unknown": 0, "inactive": 0})
            state = service_state(monitor, now)
            counters[{"healthy": "up", "unavailable": "down", "unknown": "unknown", "inactive": "inactive"}[state]] += 1
        rows = []
        for asset in assets[:MAX_ROWS_PER_TENANT]:
            services = by_asset.get(asset["id"], {"up": 0, "down": 0, "unknown": 0, "inactive": 0})
            asset_alerts = alerts.get(asset["id"], NO_ALERTS)
            rows.append({
                "id": asset["id"], "name": asset["name"], "kind": asset["kind"], "criticality": asset["criticality"], "active": asset["active"],
                "status": environment_status(active=asset["active"], services=services, alerts=asset_alerts),
                "services": {**services, "total": sum(services.values())}, "alerts": asset_alerts,
            })
        return {"rows": rows, "truncated": len(assets) > MAX_ROWS_PER_TENANT}

    by_status = lambda row: (ENVIRONMENT_ORDER[row["status"]], row["name"].lower(), row["tenant_name"].lower(), str(row["id"]))
    by_name = lambda row: (row["name"].lower(), row["tenant_name"].lower(), str(row["id"]))
    return _run(identity, tenant_ids, work, page=page, page_size=page_size, order_key=by_status if sort == "status" else by_name,
                state_key="status", states=ENVIRONMENT_STATUSES, selected=selected,
                applied={"status": ",".join(selected) if selected else None, "kind": ",".join(kinds) if kinds else None, "q": q, "sort": sort})


@monitors_router.get("")
def list_consolidated_services(
    state: str | None = None,
    asset_id: UUID | None = None,
    monitor_type: str | None = Query(None, max_length=40),
    q: str | None = None,
    sort: Literal["state", "name"] = "state",
    tenant_ids: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    identity: Identity = Depends(current_identity),
) -> InventoryList:
    selected = _parse_choices(state, SERVICE_STATES, "state")

    def work(tenant: dict[str, Any], now: datetime) -> dict[str, Any]:
        clauses, params = ["m.tenant_id=%s"], [tenant["id"]]
        if asset_id:
            clauses.append("m.asset_id=%s")
            params.append(str(asset_id))
        if monitor_type:
            clauses.append("m.monitor_type=%s")
            params.append(monitor_type)
        if q and q.strip():
            clauses.append("m.name ILIKE %s")
            params.append(f"%{q.strip()}%")
        with tenant_connection(tenant["id"]) as connection:
            _bound_statement(connection)
            monitors = list(connection.execute(
                f"""SELECT m.id,m.name,m.monitor_type,m.active,m.severity,m.last_status,m.last_checked_at,m.last_message,m.interval_seconds,
                m.asset_id,a.name AS asset_name,t.name AS responsible_team_name
                FROM cm_monitors m JOIN cm_assets a ON a.tenant_id=m.tenant_id AND a.id=m.asset_id LEFT JOIN cm_teams t ON t.id=m.responsible_team_id
                WHERE {' AND '.join(clauses)} ORDER BY m.name LIMIT %s""",
                tuple(params + [MAX_ROWS_PER_TENANT + 1]),
            ).fetchall())
            alerts = _alert_counts(list(connection.execute(
                f"""SELECT a.monitor_id,{ALERT_COUNTS_SQL} FROM cm_alerts a
                WHERE a.tenant_id=%s AND a.status='open' AND a.inactive_at IS NULL AND a.monitor_id IS NOT NULL GROUP BY a.monitor_id""",
                (tenant["id"],),
            ).fetchall()), "monitor_id")
        rows = []
        for monitor in monitors[:MAX_ROWS_PER_TENANT]:
            message = monitor.get("last_message")
            rows.append({
                "id": monitor["id"], "name": monitor["name"], "monitor_type": monitor["monitor_type"], "active": monitor["active"], "severity": monitor["severity"],
                "state": service_state(monitor, now), "last_status": monitor["last_status"],
                "stale": bool(monitor["active"]) and is_stale(monitor.get("last_checked_at"), monitor.get("interval_seconds"), now),
                "last_checked_at": monitor.get("last_checked_at"), "last_message": message[:LAST_MESSAGE_LIMIT] if message else None,
                "interval_seconds": monitor.get("interval_seconds"), "asset_id": monitor["asset_id"], "asset_name": monitor["asset_name"],
                "responsible_team_name": monitor.get("responsible_team_name"), "open_alerts": alerts.get(monitor["id"], NO_ALERTS),
            })
        return {"rows": rows, "truncated": len(monitors) > MAX_ROWS_PER_TENANT}

    by_state = lambda row: (SERVICE_ORDER[row["state"]], row["name"].lower(), row["tenant_name"].lower(), str(row["id"]))
    by_name = lambda row: (row["name"].lower(), row["tenant_name"].lower(), str(row["id"]))
    return _run(identity, tenant_ids, work, page=page, page_size=page_size, order_key=by_state if sort == "state" else by_name,
                state_key="state", states=SERVICE_STATES, selected=selected,
                applied={"state": ",".join(selected) if selected else None, "asset_id": str(asset_id) if asset_id else None, "monitor_type": monitor_type, "q": q, "sort": sort})
