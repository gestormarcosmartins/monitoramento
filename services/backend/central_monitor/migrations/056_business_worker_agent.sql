-- Business Worker embutido no agente de monitoramento: o worker passa a poder usar a identidade do agente
-- (sem token próprio). Um worker é OU de token (agent_id nulo) OU de agente (token_hash nulo), nunca os dois.
ALTER TABLE cm_business_workers ALTER COLUMN token_hash DROP NOT NULL;
ALTER TABLE cm_business_workers ADD COLUMN IF NOT EXISTS agent_id uuid REFERENCES cm_agents(id) ON DELETE RESTRICT;
ALTER TABLE cm_business_workers DROP CONSTRAINT IF EXISTS cm_business_workers_identity_kind;
ALTER TABLE cm_business_workers ADD CONSTRAINT cm_business_workers_identity_kind
    CHECK ((agent_id IS NULL AND token_hash IS NOT NULL) OR (agent_id IS NOT NULL AND token_hash IS NULL));
CREATE UNIQUE INDEX IF NOT EXISTS cm_business_workers_one_per_agent ON cm_business_workers (tenant_id, agent_id) WHERE agent_id IS NOT NULL;
