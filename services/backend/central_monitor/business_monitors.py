"""Business query monitors: Master governance, central scheduler and the Business Worker gateway.

Spec "Business Monitor v1.0": only the technical Master creates/edits/tests/publishes SQL; the isolated
Business Worker claims jobs through the API (it never sees the broker or the central database), runs them
with its own read-only credential and returns a tabular JSON result. Executions give idempotency, leases
and audit; every state transition is validated, and a result is consolidated at most once per execution_id.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .business import _actor, _enabled, _require, _scope_allows
from .business_results import (ContractError, compute_metric, public_key_b64, sign_manifest, validate_columns,
                               validate_limits, validate_metric, validate_result)
from .business_sql import SqlRejected, validate_query
from .db import tenant_connection

logger = logging.getLogger(__name__)
master_router = APIRouter(prefix="/api/v1/master/business", tags=["business-master"])
read_router = APIRouter(prefix="/api/v1/business", tags=["business-monitors"])
worker_router = APIRouter(prefix="/api/v1/agents/business", tags=["business-worker"])

LEASE_SECONDS = 90
MAX_LEASE_RENEWALS = 2
MAX_ATTEMPTS = 3
SUSPEND_AFTER_FAILURES = 5
WORKER_OFFLINE_AFTER = timedelta(minutes=5)
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
TERMINAL = ("SUCCEEDED", "FAILED", "TIMED_OUT", "REJECTED", "EXPIRED", "CANCELED")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _audit(db: Any, tenant_id: str, user_id: Any, action: str, obj: str, oid: Any, details: dict | None = None) -> None:
    db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,%s,%s,%s,%s)",
               (tenant_id, user_id, action, obj, str(oid), Jsonb(details or {})))


# ------------------------------------------------------------------ authorization
def _master(identity: Identity, tenant_id: str) -> dict[str, Any]:
    """Only the technical Master governs SQL (spec 6.1). TOTP is mandatory at login by the Keycloak realm."""
    actor = _actor(identity, tenant_id)
    if not actor.get("is_master"):
        raise HTTPException(403, "Only the Master may manage Business query monitors")
    with tenant_connection(tenant_id) as db:
        if not _enabled(db, tenant_id):
            raise HTTPException(404, "Business Monitor not enabled for this company")
    return actor


def worker_identity(x_tenant_id: str = Header(alias="X-Tenant-ID"), x_worker_id: str = Header(alias="X-Worker-ID"),
                    x_worker_token: str = Header(alias="X-Worker-Token")) -> dict[str, Any]:
    if not UUID_RE.fullmatch(x_tenant_id) or not UUID_RE.fullmatch(x_worker_id):
        raise HTTPException(401, "Invalid worker credentials")
    with tenant_connection(x_tenant_id) as db:
        worker = db.execute("SELECT * FROM cm_business_workers WHERE tenant_id=%s AND id=%s", (x_tenant_id, x_worker_id)).fetchone()
        enabled = _enabled(db, x_tenant_id) if worker else False
    if (not worker or worker["status"] in ("REVOKED", "SUSPENDED")
            or not hmac.compare_digest(worker["token_hash"], _hash(x_worker_token))):
        raise HTTPException(401, "Invalid worker credentials")
    if not enabled:
        raise HTTPException(404, "Business Monitor disabled")
    return worker


# ------------------------------------------------------------------ master: workers and sources
class WorkerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    capacity: int = Field(default=1, ge=1, le=4)


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    worker_id: UUID
    dialect: Literal["sqlserver", "postgres"]
    database_ref: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")


@master_router.post("/workers", status_code=201)
def create_worker(payload: WorkerCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    token = secrets.token_urlsafe(32)
    try:
        with tenant_connection(tenant_id) as db:
            row = db.execute("""INSERT INTO cm_business_workers(tenant_id,name,token_hash,capacity,created_by)
                VALUES (%s,%s,%s,%s,%s) RETURNING id,name,status,capacity,created_at""",
                             (tenant_id, payload.name, _hash(token), payload.capacity, actor["id"])).fetchone()
            _audit(db, tenant_id, actor["id"], "create", "business_worker", row["id"])
    except UniqueViolation as error:
        raise HTTPException(409, "A worker with this name already exists") from error
    return {**row, "worker_token": token}  # shown once; only its hash is stored


@master_router.post("/workers/{worker_id}/revoke")
def revoke_worker(worker_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        row = db.execute("UPDATE cm_business_workers SET status='REVOKED',revoked_at=now() WHERE tenant_id=%s AND id=%s RETURNING id",
                         (tenant_id, worker_id)).fetchone()
        if not row:
            raise HTTPException(404, "Worker not found")
        db.execute("""UPDATE cm_business_executions SET state='CANCELED',finished_at=now(),error_code='worker_revoked'
            WHERE tenant_id=%s AND worker_id=%s AND state IN ('PENDING','LEASED')""", (tenant_id, worker_id))
        _audit(db, tenant_id, actor["id"], "revoke", "business_worker", worker_id)
    return {"status": "revoked"}


@master_router.post("/data-sources", status_code=201)
def create_source(payload: SourceCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    try:
        with tenant_connection(tenant_id) as db:
            if not db.execute("SELECT 1 FROM cm_business_workers WHERE tenant_id=%s AND id=%s AND status<>'REVOKED'",
                              (tenant_id, payload.worker_id)).fetchone():
                raise HTTPException(404, "Worker not found")
            row = db.execute("""INSERT INTO cm_business_data_sources(tenant_id,worker_id,name,dialect,database_ref,created_by)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,name,dialect,database_ref,worker_id""",
                             (tenant_id, payload.worker_id, payload.name, payload.dialect, payload.database_ref, actor["id"])).fetchone()
            _audit(db, tenant_id, actor["id"], "create", "business_data_source", row["id"])
    except UniqueViolation as error:
        raise HTTPException(409, "A data source with this name already exists") from error
    return row


@master_router.get("/data-sources")
def list_sources(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        return list(db.execute("""SELECT s.id,s.name,s.dialect,s.database_ref,s.active,s.worker_id,w.name AS worker_name,w.status AS worker_status
            FROM cm_business_data_sources s JOIN cm_business_workers w ON w.tenant_id=s.tenant_id AND w.id=s.worker_id
            WHERE s.tenant_id=%s ORDER BY s.name""", (tenant_id,)).fetchall())


# ------------------------------------------------------------------ master: monitors and revisions
class RevisionBody(BaseModel):
    query: str = Field(min_length=6, max_length=20000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    columns: list[dict[str, str]]
    metric: dict[str, str]
    limits: dict[str, int] | None = None


class MonitorCreate(RevisionBody):
    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    indicator_id: UUID
    source_id: UUID
    interval_seconds: int = Field(ge=60, le=86400)


def _prepare_revision(dialect: str, body: RevisionBody) -> dict[str, Any]:
    try:
        for value in body.parameters.values():
            if not (value is None or isinstance(value, (str, int, float, bool))) or (isinstance(value, str) and len(value) > 200):
                raise ContractError("Parameter values must be short scalars")
        validated = validate_query(body.query, dialect, body.parameters)
        columns = validate_columns(body.columns)
        return {"query": validated, "columns": columns, "metric": validate_metric(body.metric, columns),
                "limits": validate_limits(body.limits)}
    except (SqlRejected, ContractError) as error:
        raise HTTPException(422, str(error)) from error


def _insert_revision(db: Any, tenant_id: str, monitor_id: Any, revision: int, prepared: dict[str, Any],
                     parameters: dict[str, Any], actor_id: Any) -> None:
    q = prepared["query"]
    db.execute("""INSERT INTO cm_business_monitor_revisions(tenant_id,monitor_id,revision,query_text,query_sha256,parameters,
          column_schema,metric,limits,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
               (tenant_id, monitor_id, revision, q.normalized_sql, q.sha256, Jsonb(parameters), Jsonb(prepared["columns"]),
                Jsonb(prepared["metric"]), Jsonb(prepared["limits"]), actor_id))


