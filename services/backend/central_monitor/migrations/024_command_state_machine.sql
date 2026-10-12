ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS payload_hash text;
ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS correlation_id uuid DEFAULT gen_random_uuid();
ALTER TABLE cm_outbox ADD COLUMN IF NOT EXISTS state_reason text;
UPDATE cm_outbox SET payload_hash=encode(digest(payload::text,'sha256'),'hex') WHERE payload_hash IS NULL;
CREATE OR REPLACE FUNCTION cm_outbox_payload_hash() RETURNS trigger AS $$ BEGIN
  IF TG_OP='UPDATE' OR NEW.payload_hash IS NULL THEN NEW.payload_hash=encode(digest(NEW.payload::text,'sha256'),'hex'); END IF; RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS set_cm_outbox_payload_hash ON cm_outbox;
CREATE TRIGGER set_cm_outbox_payload_hash BEFORE INSERT OR UPDATE OF payload ON cm_outbox FOR EACH ROW EXECUTE FUNCTION cm_outbox_payload_hash();
ALTER TABLE cm_outbox ALTER COLUMN payload_hash SET NOT NULL;
ALTER TABLE cm_outbox DROP CONSTRAINT IF EXISTS cm_outbox_state_check;
UPDATE cm_outbox SET state=CASE state WHEN 'processing' THEN 'running' WHEN 'retry' THEN 'retry_wait' WHEN 'dead_letter' THEN 'uncertain' ELSE state END;
ALTER TABLE cm_outbox ADD CONSTRAINT cm_outbox_state_check CHECK(state IN ('pending','running','succeeded','rejected','retry_wait','uncertain','conflict','cancelled'));
DROP INDEX IF EXISTS ix_cm_outbox_dispatch;
CREATE INDEX ix_cm_outbox_dispatch ON cm_outbox(state,available_at,id) WHERE state IN ('pending','retry_wait','running');
CREATE TABLE IF NOT EXISTS cm_outbox_attempts (
  id bigserial PRIMARY KEY, outbox_id bigint NOT NULL REFERENCES cm_outbox(id) ON DELETE CASCADE,
  attempt integer NOT NULL, worker_id text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  outcome text CHECK(outcome IN ('succeeded','retry_wait','rejected','uncertain','conflict','cancelled')), error text,
  UNIQUE(outbox_id,attempt)
);
