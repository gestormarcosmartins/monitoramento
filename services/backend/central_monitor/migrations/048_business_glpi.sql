-- Business Monitor -> GLPI integration (opt-in per company via feature flag 'business_glpi').
-- GLPI stays the authority for the ticket; these columns only mirror what GLPI confirmed.
ALTER TABLE cm_business_occurrences ADD COLUMN IF NOT EXISTS glpi_ticket_id bigint;
ALTER TABLE cm_business_occurrences ADD COLUMN IF NOT EXISTS glpi_sync_status text NOT NULL DEFAULT 'not_requested'
    CHECK (glpi_sync_status IN ('not_requested','pending','synced','failed'));
ALTER TABLE cm_business_occurrences ADD COLUMN IF NOT EXISTS glpi_sync_error text;
ALTER TABLE cm_business_occurrences ADD COLUMN IF NOT EXISTS glpi_status integer;
ALTER TABLE cm_business_occurrences ADD COLUMN IF NOT EXISTS glpi_synced_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_business_occurrences_glpi ON cm_business_occurrences(tenant_id,glpi_ticket_id)
    WHERE glpi_ticket_id IS NOT NULL;
-- Nothing is enabled automatically: a company-wide source manager turns the integration on.