@master_router.post("/monitors", status_code=201)
def create_monitor(payload: MonitorCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        source = db.execute("SELECT dialect FROM cm_business_data_sources WHERE tenant_id=%s AND id=%s AND active",
                            (tenant_id, payload.source_id)).fetchone()
        if not source:
            raise HTTPException(404, "Data source not found")
        if not db.execute("SELECT 1 FROM cm_business_indicators WHERE tenant_id=%s AND id=%s AND active", (tenant_id, payload.indicator_id)).fetchone():
            raise HTTPException(404, "Indicator not found")
        prepared = _prepare_revision(source["dialect"], payload)
        try:
            monitor = db.execute("""INSERT INTO cm_business_monitors(tenant_id,name,description,indicator_id,source_id,interval_seconds,
                  current_revision,created_by) VALUES (%s,%s,%s,%s,%s,%s,1,%s) RETURNING id,name,status""",
                                 (tenant_id, payload.name, payload.description, payload.indicator_id, payload.source_id,
                                  payload.interval_seconds, actor["id"])).fetchone()
        except UniqueViolation as error:
            raise HTTPException(409, "A monitor with this name already exists") from error
        _insert_revision(db, tenant_id, monitor["id"], 1, prepared, payload.parameters, actor["id"])
        _audit(db, tenant_id, actor["id"], "create", "business_monitor", monitor["id"],
               {"revision": 1, "query_sha256": prepared["query"].sha256})
    return {**monitor, "revision": 1, "revision_state": "DRAFT"}


@master_router.post("/monitors/{monitor_id}/revisions", status_code=201)
def new_revision(monitor_id: UUID, payload: RevisionBody, tenant_id: str = Depends(tenant_header),
                 identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        monitor = db.execute("""SELECT m.id,m.current_revision,s.dialect FROM cm_business_monitors m
            JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id
            WHERE m.tenant_id=%s AND m.id=%s FOR UPDATE OF m""", (tenant_id, monitor_id)).fetchone()
        if not monitor:
            raise HTTPException(404, "Monitor not found")
        prepared = _prepare_revision(monitor["dialect"], payload)
        revision = monitor["current_revision"] + 1
        _insert_revision(db, tenant_id, monitor_id, revision, prepared, payload.parameters, actor["id"])
        db.execute("UPDATE cm_business_monitors SET current_revision=%s,updated_at=now() WHERE tenant_id=%s AND id=%s",
                   (revision, tenant_id, monitor_id))
        _audit(db, tenant_id, actor["id"], "revise", "business_monitor", monitor_id,
               {"revision": revision, "query_sha256": prepared["query"].sha256})
    return {"monitor_id": monitor_id, "revision": revision, "revision_state": "DRAFT"}


@master_router.post("/monitors/{monitor_id}/revisions/{revision}/test", status_code=202)
def test_revision(monitor_id: UUID, revision: int, tenant_id: str = Depends(tenant_header),
                  identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        row = db.execute("""SELECT r.state,s.worker_id FROM cm_business_monitor_revisions r
            JOIN cm_business_monitors m ON m.tenant_id=r.tenant_id AND m.id=r.monitor_id
            JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id
            WHERE r.tenant_id=%s AND r.monitor_id=%s AND r.revision=%s""", (tenant_id, monitor_id, revision)).fetchone()
        if not row:
            raise HTTPException(404, "Revision not found")
        if row["state"] in ("SUPERSEDED", "REVOKED"):
            raise HTTPException(409, "This revision can no longer be tested")
        execution = db.execute("""INSERT INTO cm_business_executions(tenant_id,monitor_id,revision,worker_id,is_test,expires_at)
            VALUES (%s,%s,%s,%s,true,now()+interval '5 minutes') RETURNING id""",
                               (tenant_id, monitor_id, revision, row["worker_id"])).fetchone()
        _audit(db, tenant_id, actor["id"], "test", "business_monitor", monitor_id, {"revision": revision, "execution_id": str(execution["id"])})
    return {"execution_id": execution["id"], "state": "PENDING"}


@master_router.post("/monitors/{monitor_id}/revisions/{revision}/publish")
def publish_revision(monitor_id: UUID, revision: int, tenant_id: str = Depends(tenant_header),
                     identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    try:
        public_key_b64()  # fail closed when the signing key is not configured
    except RuntimeError as error:
        raise HTTPException(503, "Query signing is not configured") from error
    with tenant_connection(tenant_id) as db:
        monitor = db.execute("SELECT id,published_revision,status FROM cm_business_monitors WHERE tenant_id=%s AND id=%s FOR UPDATE",
                             (tenant_id, monitor_id)).fetchone()
        rev = db.execute("SELECT state FROM cm_business_monitor_revisions WHERE tenant_id=%s AND monitor_id=%s AND revision=%s FOR UPDATE",
                         (tenant_id, monitor_id, revision)).fetchone()
        if not monitor or not rev:
            raise HTTPException(404, "Revision not found")
        if rev["state"] != "TESTED":
            raise HTTPException(409, "Only a revision with a successful test can be published")
        if monitor["published_revision"]:
            db.execute("""UPDATE cm_business_monitor_revisions SET state='SUPERSEDED' WHERE tenant_id=%s AND monitor_id=%s AND revision=%s""",
                       (tenant_id, monitor_id, monitor["published_revision"]))
            db.execute("""UPDATE cm_business_executions SET state='CANCELED',finished_at=now(),error_code='revision_superseded'
                WHERE tenant_id=%s AND monitor_id=%s AND is_test=false AND state IN ('PENDING','LEASED')""", (tenant_id, monitor_id))
        db.execute("UPDATE cm_business_monitor_revisions SET state='PUBLISHED',published_by=%s,published_at=now() WHERE tenant_id=%s AND monitor_id=%s AND revision=%s",
                   (actor["id"], tenant_id, monitor_id, revision))
        db.execute("""UPDATE cm_business_monitors SET published_revision=%s,status='ACTIVE',consecutive_failures=0,suspended_reason=NULL,
            last_scheduled_at=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s""", (revision, tenant_id, monitor_id))
        _audit(db, tenant_id, actor["id"], "publish", "business_monitor", monitor_id, {"revision": revision})
    return {"status": "published", "revision": revision}


class StatusChange(BaseModel):
    action: Literal["suspend", "resume", "disable"]
    reason: str = Field(min_length=4, max_length=300)


@master_router.post("/monitors/{monitor_id}/status")
def change_status(monitor_id: UUID, payload: StatusChange, tenant_id: str = Depends(tenant_header),
                  identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        monitor = db.execute("SELECT status,published_revision FROM cm_business_monitors WHERE tenant_id=%s AND id=%s FOR UPDATE",
                             (tenant_id, monitor_id)).fetchone()
        if not monitor:
            raise HTTPException(404, "Monitor not found")
        if payload.action == "resume":
            if monitor["status"] not in ("SUSPENDED",) or not monitor["published_revision"]:
                raise HTTPException(409, "Only a suspended, published monitor can be resumed")
            new = "ACTIVE"
        else:
            new = "SUSPENDED" if payload.action == "suspend" else "DISABLED"
            # Revoked or disabled monitors never start new work: queued jobs are canceled, running ones finish.
            db.execute("""UPDATE cm_business_executions SET state='CANCELED',finished_at=now(),error_code='monitor_'||%s
                WHERE tenant_id=%s AND monitor_id=%s AND state IN ('PENDING','LEASED')""", (new.lower(), tenant_id, monitor_id))
        db.execute("""UPDATE cm_business_monitors SET status=%s,suspended_reason=%s,consecutive_failures=CASE WHEN %s='ACTIVE' THEN 0
            ELSE consecutive_failures END,last_scheduled_at=CASE WHEN %s='ACTIVE' THEN NULL ELSE last_scheduled_at END,updated_at=now()
            WHERE tenant_id=%s AND id=%s""", (new, None if new == "ACTIVE" else payload.reason, new, new, tenant_id, monitor_id))
        _audit(db, tenant_id, actor["id"], payload.action, "business_monitor", monitor_id, {"reason": payload.reason})
    return {"status": new}


# ------------------------------------------------------------------ read side (RBAC + company scope)
@read_router.get("/monitors")
def list_monitors(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor, scope = _require(identity, tenant_id, "business.view")
    with tenant_connection(tenant_id) as db:
        rows = db.execute("""SELECT m.id,m.name,m.status,m.interval_seconds,m.published_revision,m.current_revision,m.suspended_reason,
              m.consecutive_failures,i.owner_user_id,i.unit_id,i.department_id,i.process_id,i.name AS indicator_name,
              s.name AS source_name,w.name AS worker_name,w.status AS worker_status,w.last_heartbeat_at,
              le.state AS last_state,le.finished_at AS last_finished_at,le.duration_ms AS last_duration_ms,le.row_count AS last_row_count
            FROM cm_business_monitors m JOIN cm_business_indicators i ON i.tenant_id=m.tenant_id AND i.id=m.indicator_id
            JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id
            JOIN cm_business_workers w ON w.tenant_id=s.tenant_id AND w.id=s.worker_id
            LEFT JOIN LATERAL (SELECT e.state,e.finished_at,e.duration_ms,e.row_count FROM cm_business_executions e
                WHERE e.tenant_id=m.tenant_id AND e.monitor_id=m.id AND NOT e.is_test ORDER BY e.created_at DESC LIMIT 1) le ON true
            WHERE m.tenant_id=%s ORDER BY m.name LIMIT 2000""", (tenant_id,)).fetchall()
    visible = [r for r in rows if _scope_allows(r, actor, scope)][:500]
    for row in visible:
        for key in ("owner_user_id", "unit_id", "department_id", "process_id"):
            row.pop(key, None)  # scope helpers only; not part of the response
    return visible


def _scoped_monitor(db: Any, tenant_id: str, monitor_id: UUID, actor: dict[str, Any], scope: str) -> dict[str, Any]:
    row = db.execute("""SELECT m.id,i.owner_user_id,i.unit_id,i.department_id,i.process_id FROM cm_business_monitors m
        JOIN cm_business_indicators i ON i.tenant_id=m.tenant_id AND i.id=m.indicator_id
        WHERE m.tenant_id=%s AND m.id=%s""", (tenant_id, monitor_id)).fetchone()
    if not row or not _scope_allows(row, actor, scope):
        raise HTTPException(404, "Monitor not found")
    return row


@read_router.get("/monitors/{monitor_id}/executions")
def list_executions(monitor_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity),
                    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    actor, scope = _require(identity, tenant_id, "business.view")
    with tenant_connection(tenant_id) as db:
        _scoped_monitor(db, tenant_id, monitor_id, actor, scope)
        rows = db.execute("""SELECT id,revision,state,scheduled_at,started_at,finished_at,duration_ms,row_count,result_bytes,truncated,
              error_code,attempts,correlation_id FROM cm_business_executions
            WHERE tenant_id=%s AND monitor_id=%s AND NOT is_test ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                          (tenant_id, monitor_id, page_size, (page - 1) * page_size)).fetchall()
    return {"page": page, "page_size": page_size, "items": list(rows)}


@read_router.get("/monitors/{monitor_id}/executions/{execution_id}/result")
def execution_result(monitor_id: UUID, execution_id: UUID, tenant_id: str = Depends(tenant_header),
                     identity: Identity = Depends(current_identity)):
    actor, scope = _require(identity, tenant_id, "business.view")
    with tenant_connection(tenant_id) as db:
        _scoped_monitor(db, tenant_id, monitor_id, actor, scope)
        row = db.execute("""SELECT r.columns,r.rows,e.row_count,e.truncated,e.state FROM cm_business_execution_results r
            JOIN cm_business_executions e ON e.tenant_id=r.tenant_id AND e.id=r.execution_id
            WHERE r.tenant_id=%s AND r.execution_id=%s AND e.monitor_id=%s AND NOT e.is_test""",
                         (tenant_id, execution_id, monitor_id)).fetchone()
        if not row:
            raise HTTPException(404, "Result not found")
        _audit(db, tenant_id, actor["id"], "view_result", "business_execution", execution_id)
    return row


@read_router.get("/workers")
def list_workers(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _, scope = _require(identity, tenant_id, "business.source.manage")
    if scope != "company":
        raise HTTPException(403, "Company-wide source manager required")
    with tenant_connection(tenant_id) as db:
        return list(db.execute("""SELECT w.id,w.name,w.status,w.version,w.capacity,w.last_heartbeat_at,w.last_execution_at,
              (SELECT count(*) FROM cm_business_executions e WHERE e.tenant_id=w.tenant_id AND e.worker_id=w.id
                 AND e.state IN ('LEASED','RUNNING')) AS active_jobs,
              (SELECT count(*) FROM cm_business_executions e WHERE e.tenant_id=w.tenant_id AND e.worker_id=w.id
                 AND e.state='PENDING') AS queued_jobs
            FROM cm_business_workers w WHERE w.tenant_id=%s ORDER BY w.name""", (tenant_id,)).fetchall())


# ------------------------------------------------------------------ Business Worker gateway
class Heartbeat(BaseModel):
    version: str = Field(max_length=40)
    last_execution_id: UUID | None = None


@worker_router.post("/heartbeat")
def heartbeat(payload: Heartbeat, worker: dict[str, Any] = Depends(worker_identity)):
    with tenant_connection(str(worker["tenant_id"])) as db:
        db.execute("UPDATE cm_business_workers SET status='ONLINE',version=%s,last_heartbeat_at=now() WHERE tenant_id=%s AND id=%s",
                   (payload.version, worker["tenant_id"], worker["id"]))
    return {"status": "ok"}


@worker_router.get("/signing-key")
def signing_key(worker: dict[str, Any] = Depends(worker_identity)):
    """Public key the worker pins at install time to verify job manifests."""
    try:
        return {"algorithm": "Ed25519", "public_key": public_key_b64()}
    except RuntimeError as error:
        raise HTTPException(503, "Query signing is not configured") from error


def _resolve_parameters(spec: dict[str, Any], timezone: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    resolved = {}
    for name, value in spec.items():
        if value == "$today":
            resolved[name] = now.astimezone(ZoneInfo(timezone)).date().isoformat()
        elif value == "$now":
            resolved[name] = now.isoformat()
        else:
            resolved[name] = value
    return resolved


@worker_router.get("/jobs/claim")
def claim_jobs(limit: int = Query(1, ge=1, le=4), worker: dict[str, Any] = Depends(worker_identity)):
    tenant_id, worker_id = str(worker["tenant_id"]), worker["id"]
    jobs = []
    with tenant_connection(tenant_id) as db:
        busy = db.execute("""SELECT count(*) AS n FROM cm_business_executions WHERE tenant_id=%s AND worker_id=%s
            AND state IN ('LEASED','RUNNING') AND lease_expires_at>now()""", (tenant_id, worker_id)).fetchone()["n"]
        room = min(limit, worker["capacity"] - busy)  # the worker's own concurrency is enforced centrally too
        if room <= 0:
            return {"jobs": []}
        claimed = db.execute("""WITH due AS (
              SELECT e.id FROM cm_business_executions e
              JOIN cm_business_monitors m ON m.tenant_id=e.tenant_id AND m.id=e.monitor_id
              WHERE e.tenant_id=%s AND e.worker_id=%s AND e.expires_at>now() AND e.attempts<%s
                AND ((e.state='PENDING' AND e.available_at<=now()) OR (e.state IN ('LEASED','RUNNING') AND e.lease_expires_at<now()))
                AND (e.is_test OR (m.status='ACTIVE' AND m.published_revision=e.revision))
              ORDER BY e.scheduled_at FOR UPDATE OF e SKIP LOCKED LIMIT %s)
            UPDATE cm_business_executions x SET state='LEASED',leased_at=now(),lease_expires_at=now()+(%s * interval '1 second'),
              lease_renewals=0,attempts=x.attempts+1 FROM due WHERE x.id=due.id
            RETURNING x.id,x.monitor_id,x.revision,x.expires_at,x.lease_expires_at,x.is_test,x.correlation_id""",
                              (tenant_id, worker_id, MAX_ATTEMPTS, room, LEASE_SECONDS)).fetchall()
        for job in claimed:
            rev = db.execute("""SELECT r.query_text,r.parameters,r.column_schema,r.limits,s.dialect,s.database_ref,t.timezone
                FROM cm_business_monitor_revisions r JOIN cm_business_monitors m ON m.tenant_id=r.tenant_id AND m.id=r.monitor_id
                JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id
                JOIN cm_tenants t ON t.id=r.tenant_id
                WHERE r.tenant_id=%s AND r.monitor_id=%s AND r.revision=%s""", (tenant_id, job["monitor_id"], job["revision"])).fetchone()
            manifest = {
                "execution_id": str(job["id"]), "monitor_id": str(job["monitor_id"]), "revision": job["revision"],
                "tenant_id": tenant_id, "worker_id": str(worker_id), "expires_at": job["expires_at"].isoformat(),
                "lease_expires_at": job["lease_expires_at"].isoformat(), "database_ref": rev["database_ref"],
                "dialect": rev["dialect"], "query": rev["query_text"],
                "parameters": _resolve_parameters(rev["parameters"], rev["timezone"]),
                "constraints": rev["limits"], "column_schema": rev["column_schema"], "test": job["is_test"],
                "correlation_id": str(job["correlation_id"]),
            }
            # Signed per job: binds the query to this execution and worker, so a captured manifest cannot be replayed.
            jobs.append({**manifest, "signature": sign_manifest(manifest)})
    return {"jobs": jobs}


def _own_execution(db: Any, worker: dict[str, Any], execution_id: UUID, lock: bool = True) -> dict[str, Any]:
    row = db.execute(f"""SELECT * FROM cm_business_executions WHERE tenant_id=%s AND id=%s AND worker_id=%s
        {'FOR UPDATE' if lock else ''}""", (worker["tenant_id"], execution_id, worker["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Execution not found")  # also the answer for another tenant's/worker's id (H09)
    return row


@worker_router.post("/jobs/{execution_id}/started")
def job_started(execution_id: UUID, worker: dict[str, Any] = Depends(worker_identity)):
    with tenant_connection(str(worker["tenant_id"])) as db:
        row = _own_execution(db, worker, execution_id)
        if row["state"] not in ("LEASED", "RUNNING") or row["lease_expires_at"] < datetime.now(UTC):
            raise HTTPException(409, "Lease is not valid")
        db.execute("UPDATE cm_business_executions SET state='RUNNING',started_at=COALESCE(started_at,now()) WHERE tenant_id=%s AND id=%s",
                   (worker["tenant_id"], execution_id))
    return {"status": "running"}


@worker_router.post("/jobs/{execution_id}/renew")
def job_renew(execution_id: UUID, worker: dict[str, Any] = Depends(worker_identity)):
    with tenant_connection(str(worker["tenant_id"])) as db:
        row = _own_execution(db, worker, execution_id)
        if row["state"] not in ("LEASED", "RUNNING") or row["lease_expires_at"] < datetime.now(UTC):
            raise HTTPException(409, "Lease is not valid")
        if row["lease_renewals"] >= MAX_LEASE_RENEWALS:
            raise HTTPException(409, "Lease cannot be renewed again")  # no unlimited renewal
        db.execute("""UPDATE cm_business_executions SET lease_expires_at=now()+(%s * interval '1 second'),lease_renewals=lease_renewals+1
            WHERE tenant_id=%s AND id=%s""", (LEASE_SECONDS, worker["tenant_id"], execution_id))
    return {"status": "renewed"}


class ResultBody(BaseModel):
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    started_at: datetime
    duration_ms: int = Field(ge=0, le=3_600_000)
    columns: list[dict[str, str]]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False


class FailureBody(BaseModel):
    status: Literal["FAILED", "TIMED_OUT", "REJECTED"]
    error_code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    error_message: str = Field(default="", max_length=500)
    retryable: bool = False
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)


def _record_failure(db: Any, tenant_id: str, row: dict[str, Any], status: str, code: str, message: str,
                    retryable: bool, duration_ms: int | None) -> str:
    """Failed work either goes back to the queue (bounded attempts, backoff) or ends; repeated end states trip the breaker."""
    if retryable and row["attempts"] < MAX_ATTEMPTS and not row["is_test"] and status != "REJECTED":
        delay = min(900, 30 * 2 ** row["attempts"])
        db.execute("""UPDATE cm_business_executions SET state='PENDING',available_at=now()+(%s * interval '1 second'),lease_expires_at=NULL,
            error_code=%s,error_message=%s WHERE tenant_id=%s AND id=%s""", (delay, code, message, tenant_id, row["id"]))
        return "PENDING"
    db.execute("""UPDATE cm_business_executions SET state=%s,finished_at=now(),duration_ms=%s,error_code=%s,error_message=%s
        WHERE tenant_id=%s AND id=%s""", (status, duration_ms, code, message, tenant_id, row["id"]))
    if not row["is_test"]:
        monitor = db.execute("""UPDATE cm_business_monitors SET consecutive_failures=consecutive_failures+1 WHERE tenant_id=%s AND id=%s
            RETURNING consecutive_failures,status""", (tenant_id, row["monitor_id"])).fetchone()
        if monitor["consecutive_failures"] >= SUSPEND_AFTER_FAILURES and monitor["status"] == "ACTIVE":
            db.execute("""UPDATE cm_business_monitors SET status='SUSPENDED',suspended_reason='circuit_breaker' WHERE tenant_id=%s AND id=%s""",
                       (tenant_id, row["monitor_id"]))
            _audit(db, tenant_id, None, "circuit_breaker", "business_monitor", row["monitor_id"], {"failures": monitor["consecutive_failures"]})
    return status


@worker_router.post("/jobs/{execution_id}/failed")
def job_failed(execution_id: UUID, payload: FailureBody, worker: dict[str, Any] = Depends(worker_identity)):
    tenant_id = str(worker["tenant_id"])
    with tenant_connection(tenant_id) as db:
        row = _own_execution(db, worker, execution_id)
        if row["state"] in TERMINAL:
            return {"status": "duplicate", "state": row["state"]}  # a late or repeated report changes nothing
        if row["state"] not in ("LEASED", "RUNNING"):
            raise HTTPException(409, "Execution is not in progress")
        state = _record_failure(db, tenant_id, row, payload.status, payload.error_code, payload.error_message,
                                payload.retryable, payload.duration_ms)
        db.execute("UPDATE cm_business_workers SET last_execution_at=now() WHERE tenant_id=%s AND id=%s", (tenant_id, worker["id"]))
    return {"status": "recorded", "state": state}


def _feed_indicator(db: Any, tenant_id: str, monitor: dict[str, Any], execution_id: UUID, worker_id: Any,
                    value: Decimal, collected_at: datetime) -> None:
    """Query results enter the same series, state machine and occurrence logic as every other collector."""
    from .business_evaluation import apply_evaluation
    indicator = db.execute("SELECT * FROM cm_business_indicators WHERE tenant_id=%s AND id=%s AND active FOR UPDATE",
                           (tenant_id, monitor["indicator_id"])).fetchone()
    if not indicator:
        return
    digest = hashlib.sha256(f"{execution_id}:{value}".encode()).hexdigest()
    inserted = db.execute("""INSERT INTO cm_business_values(tenant_id,indicator_id,event_id,payload_sha256,value,collected_at,execution_id,worker_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,indicator_id,event_id) DO NOTHING RETURNING id""",
                          (tenant_id, monitor["indicator_id"], f"exec-{execution_id}", digest, value, collected_at, execution_id, worker_id)).fetchone()
    if not inserted:
        return
    state = db.execute("SELECT last_collected_at FROM cm_business_states WHERE tenant_id=%s AND indicator_id=%s FOR UPDATE",
                       (tenant_id, monitor["indicator_id"])).fetchone()
    if state and state["last_collected_at"] and collected_at <= state["last_collected_at"]:
        return  # out-of-order result: stored, never regresses the current state
    db.execute("""INSERT INTO cm_business_states(tenant_id,indicator_id,state,last_value,last_collected_at,evaluated_at)
        VALUES (%s,%s,'sem_dados',%s,%s,now()) ON CONFLICT (tenant_id,indicator_id) DO UPDATE SET
        last_value=excluded.last_value,last_collected_at=excluded.last_collected_at""",
               (tenant_id, monitor["indicator_id"], value, collected_at))
    apply_evaluation(db, tenant_id, indicator, datetime.now(UTC))


@worker_router.post("/jobs/{execution_id}/result")
def job_result(execution_id: UUID, payload: ResultBody, worker: dict[str, Any] = Depends(worker_identity)):
    tenant_id = str(worker["tenant_id"])
    with tenant_connection(tenant_id) as db:
        row = _own_execution(db, worker, execution_id)
        rev = db.execute("""SELECT column_schema,limits,metric FROM cm_business_monitor_revisions
            WHERE tenant_id=%s AND monitor_id=%s AND revision=%s""", (tenant_id, row["monitor_id"], row["revision"])).fetchone()
        try:
            result = validate_result(rev["column_schema"], rev["limits"], payload.model_dump(mode="json"))
        except ContractError as error:
            if row["state"] in ("LEASED", "RUNNING"):
                _record_failure(db, tenant_id, row, "FAILED", "contract_violation", str(error)[:300], False, payload.duration_ms)
                return {"status": "rejected", "reason": "contract_violation"}
            raise HTTPException(409, "Execution already closed") from error
        digest = hashlib.sha256(str(sorted(result.rows, key=str)).encode() + str(result.row_count).encode()).hexdigest()
        if row["state"] in TERMINAL:
            if row["state"] == "SUCCEEDED" and row["result_sha256"] == digest:
                return {"status": "duplicate"}  # same answer twice: processed once
            raise HTTPException(409, "Execution already closed with a different outcome")
        if row["state"] not in ("LEASED", "RUNNING"):
            raise HTTPException(409, "Execution is not in progress")
        finished = datetime.now(UTC)
        db.execute("""UPDATE cm_business_executions SET state='SUCCEEDED',started_at=COALESCE(started_at,%s),finished_at=%s,duration_ms=%s,
            row_count=%s,result_bytes=%s,truncated=%s,result_sha256=%s,error_code=NULL,error_message=NULL WHERE tenant_id=%s AND id=%s""",
                   (payload.started_at, finished, payload.duration_ms, result.row_count, result.size_bytes, result.truncated,
                    digest, tenant_id, execution_id))
        db.execute("INSERT INTO cm_business_execution_results(tenant_id,execution_id,columns,rows) VALUES (%s,%s,%s,%s)",
                   (tenant_id, execution_id, Jsonb(result.columns), Jsonb(result.rows)))
        db.execute("UPDATE cm_business_workers SET last_execution_at=now() WHERE tenant_id=%s AND id=%s", (tenant_id, worker["id"]))
        if row["is_test"]:
            db.execute("""UPDATE cm_business_monitor_revisions SET state=CASE WHEN state='DRAFT' THEN 'TESTED' ELSE state END,tested_at=now()
                WHERE tenant_id=%s AND monitor_id=%s AND revision=%s""", (tenant_id, row["monitor_id"], row["revision"]))
            return {"status": "accepted", "test": True}
        monitor = db.execute("SELECT id,indicator_id FROM cm_business_monitors WHERE tenant_id=%s AND id=%s FOR UPDATE",
                             (tenant_id, row["monitor_id"])).fetchone()
        db.execute("UPDATE cm_business_monitors SET consecutive_failures=0 WHERE tenant_id=%s AND id=%s", (tenant_id, row["monitor_id"]))
        value = compute_metric(rev["metric"], result.rows)
        if value is not None and abs(value) >= Decimal("1e15"):
            db.execute("UPDATE cm_business_executions SET error_code='metric_out_of_range' WHERE tenant_id=%s AND id=%s", (tenant_id, execution_id))
            value = None  # keep the stored result, but never feed an unrepresentable number to the indicator
        if value is not None:
            _feed_indicator(db, tenant_id, monitor, execution_id, worker["id"], value, finished)
    return {"status": "accepted", "indicator_updated": value is not None}


# ------------------------------------------------------------------ central scheduler (runs in the worker loop)
def run_scheduler(connect: Any) -> dict[str, int]:
    """Expire stale work and create at most one due execution per monitor. No catch-up after downtime (spec 5.2)."""
    counts = {"scheduled": 0, "expired": 0, "failed": 0, "offline": 0}
    with connect() as db:
        tenants = [str(r["id"]) for r in db.execute("SELECT id FROM cm_tenants WHERE active").fetchall()]
    for tenant_id in tenants:
        try:
            with tenant_connection(tenant_id) as db:
                if not _enabled(db, tenant_id):
                    continue
                counts["expired"] += len(db.execute("""UPDATE cm_business_executions SET state='EXPIRED',finished_at=now(),error_code='missed_window'
                    WHERE tenant_id=%s AND state='PENDING' AND expires_at<now() RETURNING id""", (tenant_id,)).fetchall())
                exhausted = db.execute("""SELECT * FROM cm_business_executions WHERE tenant_id=%s AND state IN ('LEASED','RUNNING')
                    AND lease_expires_at<now() AND attempts>=%s FOR UPDATE SKIP LOCKED""", (tenant_id, MAX_ATTEMPTS)).fetchall()
                for row in exhausted:
                    _record_failure(db, tenant_id, row, "TIMED_OUT", "lease_expired", "Worker did not report within the lease", False, None)
                    counts["failed"] += 1
                due = db.execute("""SELECT m.id,m.published_revision AS revision,s.worker_id,m.interval_seconds
                      FROM cm_business_monitors m JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id AND s.active
                      WHERE m.tenant_id=%s AND m.status='ACTIVE' AND m.published_revision IS NOT NULL
                        AND (m.last_scheduled_at IS NULL OR m.last_scheduled_at + m.interval_seconds * interval '1 second' <= now())
                        AND NOT EXISTS (SELECT 1 FROM cm_business_executions e WHERE e.tenant_id=m.tenant_id AND e.monitor_id=m.id
                          AND NOT e.is_test AND e.state IN ('PENDING','LEASED','RUNNING'))
                      ORDER BY m.last_scheduled_at NULLS FIRST LIMIT 200 FOR UPDATE OF m SKIP LOCKED""", (tenant_id,)).fetchall()
                for monitor in due:
                    window = min(monitor["interval_seconds"], 300)  # stale jobs expire instead of piling up
                    db.execute("""INSERT INTO cm_business_executions(tenant_id,monitor_id,revision,worker_id,expires_at)
                        VALUES (%s,%s,%s,%s,now()+(%s * interval '1 second'))""", (tenant_id, monitor["id"], monitor["revision"], monitor["worker_id"], window))
                    db.execute("UPDATE cm_business_monitors SET last_scheduled_at=now() WHERE tenant_id=%s AND id=%s", (tenant_id, monitor["id"]))
                    counts["scheduled"] += 1
                offline = db.execute("""UPDATE cm_business_workers SET status='OFFLINE' WHERE tenant_id=%s AND status='ONLINE'
                    AND last_heartbeat_at<now()-(%s * interval '1 second') RETURNING id""", (tenant_id, WORKER_OFFLINE_AFTER.total_seconds())).fetchall()
                for worker in offline:
                    _audit(db, tenant_id, None, "heartbeat_lost", "business_worker", worker["id"])
                    logger.error("business_worker_offline", extra={"tenant_id": tenant_id, "worker_id": str(worker["id"])})
                    counts["offline"] += 1
        except Exception:
            logger.exception("business_scheduler_tenant_failed", extra={"tenant_id": tenant_id})
    return counts
