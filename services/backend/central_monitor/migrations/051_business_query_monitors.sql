-- Business Monitor query monitors (spec "Business Monitor v1.0"): Master-governed, versioned SQL monitors run by
-- an isolated Business Worker that claims jobs through the API. Executions are the system of record for
-- idempotency, leases and audit; they are not a substitute for the broker (see docs).
CREATE TABLE cm_business_workers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (length(trim(name)) BETWEEN 2 AND 120),
    token_hash text NOT NULL,
    status text NOT NULL DEFAULT 'OFFLINE' CHECK (status IN ('ONLINE','OFFLINE','SUSPENDED','REVOKED')),
    version text,
    capacity integer NOT NULL DEFAULT 1 CHECK (capacity BETWEEN 1 AND 4),
    last_heartbeat_at timestamptz,
    last_execution_at timestamptz,
    created_by uuid NOT NULL REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE cm_business_data_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    worker_id uuid NOT NULL,
    name text NOT NULL CHECK (length(trim(name)) BETWEEN 2 AND 120),
    dialect text NOT NULL CHECK (dialect IN ('sqlserver','postgres')),
    -- Resolved on the customer side: no connection string, user or password ever reaches the central.
    database_ref text NOT NULL CHECK (database_ref ~ '^[a-z][a-z0-9_]{1,63}$'),
    active boolean NOT NULL DEFAULT true,
    created_by uuid NOT NULL REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, worker_id) REFERENCES cm_business_workers(tenant_id, id) ON DELETE RESTRICT,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE cm_business_monitors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (length(trim(name)) BETWEEN 3 AND 160),
    description text NOT NULL DEFAULT '',
    indicator_id uuid NOT NULL,
    source_id uuid NOT NULL,
    interval_seconds integer NOT NULL CHECK (interval_seconds BETWEEN 60 AND 86400),
    status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','ACTIVE','SUSPENDED','DISABLED')),
    suspended_reason text,
    current_revision integer NOT NULL DEFAULT 0,
    published_revision integer,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_scheduled_at timestamptz,
    created_by uuid NOT NULL REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, indicator_id) REFERENCES cm_business_indicators(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, source_id) REFERENCES cm_business_data_sources(tenant_id, id) ON DELETE RESTRICT,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE cm_business_monitor_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    monitor_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    query_text text NOT NULL,          -- normalized by the AST validator: this exact text is what runs
    query_sha256 char(64) NOT NULL,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    column_schema jsonb NOT NULL,
    metric jsonb NOT NULL,
    limits jsonb NOT NULL,
    state text NOT NULL DEFAULT 'DRAFT' CHECK (state IN ('DRAFT','TESTED','PUBLISHED','SUPERSEDED','REVOKED')),
    created_by uuid NOT NULL REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    tested_at timestamptz,
    published_by uuid REFERENCES cm_users(id),
    published_at timestamptz,
    FOREIGN KEY (tenant_id, monitor_id) REFERENCES cm_business_monitors(tenant_id, id) ON DELETE RESTRICT,
    UNIQUE (tenant_id, monitor_id, revision)
);

CREATE TABLE cm_business_executions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),   -- the execution_id: idempotency key end to end
    tenant_id uuid NOT NULL,
    monitor_id uuid NOT NULL,
    revision integer NOT NULL,
    worker_id uuid NOT NULL,
    is_test boolean NOT NULL DEFAULT false,
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','LEASED','RUNNING','SUCCEEDED','FAILED',
        'TIMED_OUT','REJECTED','EXPIRED','CANCELED')),
    scheduled_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    leased_at timestamptz,
    lease_expires_at timestamptz,
    lease_renewals integer NOT NULL DEFAULT 0,
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms integer,
    row_count integer,
    result_bytes integer,
    truncated boolean,
    result_sha256 char(64),
    error_code text,
    error_message text,
    correlation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, monitor_id, revision) REFERENCES cm_business_monitor_revisions(tenant_id, monitor_id, revision),
    FOREIGN KEY (tenant_id, worker_id) REFERENCES cm_business_workers(tenant_id, id) ON DELETE RESTRICT,
    UNIQUE (tenant_id, id)
);
CREATE INDEX ix_cm_business_exec_claim ON cm_business_executions(tenant_id, worker_id, scheduled_at)
    WHERE state IN ('PENDING','LEASED','RUNNING');
CREATE INDEX ix_cm_business_exec_monitor ON cm_business_executions(tenant_id, monitor_id, created_at DESC);

CREATE TABLE cm_business_execution_results (
    tenant_id uuid NOT NULL,
    execution_id uuid NOT NULL,
    columns jsonb NOT NULL,
    rows jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    retain_until timestamptz NOT NULL DEFAULT (now() + interval '90 days'),
    PRIMARY KEY (tenant_id, execution_id),
    FOREIGN KEY (tenant_id, execution_id) REFERENCES cm_business_executions(tenant_id, id) ON DELETE CASCADE
);

-- Values produced by query monitors enter the same series as HUB collectors.
ALTER TABLE cm_business_values ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE cm_business_values ADD COLUMN IF NOT EXISTS execution_id uuid;
ALTER TABLE cm_business_values ADD COLUMN IF NOT EXISTS worker_id uuid;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['cm_business_workers','cm_business_data_sources','cm_business_monitors',
      'cm_business_monitor_revisions','cm_business_executions','cm_business_execution_results'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY business_tenant ON %I USING '
      || '(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) '
      || 'WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
  END LOOP;
END $$;
-- Nothing is seeded or enabled: the technical Master creates workers, sources and monitors explicitly.
