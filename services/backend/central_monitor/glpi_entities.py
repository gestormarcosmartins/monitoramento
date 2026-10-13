"""One GLPI entity per Nexora company: every ticket (technical or Business) is bound to its company's entity."""
from __future__ import annotations

from .db import connect
from .glpi import GlpiClient


def ensure_tenant_entity(tenant_id: str, client: GlpiClient | None = None) -> int:
    """Return the company's GLPI entity id, creating the entity on first use.

    Raises when GLPI cannot confirm the entity: callers must retry rather than open a ticket without a company.
    The advisory lock keeps two workers from creating the same entity twice."""
    with connect() as db:
        row = db.execute("SELECT glpi_entity_id FROM cm_tenants WHERE id=%s", (tenant_id,)).fetchone()
    if row and row["glpi_entity_id"]:
        return int(row["glpi_entity_id"])
    client = client or GlpiClient()
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"glpi-entity:{tenant_id}",))
        row = db.execute("SELECT name,glpi_entity_id FROM cm_tenants WHERE id=%s FOR UPDATE", (tenant_id,)).fetchone()
        if not row:
            raise LookupError("Company not found")
        if row["glpi_entity_id"]:
            return int(row["glpi_entity_id"])
        entity_id = client.find_entity(tenant_id, row["name"]) or client.create_entity(tenant_id, row["name"])
        db.execute("UPDATE cm_tenants SET glpi_entity_id=%s WHERE id=%s", (entity_id, tenant_id))
        return entity_id
