-- Each company is bound to its own GLPI entity; tickets are created inside it.
ALTER TABLE cm_tenants ADD COLUMN IF NOT EXISTS glpi_entity_id bigint;
CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_tenants_glpi_entity ON cm_tenants(glpi_entity_id) WHERE glpi_entity_id IS NOT NULL;
