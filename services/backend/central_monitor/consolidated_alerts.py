"""Read-only alert list across every company the caller may access (CD-09, Central de Monitoramento).

Same model as ``consolidated_tickets``: tenant isolation is per connection (RLS), so the consolidation is
done here, one company at a time, with the caller's own grant for each. There is intentionally no write
route under this prefix. Alerts have no row scope; the ``client`` role is excluded (see the proposal doc).
"""
import base64
import binascii
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .auth import Identity, current_identity
from .consolidated_tickets import _bound_statement, _fan_out, _filters_digest, accessible_tenants, parse_tenant_ids
from .db import tenant_connection

logger = logging.getLogger("central-monitor-api")
router = APIRouter(prefix="/api/v1/alerts/consolidated", tags=["alerts"])

SEVERITIES = ("info", "warning", "average", "high", "disaster")
RANK_SQL = "CASE a.severity WHEN 'disaster' THEN 5 WHEN 'high' THEN 4 WHEN 'average' THEN 3 WHEN 'warning' THEN 2 ELSE 1 END"
GROUPS = {"disaster": "critical", "high": "critical", "average": "attention", "warning": "attention"}


def severity_group(severity: str) -> str:
    """Same grouping as the dashboard: critical = high + disaster, attention = warning + average."""
    return GROUPS.get(severity, "info")


class ConsolidatedAlertList(BaseModel):
    items: list[dict[str, Any]]
    page_size: int
    next_cursor: str | None
    total: int | None
    counts: dict[str, int] | None
    totals_by_tenant: list[dict[str, Any]]
    tenants_failed: list[dict[str, Any]]
    partial: bool
    read_only: Literal[True] = True
    data_as_of: datetime
    sync_state: Literal["projected"] = "projected"
    filters_applied: dict[str, str | None]


