ALTER TABLE cm_alerts ADD COLUMN IF NOT EXISTS inactive_at timestamptz;
ALTER TABLE cm_alerts ADD COLUMN IF NOT EXISTS inactive_reason text;

ALTER TABLE cm_holidays ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE cm_holidays ADD COLUMN IF NOT EXISTS inactive_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_cm_alerts_active_history
    ON cm_alerts(tenant_id,opened_at DESC) WHERE inactive_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_cm_holidays_active_date
    ON cm_holidays(tenant_id,holiday_date) WHERE active;
