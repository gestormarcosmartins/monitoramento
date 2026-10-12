ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS requested_by uuid REFERENCES cm_users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_cm_outbox_requested_by_pending
ON cm_outbox(tenant_id,requested_by,state) WHERE processed_at IS NULL AND requested_by IS NOT NULL;
