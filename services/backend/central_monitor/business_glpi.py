"""Business Monitor -> GLPI: occurrences become tickets in the separate category "Negócio".

Rules (spec section 10, step 8, and AGENTS.md):
- opt-in per company (feature flag ``business_glpi``), managed by a company-wide source manager;
- GLPI is the authority for the ticket: an occurrence is shown as synced only after GLPI confirmed it;
- indicator recovery never resolves or closes the GLPI ticket, it only adds a private follow-up;
- ticket content carries no indicator values: those stay in Nexora behind Business permissions.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .business import _require
from .business_glpi_outbox import FEATURE, enqueue, glpi_enabled
from .db import tenant_connection
from .glpi import GlpiClient
from .glpi_entities import ensure_tenant_entity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/business", tags=["business-glpi"])
FOLLOWUP = {
    "escalated": "Severidade da ocorrência de negócio alterada para {severity}.",
    "normalized": "O indicador voltou à normalidade no Nexora. O chamado não foi encerrado automaticamente: "
                  "a decisão de resolver é de um atendente.",
    "acknowledged": "Ocorrência de negócio reconhecida no Nexora.",
    "closed": "Ocorrência de negócio encerrada no Nexora.",
}


def marker_for(occurrence_id: Any) -> str:
    return f"NXBM-{str(occurrence_id).replace('-', '')[:16]}"


def sync_occurrence(tenant_id: str, occurrence_id: str, event: str, client: GlpiClient | None = None) -> None:
    """Outbox handler. Raises on failure so the outbox retries; never reports success early."""
    with tenant_connection(tenant_id) as db:
        row = db.execute("""SELECT o.id,o.severity,o.glpi_ticket_id,i.name AS indicator_name,i.code AS indicator_code,
                t.name AS tenant_name FROM cm_business_occurrences o
            JOIN cm_business_indicators i ON i.tenant_id=o.tenant_id AND i.id=o.indicator_id
            JOIN cm_tenants t ON t.id=o.tenant_id
            WHERE o.tenant_id=%s AND o.id=%s""", (tenant_id, occurrence_id)).fetchone()
    if not row:
        return
    client = client or GlpiClient()
    try:
        ticket_id = row["glpi_ticket_id"]
        if ticket_id is None:
            ticket_id = client.create_business_ticket({
                **row, "id": occurrence_id, "marker": marker_for(occurrence_id),
                "glpi_entity_id": ensure_tenant_entity(tenant_id, client)})
            with tenant_connection(tenant_id) as db:
                db.execute("""UPDATE cm_business_occurrences SET glpi_ticket_id=%s,glpi_sync_status='synced',
                    glpi_sync_error=NULL,glpi_synced_at=now() WHERE tenant_id=%s AND id=%s""",
                           (ticket_id, tenant_id, occurrence_id))
                db.execute("""INSERT INTO cm_business_occurrence_history(tenant_id,occurrence_id,action,details)
                    VALUES (%s,%s,'glpi_ticket_created',%s)""", (tenant_id, occurrence_id, Jsonb({"glpi_ticket_id": ticket_id})))
        if event in FOLLOWUP:
            client.add_followup(int(ticket_id), FOLLOWUP[event].format(severity=row["severity"]), private=True)
    except Exception as error:
        with tenant_connection(tenant_id) as db:
            db.execute("""UPDATE cm_business_occurrences SET glpi_sync_status='failed',glpi_sync_error=%s
                WHERE tenant_id=%s AND id=%s AND glpi_ticket_id IS NULL""", (str(error)[:500], tenant_id, occurrence_id))
        raise


def reconcile_tickets(connect: Any, client: GlpiClient | None = None, limit: int = 100) -> None:
    """Mirror the GLPI status (read only). Nexora never changes the ticket or the occurrence from it."""
    with connect() as db:
        tenants = [str(r["id"]) for r in db.execute("SELECT id FROM cm_tenants WHERE active").fetchall()]
    client = client or GlpiClient()
    for tenant_id in tenants:
        with tenant_connection(tenant_id) as db:
            if not glpi_enabled(db, tenant_id):
                continue
            rows = db.execute("""SELECT id,glpi_ticket_id,glpi_status FROM cm_business_occurrences
                WHERE tenant_id=%s AND glpi_ticket_id IS NOT NULL AND glpi_sync_status='synced'
                ORDER BY glpi_synced_at NULLS FIRST LIMIT %s""", (tenant_id, limit)).fetchall()
        for row in rows:
            try:
                status = int(client.get_ticket(int(row["glpi_ticket_id"])).get("status", 0)) or None
                with tenant_connection(tenant_id) as db:
                    db.execute("""UPDATE cm_business_occurrences SET glpi_status=%s,glpi_synced_at=now()
                        WHERE tenant_id=%s AND id=%s""", (status, tenant_id, row["id"]))
            except Exception:
                logger.exception("business_glpi_reconcile_failed", extra={"tenant_id": tenant_id, "occurrence_id": str(row["id"])})


class GlpiSetting(BaseModel):
    enabled: bool
    reason: str = Field(min_length=4, max_length=500)


def _company_source_manager(identity: Identity, tenant_id: str) -> dict[str, Any]:
    actor, scope = _require(identity, tenant_id, "business.source.manage")
    if scope != "company":
        raise HTTPException(403, "Company-wide source manager required")
    return actor


@router.get("/glpi")
def glpi_status(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _company_source_manager(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        enabled = glpi_enabled(db, tenant_id)
        pending = db.execute("""SELECT count(*) FILTER (WHERE glpi_sync_status='pending') AS pending,
            count(*) FILTER (WHERE glpi_sync_status='failed') AS failed,
            count(*) FILTER (WHERE glpi_sync_status='synced') AS synced
            FROM cm_business_occurrences WHERE tenant_id=%s""", (tenant_id,)).fetchone()
    return {"enabled": enabled, "category": GlpiClient.BUSINESS_CATEGORY, **pending}


@router.put("/glpi")
def set_glpi(payload: GlpiSetting, tenant_id: str = Depends(tenant_header),
             identity: Identity = Depends(current_identity)):
    actor = _company_source_manager(identity, tenant_id)
    category_id = None
    if payload.enabled:
        # Confirm GLPI is reachable and the category exists BEFORE reporting the integration as on.
        try:
            glpi = GlpiClient()
            category_id = glpi.ensure_category()
            ensure_tenant_entity(tenant_id, glpi)  # the company's entity must exist before tickets flow
        except Exception as error:
            logger.exception("business_glpi_enable_failed")
            raise HTTPException(503, "GLPI unavailable or category could not be confirmed") from error
    with tenant_connection(tenant_id) as db:
        db.execute("""INSERT INTO cm_feature_flags(tenant_id,feature,mode,approved_by,reason) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,feature) DO UPDATE SET mode=excluded.mode,approved_by=excluded.approved_by,
            reason=excluded.reason,updated_at=now()""",
                   (tenant_id, FEATURE, "enabled" if payload.enabled else "off", actor["id"], payload.reason))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) "
                   "VALUES (%s,%s,'set_feature','business_glpi',%s,%s)",
                   (tenant_id, actor["id"], tenant_id, Jsonb({"enabled": payload.enabled, "reason": payload.reason})))
    return {"enabled": payload.enabled, "category": GlpiClient.BUSINESS_CATEGORY, "category_id": category_id}


@router.post("/occurrences/{occurrence_id}/glpi-retry")
def retry_glpi(occurrence_id: UUID, tenant_id: str = Depends(tenant_header),
               identity: Identity = Depends(current_identity)):
    actor = _company_source_manager(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        if not glpi_enabled(db, tenant_id):
            raise HTTPException(409, "GLPI integration is not enabled for this company")
        row = db.execute("""SELECT glpi_ticket_id,glpi_sync_status FROM cm_business_occurrences
            WHERE tenant_id=%s AND id=%s FOR UPDATE""", (tenant_id, occurrence_id)).fetchone()
        if not row:
            raise HTTPException(404, "Occurrence not found")
        if row["glpi_ticket_id"] is not None:
            raise HTTPException(409, "Occurrence already has a GLPI ticket")
        enqueue(db, tenant_id, occurrence_id, "opened")
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) "
                   "VALUES (%s,%s,'glpi_retry','business_occurrence',%s)", (tenant_id, actor["id"], str(occurrence_id)))
    return {"status": "queued"}
