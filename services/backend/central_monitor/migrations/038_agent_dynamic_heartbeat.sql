ALTER TABLE cm_agents
    ADD COLUMN IF NOT EXISTS heartbeat_interval_seconds integer NOT NULL DEFAULT 30,
    ADD COLUMN IF NOT EXISTS services_interval_seconds integer NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS heartbeat_grace_seconds integer NOT NULL DEFAULT 15;

ALTER TABLE cm_agents DROP CONSTRAINT IF EXISTS cm_agents_heartbeat_interval_check;
ALTER TABLE cm_agents ADD CONSTRAINT cm_agents_heartbeat_interval_check
    CHECK (heartbeat_interval_seconds BETWEEN 10 AND 3600);

ALTER TABLE cm_agents DROP CONSTRAINT IF EXISTS cm_agents_services_interval_check;
ALTER TABLE cm_agents ADD CONSTRAINT cm_agents_services_interval_check
    CHECK (services_interval_seconds BETWEEN 10 AND 86400);
