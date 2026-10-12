CREATE TABLE cm_api_clients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id),
    name varchar(160) NOT NULL,
    token_hash char(64) NOT NULL,
    scopes text[] NOT NULL,
    actor_user_id uuid NOT NULL REFERENCES cm_users(id),
    active boolean NOT NULL DEFAULT true,
    expires_at timestamptz,
    last_used_at timestamptz,
    created_by uuid NOT NULL REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, id),
    CHECK (cardinality(scopes) > 0),
    CHECK (scopes <@ ARRAY['incidents:read','incidents:create','incidents:attend','incidents:finish']::text[])
);

CREATE INDEX ix_cm_api_clients_active ON cm_api_clients(tenant_id,id)
WHERE active;

CREATE TABLE cm_api_idempotency (
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id),
    api_client_id uuid NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    operation varchar(100) NOT NULL,
    request_hash char(64) NOT NULL,
    response_status integer NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
    PRIMARY KEY (tenant_id,api_client_id,idempotency_key),
    FOREIGN KEY (tenant_id,api_client_id) REFERENCES cm_api_clients(tenant_id,id) ON DELETE CASCADE
);

CREATE INDEX ix_cm_api_idempotency_expiry ON cm_api_idempotency(expires_at);

ALTER TABLE cm_api_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_api_clients FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm_api_clients
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

ALTER TABLE cm_api_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_api_idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm_api_idempotency
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
