"""Read-only ticket views across every company the caller may access (CD-05, NX-019 e NX-020).

Tenant isolation is enforced per connection (RLS on ``app.tenant_id``), so the consolidation is done
here, one company at a time, never with a cross-tenant query. Every company is authorized with its own
grant (role and row scope) and queried with the same filters as ``GET /api/v1/tickets``.

There is intentionally no write route under this prefix: every mutation needs one concrete company
and keeps using ``/api/v1/tickets/{id}/...`` with ``X-Tenant-ID`` set to the item's ``tenant_id``.
"""
import base64
import binascii
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.errors import QueryCanceled
from pydantic import BaseModel

from .auth import Identity, current_identity
from .db import connect, tenant_connection
from .operations import ROLES
from .ticketing import ticket_filters, ticket_summary_row

logger = logging.getLogger("central-monitor-api")
router = APIRouter(prefix="/api/v1/tickets/consolidated", tags=["tickets"])

MAX_TENANTS = 20
FANOUT_WORKERS = 4
TENANT_TIMEOUT_MS = 3000
SUMMARY_KEYS = ("total", "open", "critical", "overdue", "unassigned", "pending_sync")


class ConsolidatedTicketList(BaseModel):
    items: list[dict[str, Any]]
    page_size: int
    next_cursor: str | None
    total: int | None
    totals_by_tenant: list[dict[str, Any]]
    tenants_failed: list[dict[str, Any]]
    partial: bool
    read_only: Literal[True] = True
    data_as_of: datetime
    sync_state: Literal["projected"] = "projected"
    filters_applied: dict[str, str | bool | None]


def _ensure_user(identity: Identity) -> dict[str, Any]:
    with connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(20261011)")
        return connection.execute(
            """INSERT INTO cm_users(external_subject,email,display_name,is_master)
            VALUES (%s,%s,%s,NOT EXISTS (SELECT 1 FROM cm_users WHERE is_master))
            ON CONFLICT (external_subject) DO UPDATE SET email=excluded.email,display_name=excluded.display_name RETURNING *""",
            (identity.subject, identity.email, identity.display_name),
        ).fetchone()


