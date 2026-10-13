"""Transactional-outbox hook for the Business -> GLPI integration (no imports from business modules)."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

FEATURE = "business_glpi"
TOPIC = "business.occurrence.glpi"
EVENTS = ("opened", "escalated", "normalized", "acknowledged", "closed")


def glpi_enabled(db: Any, tenant_id: str) -> bool:
    row = db.execute("SELECT mode FROM cm_feature_flags WHERE tenant_id=%s AND feature=%s",
                     (tenant_id, FEATURE)).fetchone()
    return bool(row and row["mode"] == "enabled")


def enqueue(db: Any, tenant_id: str, occurrence_id: Any, event: str) -> None:
    """Call inside the transaction that changed the occurrence. No-op unless the company opted in."""
    if event not in EVENTS or not glpi_enabled(db, tenant_id):
        return
    if event == "opened":
        db.execute("""UPDATE cm_business_occurrences SET glpi_sync_status='pending',glpi_sync_error=NULL
            WHERE tenant_id=%s AND id=%s AND glpi_ticket_id IS NULL""", (tenant_id, occurrence_id))
    db.execute("INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,%s,%s)",
               (tenant_id, TOPIC, Jsonb({"occurrence_id": str(occurrence_id), "event": event})))
