from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .command_bus import canonical_payload_hash
from .db import tenant_connection
from .operations import authorize

router = APIRouter(prefix="/api/v1", tags=["reconciliation"])


class ObservedChild(BaseModel):
    kind: str = Field(pattern="^(followup|task)$")
    external_id: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any] = Field(default_factory=dict)


class ReconciliationSnapshot(BaseModel):
    resource: str = Field(pattern="^ticket_children$")
    parent_external_id: str = Field(min_length=1, max_length=300)
    cursor: str | None = Field(default=None, max_length=1000)
    children: list[ObservedChild] = Field(default_factory=list, max_length=10000)


def reconciliation_findings(
    local: Iterable[tuple[str, str]], observed: Iterable[tuple[str, str]]
) -> list[dict[str, str]]:
    local_set = set(local)
    observed_set = set(observed)
    findings = [
        {"child_kind": kind, "external_id": external_id, "finding_type": "import_required"}
        for kind, external_id in sorted(observed_set - local_set)
    ]
    findings.extend(
        {"child_kind": kind, "external_id": external_id, "finding_type": "remote_missing_requires_review"}
        for kind, external_id in sorted(local_set - observed_set)
    )
    return findings


@router.post("/integrations/{connection_id}/reconciliation-runs", status_code=201)
def reconcile_snapshot(
    connection_id: str,
    payload: ReconciliationSnapshot,
    tenant_id: str = Depends(tenant_header),
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    actor = authorize(identity, tenant_id, ("admin", "manager", "operator"))
    snapshot = payload.model_dump(mode="json")
    snapshot_hash = canonical_payload_hash(snapshot)
    with tenant_connection(tenant_id) as connection:
        integration = connection.execute(
            "SELECT id FROM cm_integration_connections WHERE tenant_id=%s AND id=%s AND active",
            (tenant_id, connection_id),
        ).fetchone()
        if not integration:
            raise HTTPException(404, "Active integration connection not found")
        parent = connection.execute(
            "SELECT id FROM cm_incidents WHERE tenant_id=%s AND glpi_ticket_id::text=%s AND inactive_at IS NULL",
            (tenant_id, payload.parent_external_id),
        ).fetchone()
        if not parent:
            raise HTTPException(404, "Parent ticket mapping not found")
        previous = connection.execute(
            "SELECT cursor FROM cm_reconciliation_cursors WHERE tenant_id=%s AND connection_id=%s AND resource=%s",
            (tenant_id, connection_id, payload.resource),
        ).fetchone()
        existing = connection.execute(
            "SELECT * FROM cm_reconciliation_runs WHERE tenant_id=%s AND connection_id=%s AND resource=%s AND parent_external_id=%s AND snapshot_hash=%s",
            (tenant_id, connection_id, payload.resource, payload.parent_external_id, snapshot_hash),
        ).fetchone()
        if existing:
            findings = list(connection.execute(
                "SELECT child_kind,external_id,finding_type,state,details,detected_at FROM cm_reconciliation_findings WHERE tenant_id=%s AND run_id=%s ORDER BY id",
                (tenant_id, existing["id"]),
            ).fetchall())
            return {"run": existing, "findings": findings, "duplicate": True}

        local_rows = list(connection.execute(
            """SELECT 'followup' AS kind,external_id FROM cm_incident_comments
               WHERE tenant_id=%s AND incident_id=%s AND external_id IS NOT NULL
               UNION ALL
               SELECT 'task' AS kind,external_id FROM cm_ticket_tasks
               WHERE tenant_id=%s AND incident_id=%s AND external_id IS NOT NULL""",
            (tenant_id, parent["id"], tenant_id, parent["id"]),
        ).fetchall())
        observed_pairs = [(child.kind, child.external_id) for child in payload.children]
        findings = reconciliation_findings(
            ((row["kind"], row["external_id"]) for row in local_rows), observed_pairs
        )
        run = connection.execute(
            """INSERT INTO cm_reconciliation_runs
               (tenant_id,connection_id,resource,parent_external_id,cursor_before,cursor_after,snapshot_hash,
                state,observed_count,finding_count,requested_by,completed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s,%s,%s,now()) RETURNING *""",
            (tenant_id, connection_id, payload.resource, payload.parent_external_id,
             previous["cursor"] if previous else None, payload.cursor, snapshot_hash,
             len(payload.children), len(findings), actor["id"]),
        ).fetchone()
        child_payloads = {(child.kind, child.external_id): child.payload for child in payload.children}
        for finding in findings:
            details = child_payloads.get((finding["child_kind"], finding["external_id"]), {})
            connection.execute(
                """INSERT INTO cm_reconciliation_findings
                   (tenant_id,run_id,child_kind,external_id,finding_type,details)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (tenant_id, run["id"], finding["child_kind"], finding["external_id"],
                 finding["finding_type"], Jsonb(details)),
            )
        connection.execute(
            """INSERT INTO cm_reconciliation_cursors
               (tenant_id,connection_id,resource,cursor,last_polled_at,last_success_at,last_error)
               VALUES (%s,%s,%s,%s,now(),now(),NULL)
               ON CONFLICT(tenant_id,connection_id,resource) DO UPDATE
               SET cursor=excluded.cursor,last_polled_at=now(),last_success_at=now(),last_error=NULL,updated_at=now()""",
            (tenant_id, connection_id, payload.resource, payload.cursor),
        )
        connection.execute(
            "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'integration.reconciled','reconciliation_run',%s,%s)",
            (tenant_id, actor["id"], str(run["id"]), Jsonb({"parent_external_id": payload.parent_external_id, "finding_count": len(findings)})),
        )
        rows = list(connection.execute(
            "SELECT child_kind,external_id,finding_type,state,details,detected_at FROM cm_reconciliation_findings WHERE tenant_id=%s AND run_id=%s ORDER BY id",
            (tenant_id, run["id"]),
        ).fetchall())
        return {"run": run, "findings": rows, "duplicate": False}


@router.get("/integrations/{connection_id}/reconciliation-runs")
def reconciliation_runs(
    connection_id: str,
    tenant_id: str = Depends(tenant_header),
    identity: Identity = Depends(current_identity),
) -> list[dict[str, Any]]:
    authorize(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute(
            """SELECT r.*,c.cursor,c.last_success_at,c.last_error
               FROM cm_reconciliation_runs r
               LEFT JOIN cm_reconciliation_cursors c ON c.tenant_id=r.tenant_id
                 AND c.connection_id=r.connection_id AND c.resource=r.resource
               WHERE r.tenant_id=%s AND r.connection_id=%s ORDER BY r.started_at DESC LIMIT 100""",
            (tenant_id, connection_id),
        ).fetchall())
