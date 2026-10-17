-- Atualização automática e assinada do agente de monitoramento, com anéis (mesmo desenho do Business Worker).
-- Versões do agente são da plataforma (sem tenant_id); quem recebe qual versão é decidido por agente (update_ring) e por versão (channel).
-- Padrão do anel é 'hold': nenhum agente existente se atualiza sozinho até o Master escolher o anel dele (canary no piloto).
CREATE TABLE IF NOT EXISTS cm_agent_releases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version text NOT NULL CHECK (version ~ '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$'),
    platform text NOT NULL CHECK (platform IN ('win-x64','linux-x64')),
    file_name text NOT NULL CHECK (file_name ~ '^[A-Za-z0-9._-]{1,120}$'),
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 1 AND 209715200),
    -- canary: só agentes do anel canary; stable: todos que não estão em hold; revoked: nunca oferecida e quem roda volta atrás.
    channel text NOT NULL DEFAULT 'canary' CHECK (channel IN ('canary','stable','revoked')),
    notes text NOT NULL DEFAULT '' CHECK (length(notes) <= 500),
    created_by uuid REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    promoted_at timestamptz,
    revoked_at timestamptz,
    UNIQUE (version, platform)
);

ALTER TABLE cm_agents
    ADD COLUMN IF NOT EXISTS update_ring text NOT NULL DEFAULT 'hold' CHECK (update_ring IN ('canary','stable','hold')),
    ADD COLUMN IF NOT EXISTS platform text CHECK (platform IN ('win-x64','linux-x64')),
    ADD COLUMN IF NOT EXISTS update_status text CHECK (update_status IN ('DOWNLOADED','APPLIED','FAILED','ROLLED_BACK')),
    ADD COLUMN IF NOT EXISTS update_version text,
    ADD COLUMN IF NOT EXISTS update_error text,
    ADD COLUMN IF NOT EXISTS update_reported_at timestamptz;
