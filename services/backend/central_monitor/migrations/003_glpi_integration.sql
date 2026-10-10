ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS glpi_ticket_id bigint;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS sync_status text NOT NULL DEFAULT 'pending'
    CHECK (sync_status IN ('pending','synced','failed'));
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS sync_error text;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS synced_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS ux_incidents_glpi_ticket
    ON cm_incidents(glpi_ticket_id) WHERE glpi_ticket_id IS NOT NULL;
