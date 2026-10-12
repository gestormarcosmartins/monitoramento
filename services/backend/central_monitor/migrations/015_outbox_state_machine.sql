ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS state text NOT NULL DEFAULT 'pending';
ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS locked_at timestamptz;
ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS worker_id text;
ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS idempotency_key text;

UPDATE cm_outbox SET state='succeeded' WHERE processed_at IS NOT NULL;

DO $$ BEGIN
    ALTER TABLE cm_outbox ADD CONSTRAINT cm_outbox_state_check
        CHECK (state IN ('pending','processing','retry','succeeded','dead_letter'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_outbox_idempotency
    ON cm_outbox(tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_cm_outbox_dispatch
    ON cm_outbox(state,available_at,id) WHERE state IN ('pending','retry','processing');
