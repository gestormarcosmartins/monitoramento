CREATE TABLE IF NOT EXISTS cm_integration_inbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    source text NOT NULL,
    connection_key text NOT NULL,
    external_event_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload_hash text NOT NULL,
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'received' CHECK (state IN ('received','correlated','quarantined','duplicate','rejected','failed')),
    result jsonb,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    last_error text,
    UNIQUE (tenant_id,source,connection_key,external_event_id),
    UNIQUE (tenant_id,id)
);

CREATE INDEX IF NOT EXISTS ix_cm_integration_inbox_state
    ON cm_integration_inbox(tenant_id,state,received_at DESC);

ALTER TABLE cm_integration_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_integration_inbox FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_integration_inbox;
CREATE POLICY tenant_isolation ON cm_integration_inbox
    USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