def parse_tenant_ids(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    parsed: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            normalized = str(UUID(part))
        except ValueError as error:
            raise HTTPException(422, f"Invalid tenant id: {part}") from error
        if normalized not in parsed:
            parsed.append(normalized)
    if len(parsed) > MAX_TENANTS:
        raise HTTPException(422, f"At most {MAX_TENANTS} companies per request")
    return parsed or None


def accessible_tenants(identity: Identity, requested: list[str] | None) -> list[dict[str, Any]]:
    """Companies the caller may read, each with its own actor (role and row scope).

    ``requested`` only narrows the authorized set: ids without a grant are ignored, so the response
    never reveals whether a company exists.
    """
    user = _ensure_user(identity)
    if not user["active"]:
        raise HTTPException(403, "User is inactive")
    with connect() as connection:
        companies = connection.execute("SELECT id,name FROM cm_tenants WHERE active ORDER BY name").fetchall()
    candidates = [row for row in companies if requested is None or str(row["id"]) in requested]
    allowed: list[dict[str, Any]] = []
    for company in candidates:
        company_id = str(company["id"])
        if user["is_master"]:
            actor = {**user, "role": "master"}
        else:
            with tenant_connection(company_id) as connection:
                membership = connection.execute(
                    """SELECT role,row_scope,engagement_id,department_id,team_id FROM cm_access_grants
                    WHERE tenant_id=%s AND user_id=%s AND active
                      AND valid_from<=now() AND (valid_until IS NULL OR valid_until>now())
                    ORDER BY CASE role WHEN 'admin' THEN 1 WHEN 'manager' THEN 2 WHEN 'operator' THEN 3
                        WHEN 'auditor' THEN 4 WHEN 'viewer' THEN 5 ELSE 6 END LIMIT 1""",
                    (company_id, user["id"]),
                ).fetchone()
            if not membership or membership["role"] not in ROLES:
                continue
            actor = {**user, **membership}
        allowed.append({"id": company_id, "name": company["name"], "actor": actor})
    if requested is None and len(allowed) > MAX_TENANTS:
        raise HTTPException(422, f"More than {MAX_TENANTS} companies are authorized; narrow the request with tenant_ids")
    return allowed


def _filters_digest(filters: dict[str, Any], tenant_ids: list[str]) -> str:
    raw = json.dumps({"f": filters, "t": sorted(tenant_ids)}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def encode_cursor(updated_at: datetime, ticket_id: Any, digest: str) -> str:
    raw = json.dumps({"u": updated_at.isoformat(), "i": str(ticket_id), "h": digest}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str, digest: str) -> tuple[datetime, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        updated_at = datetime.fromisoformat(data["u"])
        ticket_id = str(UUID(data["i"]))
        cursor_digest = data["h"]
    except (binascii.Error, ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, "Invalid cursor") from error
    if cursor_digest != digest:
        raise HTTPException(422, "Cursor does not match the current filters or companies")
    return updated_at, ticket_id


def _bound_statement(connection: Any) -> None:
    connection.execute("SELECT set_config('statement_timeout', %s, true)", (str(TENANT_TIMEOUT_MS),))


def _failure(tenant: dict[str, Any], error: Exception) -> dict[str, Any]:
    name = type(error).__name__
    if isinstance(error, QueryCanceled):
        code, retryable = "timeout", True
    elif name in {"PoolTimeout", "PoolClosed", "OperationalError"}:
        code, retryable = "unavailable", True
    else:
        code, retryable = "error", False
    logger.warning("consolidated tickets: company %s failed (%s: %s)", tenant["id"], name, error)
    return {"tenant_id": tenant["id"], "tenant_name": tenant["name"], "code": code, "retryable": retryable}


def _fan_out(tenants: list[dict[str, Any]], work: Callable[[dict[str, Any]], Any]) -> tuple[list[tuple[dict[str, Any], Any]], list[dict[str, Any]]]:
    """Run ``work`` once per company with bounded concurrency; one company failing never fails the others."""
    def run(tenant: dict[str, Any]) -> tuple[dict[str, Any], Any, Exception | None]:
        try:
            return tenant, work(tenant), None
        except Exception as error:  # noqa: BLE001 - reported per company, never leaked to the client
            return tenant, None, error

    done: list[tuple[dict[str, Any], Any]] = []
    failed: list[dict[str, Any]] = []
    if not tenants:
        return done, failed
    with ThreadPoolExecutor(max_workers=min(FANOUT_WORKERS, len(tenants))) as pool:
        for tenant, value, error in pool.map(run, tenants):
            if error is None:
                done.append((tenant, value))
            else:
                failed.append(_failure(tenant, error))
    if not done:
        raise HTTPException(503, "Ticket sources are unavailable")
    return done, failed


@router.get("")
def list_consolidated_tickets(
    q: str | None = None,
    ticket_status: str | None = Query(None, alias="status"),
    priority: str | None = None,
    actionable: bool = False,
    team_id: UUID | None = None,
    assignee_user_id: UUID | None = None,
    mine: bool = False,
    tenant_ids: str | None = None,
    page_size: int = Query(25, ge=1, le=100),
    cursor: str | None = None,
    identity: Identity = Depends(current_identity),
) -> ConsolidatedTicketList:
    started = time.monotonic()
    tenants = accessible_tenants(identity, parse_tenant_ids(tenant_ids))
    filters = {
        "q": q, "ticket_status": ticket_status, "priority": priority, "actionable": actionable,
        "team_id": str(team_id) if team_id else None,
        "assignee_user_id": str(assignee_user_id) if assignee_user_id else None, "mine": mine,
    }
    digest = _filters_digest(filters, [tenant["id"] for tenant in tenants])
    after = decode_cursor(cursor, digest) if cursor else None
    first_page = after is None

    def page_for(tenant: dict[str, Any]) -> dict[str, Any]:
        where, params = ticket_filters(tenant["actor"], tenant["id"], **filters)
        paging_sql, paging_params = "", []
        if after:
            paging_sql = " AND (i.updated_at, i.id) < (%s::timestamptz, %s::uuid)"
            paging_params = [after[0], after[1]]
        with tenant_connection(tenant["id"]) as connection:
            _bound_statement(connection)
            total = None
            if first_page:
                total = connection.execute(f"SELECT count(*) AS total FROM cm_incidents i WHERE {where}", tuple(params)).fetchone()["total"]
            rows = list(connection.execute(
                f"""SELECT i.*,u.display_name AS requester_name,team.name AS assignee_team_name,
                assignee.display_name AS assignee_name FROM cm_incidents i
                LEFT JOIN cm_users u ON u.id=i.requester_user_id LEFT JOIN cm_teams team ON team.id=i.assignee_team_id
                LEFT JOIN cm_users assignee ON assignee.id=i.assignee_user_id WHERE {where}{paging_sql}
                ORDER BY i.updated_at DESC, i.id DESC LIMIT %s""",
                tuple(params + paging_params + [page_size + 1]),
            ).fetchall())
        return {"rows": rows, "total": total}

    results, failed = _fan_out(tenants, page_for)
    merged: list[dict[str, Any]] = []
    for tenant, value in results:
        merged.extend({**row, "tenant_name": tenant["name"]} for row in value["rows"])
    merged.sort(key=lambda row: (row["updated_at"], str(row["id"])), reverse=True)
    page = merged[:page_size]
    next_cursor = encode_cursor(page[-1]["updated_at"], page[-1]["id"], digest) if len(merged) > page_size else None
    totals = [{"tenant_id": tenant["id"], "tenant_name": tenant["name"], "total": value["total"]} for tenant, value in results] if first_page else []
    logger.info("consolidated tickets: %d companies, %d failed, %d items, %d ms", len(tenants), len(failed), len(page), (time.monotonic() - started) * 1000)
    applied: dict[str, str | bool | None] = {"q": q, "status": ticket_status, "priority": priority, "actionable": actionable}
    if team_id:
        applied["team_id"] = str(team_id)
    if assignee_user_id:
        applied["assignee_user_id"] = str(assignee_user_id)
    if mine:
        applied["mine"] = "true"
    return ConsolidatedTicketList(
        items=page, page_size=page_size, next_cursor=next_cursor,
        total=sum(item["total"] for item in totals) if first_page else None,
        totals_by_tenant=totals, tenants_failed=failed, partial=bool(failed),
        data_as_of=datetime.now(UTC), filters_applied=applied,
    )


@router.get("/summary")
def consolidated_summary(tenant_ids: str | None = None, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    tenants = accessible_tenants(identity, parse_tenant_ids(tenant_ids))

    def summary_for(tenant: dict[str, Any]) -> dict[str, Any]:
        with tenant_connection(tenant["id"]) as connection:
            _bound_statement(connection)
            return ticket_summary_row(connection, tenant["actor"], tenant["id"])

    results, failed = _fan_out(tenants, summary_for)
    totals = {key: sum(int(row[key]) for _, row in results) for key in SUMMARY_KEYS}
    return {
        "totals": totals,
        "by_tenant": [{"tenant_id": tenant["id"], "tenant_name": tenant["name"], **{key: row[key] for key in SUMMARY_KEYS}} for tenant, row in results],
        "tenants_failed": failed, "partial": bool(failed), "read_only": True, "data_as_of": datetime.now(UTC),
    }
