-- V0.2/P1 hardening. Requires 045 and 046. Roll out via migration-owner, not central_app.
ALTER TABLE cm_business_states ADD COLUMN IF NOT EXISTS state_changed_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE cm_business_values ADD COLUMN IF NOT EXISTS collector_id uuid;
ALTER TABLE cm_business_values ADD COLUMN IF NOT EXISTS revision integer;
CREATE INDEX IF NOT EXISTS ix_business_values_dedupe_agent ON cm_business_values(tenant_id,agent_id,indicator_id,event_id);
CREATE INDEX IF NOT EXISTS ix_business_states_freshness ON cm_business_states(tenant_id,last_collected_at);
CREATE INDEX IF NOT EXISTS ix_business_occurrences_open ON cm_business_occurrences(tenant_id,indicator_id)
    WHERE state IN ('open','acknowledged');
-- All new P1 tables already have FORCE RLS. Nothing is seeded or enabled automatically.
