"""Master governance views for Business query monitors and dead-letter handling.

The technical Master governs SQL (spec 6.1) but never reads business data: everything here is metadata (definitions,
states, counters, error codes, column names). Result rows stay behind `business.view` in `business_monitors.read_router`.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .business_broker import BrokerUnavailable, get_broker
from .business_monitors import _audit, _enqueue_publish, _master
from .db import tenant_connection

master_router = APIRouter(prefix="/api/v1/master/business", tags=["business-governance"])

REPROCESSABLE = ("FAILED", "TIMED_OUT", "REJECTED", "EXPIRED", "CANCELED", "PENDING")


@master_router.get("/workers")
def list_workers_for_master(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        return list(db.execute("""SELECT w.id,w.name,w.status,w.version,w.capacity,w.last_heartbeat_at,w.last_execution_at,w.created_at,w.revoked_at,
              (SELECT count(*) FROM cm_business_executions e WHERE e.tenant_id=w.tenant_id AND e.worker_id=w.id
                 AND e.state IN ('LEASED','RUNNING')) AS active_jobs,
              (SELECT count(*) FROM cm_business_executions e WHERE e.tenant_id=w.tenant_id AND e.worker_id=w.id
                 AND e.state='PENDING') AS queued_jobs,
              (SELECT count(*) FROM cm_business_data_sources s WHERE s.tenant_id=w.tenant_id AND s.worker_id=w.id AND s.active) AS sources
            FROM cm_business_workers w WHERE w.tenant_id=%s ORDER BY w.name""", (tenant_id,)).fetchall())


@master_router.get("/monitors")
def list_monitors_for_master(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        return list(db.execute("""SELECT m.id,m.name,m.description,m.status,m.interval_seconds,m.current_revision,m.published_revision,
              m.suspended_reason,m.consecutive_failures,m.updated_at,m.indicator_id,i.name AS indicator_name,
              m.source_id,s.name AS source_name,s.dialect,w.id AS worker_id,w.name AS worker_name,w.status AS worker_status,
              le.state AS last_state,le.finished_at AS last_finished_at,le.duration_ms AS last_duration_ms,le.row_count AS last_row_count,
              le.error_code AS last_error_code
            FROM cm_business_monitors m JOIN cm_business_indicators i ON i.tenant_id=m.tenant_id AND i.id=m.indicator_id
            JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id
            JOIN cm_business_workers w ON w.tenant_id=s.tenant_id AND w.id=s.worker_id
            LEFT JOIN LATERAL (SELECT e.state,e.finished_at,e.duration_ms,e.row_count,e.error_code FROM cm_business_executions e
                WHERE e.tenant_id=m.tenant_id AND e.monitor_id=m.id AND NOT e.is_test ORDER BY e.created_at DESC LIMIT 1) le ON true
            WHERE m.tenant_id=%s ORDER BY m.name LIMIT 500""", (tenant_id,)).fetchall())


@master_router.get("/monitors/{monitor_id}/revisions")
def monitor_revisions(monitor_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        monitor = db.execute("""SELECT m.id,m.name,m.description,m.status,m.interval_seconds,m.current_revision,m.published_revision,
              m.indicator_id,m.source_id,s.dialect,s.database_ref FROM cm_business_monitors m
            JOIN cm_business_data_sources s ON s.tenant_id=m.tenant_id AND s.id=m.source_id
            WHERE m.tenant_id=%s AND m.id=%s""", (tenant_id, monitor_id)).fetchone()
        if not monitor:
            raise HTTPException(404, "Monitor not found")
        revisions = list(db.execute("""SELECT r.revision,r.state,r.query_text,r.query_sha256,r.parameters,r.column_schema,r.metric,r.limits,
              r.created_at,r.tested_at,r.published_at,t.id AS test_execution_id,t.state AS test_state,t.error_code AS test_error_code,
              t.finished_at AS test_finished_at,t.row_count AS test_row_count,t.duration_ms AS test_duration_ms
            FROM cm_business_monitor_revisions r
            LEFT JOIN LATERAL (SELECT e.id,e.state,e.error_code,e.finished_at,e.row_count,e.duration_ms FROM cm_business_executions e
                WHERE e.tenant_id=r.tenant_id AND e.monitor_id=r.monitor_id AND e.revision=r.revision AND e.is_test
                ORDER BY e.created_at DESC LIMIT 1) t ON true
            WHERE r.tenant_id=%s AND r.monitor_id=%s ORDER BY r.revision DESC""", (tenant_id, monitor_id)).fetchall())
    return {**monitor, "revisions": revisions}


@master_router.get("/executions/{execution_id}")
def execution_metadata(execution_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    """State of one execution (tests included) for the Master: no rows, only counters and column names/types."""
    _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        row = db.execute("""SELECT e.id,e.monitor_id,e.revision,e.is_test,e.state,e.attempts,e.scheduled_at,e.started_at,e.finished_at,
              e.duration_ms,e.row_count,e.truncated,e.error_code,e.error_message,e.published_at,r.columns
            FROM cm_business_executions e LEFT JOIN cm_business_execution_results r ON r.tenant_id=e.tenant_id AND r.execution_id=e.id
            WHERE e.tenant_id=%s AND e.id=%s""", (tenant_id, execution_id)).fetchone()
    if not row:
        raise HTTPException(404, "Execution not found")
    return row


class DeadLetterAction(BaseModel):
    reason: str = Field(min_length=4, max_length=300)


@master_router.post("/broker/dead-letters/{execution_id}/reprocess", status_code=201)
def reprocess_dead_letter(execution_id: UUID, payload: DeadLetterAction, tenant_id: str = Depends(tenant_header),
                          identity: Identity = Depends(current_identity)):
    """Run a dead-lettered execution again as a NEW execution (the old one stays as evidence), then drop its dead letter.
    Refused when the monitor would not run it anyway (disabled, other revision) or one is already in flight."""
    actor = _master(identity, tenant_id)
    broker = get_broker()
    if broker is None:
        raise HTTPException(409, "The broker is not enabled; there is no dead-letter queue in db mode")
    with tenant_connection(tenant_id) as db:
        old = db.execute("""SELECT e.*,m.status AS monitor_status,m.published_revision,m.interval_seconds,w.status AS worker_status
            FROM cm_business_executions e JOIN cm_business_monitors m ON m.tenant_id=e.tenant_id AND m.id=e.monitor_id
            JOIN cm_business_workers w ON w.tenant_id=e.tenant_id AND w.id=e.worker_id
            WHERE e.tenant_id=%s AND e.id=%s FOR UPDATE OF e""", (tenant_id, execution_id)).fetchone()
        if not old:
            raise HTTPException(404, "Execution not found")
        if old["state"] not in REPROCESSABLE:
            raise HTTPException(409, f"An execution in state {old['state']} cannot be reprocessed")
        if old["worker_status"] in ("REVOKED", "SUSPENDED"):
            raise HTTPException(409, "The worker is revoked or suspended")
        if not old["is_test"]:
            if old["monitor_status"] != "ACTIVE" or old["published_revision"] != old["revision"]:
                raise HTTPException(409, "The monitor is not active on this revision; publish or resume it first")
            if db.execute("""SELECT 1 FROM cm_business_executions WHERE tenant_id=%s AND monitor_id=%s AND NOT is_test AND id<>%s
                AND state IN ('PENDING','LEASED','RUNNING') AND expires_at>now()""", (tenant_id, old["monitor_id"], execution_id)).fetchone():
                raise HTTPException(409, "Another execution of this monitor is already in flight")
        window = 300 if old["is_test"] else min(old["interval_seconds"], 300)
        created = db.execute("""INSERT INTO cm_business_executions(tenant_id,monitor_id,revision,worker_id,is_test,expires_at)
            VALUES (%s,%s,%s,%s,%s,now()+(%s * interval '1 second')) RETURNING id""",
                             (tenant_id, old["monitor_id"], old["revision"], old["worker_id"], old["is_test"], window)).fetchone()
        if old["state"] == "PENDING":  # never let the stale message and the new one both run
            db.execute("""UPDATE cm_business_executions SET state='CANCELED',finished_at=now(),error_code='reprocessed'
                WHERE tenant_id=%s AND id=%s""", (tenant_id, execution_id))
        _enqueue_publish(db, tenant_id, created["id"], 0)
        _audit(db, tenant_id, actor["id"], "dlq_reprocess", "business_execution", execution_id,
               {"new_execution_id": str(created["id"]), "reason": payload.reason})
    try:
        removed = broker.discard_dlq(tenant_id, str(execution_id))
    except BrokerUnavailable:
        removed = None  # the new execution is already queued; the dead letter can be discarded later
    return {"execution_id": created["id"], "state": "PENDING", "dead_letters_removed": removed}


@master_router.post("/broker/dead-letters/{execution_id}/discard")
def discard_dead_letter(execution_id: UUID, payload: DeadLetterAction, tenant_id: str = Depends(tenant_header),
                        identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    broker = get_broker()
    if broker is None:
        raise HTTPException(409, "The broker is not enabled; there is no dead-letter queue in db mode")
    try:
        removed = broker.discard_dlq(tenant_id, str(execution_id))
    except BrokerUnavailable as error:
        raise HTTPException(503, "Broker unavailable") from error
    with tenant_connection(tenant_id) as db:
        _audit(db, tenant_id, actor["id"], "dlq_discard", "business_execution", execution_id,
               {"removed": removed, "reason": payload.reason})
    return {"dead_letters_removed": removed}