def parse_severities(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    parsed: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in SEVERITIES:
            raise HTTPException(422, f"Invalid severity: {part}")
        if part not in parsed:
            parsed.append(part)
    return parsed or None


def encode_cursor(sort: str, rank: int, opened_at: datetime, alert_id: Any, digest: str) -> str:
    raw = json.dumps({"k": rank, "u": opened_at.isoformat(), "i": str(alert_id), "s": sort, "h": digest}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str, sort: str, digest: str) -> tuple[int, datetime, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        rank, opened_at, alert_id = int(data["k"]), datetime.fromisoformat(data["u"]), str(UUID(data["i"]))
        cursor_sort, cursor_digest = data["s"], data["h"]
    except (binascii.Error, ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, "Invalid cursor") from error
    if cursor_sort != sort or cursor_digest != digest:
        raise HTTPException(422, "Cursor does not match the current filters, sort or companies")
    return rank, opened_at, alert_id


def _where(tenant_id: str, filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = ["a.tenant_id=%s", "a.inactive_at IS NULL"]
    params: list[Any] = [tenant_id]
    if filters["status"] != "all":
        clauses.append("a.status=%s")
        params.append(filters["status"])
    if filters["severity"]:
        clauses.append("a.severity = ANY(%s)")
        params.append(list(filters["severity"]))
    if filters["q"]:
        clauses.append("(a.title ILIKE %s OR COALESCE(a.message,'') ILIKE %s)")
        params += [f"%{filters['q']}%", f"%{filters['q']}%"]
    if filters["asset_id"]:
        clauses.append("m.asset_id=%s")
        params.append(filters["asset_id"])
    if filters["from"]:
        clauses.append("a.opened_at>=%s")
        params.append(filters["from"])
    if filters["to"]:
        clauses.append("a.opened_at<=%s")
        params.append(filters["to"])
    return " AND ".join(clauses), params


@router.get("")
def list_consolidated_alerts(
    alert_status: Literal["open", "resolved", "ignored", "all"] = Query("open", alias="status"),
    severity: str | None = None,
    q: str | None = None,
    asset_id: UUID | None = None,
    opened_from: datetime | None = Query(None, alias="from"),
    opened_to: datetime | None = Query(None, alias="to"),
    sort: Literal["severity", "recent"] = "severity",
    tenant_ids: str | None = None,
    page_size: int = Query(25, ge=1, le=100),
    cursor: str | None = None,
    identity: Identity = Depends(current_identity),
) -> ConsolidatedAlertList:
    started = time.monotonic()
    severities = parse_severities(severity)
    tenants = [tenant for tenant in accessible_tenants(identity, parse_tenant_ids(tenant_ids)) if tenant["actor"].get("role") != "client"]
    filters = {
        "status": alert_status, "severity": severities, "q": q, "asset_id": str(asset_id) if asset_id else None,
        "from": opened_from.isoformat() if opened_from else None, "to": opened_to.isoformat() if opened_to else None,
    }
    digest = _filters_digest({**filters, "sort": sort}, [tenant["id"] for tenant in tenants])
    after = decode_cursor(cursor, sort, digest) if cursor else None
    first_page = after is None
    by_severity = sort == "severity"

    def page_for(tenant: dict[str, Any]) -> dict[str, Any]:
        where, params = _where(tenant["id"], {**filters, "from": opened_from, "to": opened_to})
        paging_sql, paging_params = "", []
        if after:
            if by_severity:
                paging_sql = f" AND ({RANK_SQL}, a.opened_at, a.id) < (%s::int, %s::timestamptz, %s::uuid)"
                paging_params = [after[0], after[1], after[2]]
            else:
                paging_sql = " AND (a.opened_at, a.id) < (%s::timestamptz, %s::uuid)"
                paging_params = [after[1], after[2]]
        order_sql = f"{RANK_SQL} DESC, a.opened_at DESC, a.id DESC" if by_severity else "a.opened_at DESC, a.id DESC"
        source = "FROM cm_alerts a LEFT JOIN cm_monitors m ON m.tenant_id=a.tenant_id AND m.id=a.monitor_id"
        with tenant_connection(tenant["id"]) as connection:
            _bound_statement(connection)
            counts = None
            if first_page:
                counts = connection.execute(
                    f"""SELECT count(*) AS total,
                    count(*) FILTER (WHERE a.severity IN ('high','disaster')) AS critical,
                    count(*) FILTER (WHERE a.severity IN ('warning','average')) AS attention,
                    count(*) FILTER (WHERE a.severity NOT IN ('high','disaster','warning','average')) AS info
                    {source} WHERE {where}""",
                    tuple(params),
                ).fetchone()
            rows = list(connection.execute(
                f"""SELECT a.id,a.tenant_id,a.monitor_id,a.title,a.message,a.severity,a.status,a.opened_at,a.resolved_at,
                {RANK_SQL} AS severity_rank,m.name AS monitor_name,m.asset_id,s.name AS asset_name,inc.id AS incident_id,inc.status AS incident_status
                {source} LEFT JOIN cm_assets s ON s.tenant_id=m.tenant_id AND s.id=m.asset_id
                LEFT JOIN LATERAL (SELECT i.id,i.status FROM cm_incidents i WHERE i.tenant_id=a.tenant_id AND i.alert_id=a.id
                    AND i.inactive_at IS NULL ORDER BY i.created_at DESC LIMIT 1) inc ON true
                WHERE {where}{paging_sql} ORDER BY {order_sql} LIMIT %s""",
                tuple(params + paging_params + [page_size + 1]),
            ).fetchall())
        return {"rows": rows, "counts": counts}

    results, failed = _fan_out(tenants, page_for)
    merged: list[dict[str, Any]] = []
    for tenant, value in results:
        merged.extend({**row, "tenant_name": tenant["name"]} for row in value["rows"])
    if by_severity:
        merged.sort(key=lambda row: (row["severity_rank"], row["opened_at"], str(row["id"])), reverse=True)
    else:
        merged.sort(key=lambda row: (row["opened_at"], str(row["id"])), reverse=True)
    page = merged[:page_size]
    next_cursor = None
    if len(merged) > page_size:
        last = page[-1]
        next_cursor = encode_cursor(sort, last["severity_rank"], last["opened_at"], last["id"], digest)
    items = [{**{key: value for key, value in row.items() if key != "severity_rank"}, "severity_group": severity_group(row["severity"])} for row in page]

    totals = counts = None
    totals_by_tenant: list[dict[str, Any]] = []
    if first_page:
        totals_by_tenant = [
            {"tenant_id": tenant["id"], "tenant_name": tenant["name"], **{key: int(value["counts"][key]) for key in ("total", "critical", "attention", "info")}}
            for tenant, value in results
        ]
        totals = sum(item["total"] for item in totals_by_tenant)
        counts = {key: sum(item[key] for item in totals_by_tenant) for key in ("critical", "attention", "info")}
    logger.info("consolidated alerts: %d companies, %d failed, %d items, %d ms", len(tenants), len(failed), len(items), (time.monotonic() - started) * 1000)
    applied: dict[str, str | None] = {"status": alert_status, "sort": sort, "severity": ",".join(severities) if severities else None, "q": q,
                                       "asset_id": filters["asset_id"], "from": filters["from"], "to": filters["to"]}
    return ConsolidatedAlertList(
        items=items, page_size=page_size, next_cursor=next_cursor, total=totals, counts=counts, totals_by_tenant=totals_by_tenant,
        tenants_failed=failed, partial=bool(failed), data_as_of=datetime.now(UTC), filters_applied=applied,
    )
