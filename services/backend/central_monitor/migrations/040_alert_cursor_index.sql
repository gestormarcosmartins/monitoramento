-- Keyset pagination of the consolidated alert list (CD-09): per company, filtered by status and ordered by opened_at DESC, id DESC.
-- With the default status=open the set is small; resolved/all paginate by cursor instead of downloading the whole history.
CREATE INDEX IF NOT EXISTS ix_cm_alerts_cursor
    ON cm_alerts (tenant_id, status, opened_at DESC, id DESC) WHERE inactive_at IS NULL;
