-- Keyset pagination of the consolidated ticket list (CD-05): ORDER BY updated_at DESC, id DESC per company.
-- ix_cm_tickets_queue starts with status/priority and cannot serve this ordering when no status filter is set.
CREATE INDEX IF NOT EXISTS ix_cm_tickets_cursor
    ON cm_incidents (tenant_id, updated_at DESC, id DESC) WHERE inactive_at IS NULL;
