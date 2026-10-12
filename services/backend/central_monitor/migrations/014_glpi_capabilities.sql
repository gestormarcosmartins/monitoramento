CREATE TABLE IF NOT EXISTS cm_integration_capabilities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    integration text NOT NULL,
    version text,
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    UNIQUE (tenant_id,integration)
);

ALTER TABLE cm_integration_capabilities ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_integration_capabilities FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_integration_capabilities;
CREATE POLICY tenant_isolation ON cm_integration_capabilities
    USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE INDEX IF NOT EXISTS ix_cm_integration_capabilities_tenant
    ON cm_integration_capabilities(tenant_id,integration,discovered_at DESC);
