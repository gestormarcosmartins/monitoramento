ALTER TABLE cm_agents ADD COLUMN IF NOT EXISTS last_health jsonb NOT NULL DEFAULT '{}'::jsonb;
