CREATE TABLE IF NOT EXISTS cm_agents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    machine_id text NOT NULL,
    token_hash text NOT NULL,
    version text,
    status text NOT NULL DEFAULT 'offline' CHECK (status IN ('online','offline','disabled')),
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,machine_id),
    UNIQUE (tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_agent_enrollment_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    token_hash text NOT NULL,
    description text,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,token_hash)
);

CREATE TABLE IF NOT EXISTS cm_agent_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    agent_id uuid NOT NULL,
    execution_id text NOT NULL,
    collected_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,agent_id) REFERENCES cm_agents(tenant_id,id) ON DELETE CASCADE,
    UNIQUE (tenant_id,agent_id,execution_id)
);

CREATE TABLE IF NOT EXISTS cm_agent_logs (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    agent_id uuid NOT NULL,
    level text NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,agent_id) REFERENCES cm_agents(tenant_id,id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_agents_last_seen ON cm_agents(tenant_id,last_seen_at);
CREATE INDEX IF NOT EXISTS ix_agent_logs_recent ON cm_agent_logs(tenant_id,agent_id,occurred_at DESC);

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_agents','cm_agent_enrollment_tokens','cm_agent_snapshots','cm_agent_logs']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid)', table_name);
  END LOOP;
END $$;
