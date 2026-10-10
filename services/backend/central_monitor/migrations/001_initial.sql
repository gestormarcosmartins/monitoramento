CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS cm_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_subject text NOT NULL UNIQUE,
    email text NOT NULL UNIQUE,
    display_name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cm_tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    slug text NOT NULL UNIQUE,
    active boolean NOT NULL DEFAULT true,
    timezone text NOT NULL DEFAULT 'America/Sao_Paulo',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cm_user_tenants (
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('admin','operator','viewer')),
    PRIMARY KEY (user_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS cm_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('linux','windows','database','network','website','api','service','other')),
    address text,
    criticality text NOT NULL DEFAULT 'medium' CHECK (criticality IN ('low','medium','high','critical')),
    active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS cm_monitors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    asset_id uuid NOT NULL,
    name text NOT NULL,
    monitor_type text NOT NULL CHECK (monitor_type IN ('http','tcp','zabbix','event_deadman')),
    configuration jsonb NOT NULL,
    interval_seconds integer NOT NULL DEFAULT 60 CHECK (interval_seconds BETWEEN 30 AND 86400),
    severity text NOT NULL DEFAULT 'warning' CHECK (severity IN ('info','warning','average','high','disaster')),
    active boolean NOT NULL DEFAULT true,
    last_checked_at timestamptz,
    last_status text NOT NULL DEFAULT 'unknown' CHECK (last_status IN ('unknown','up','down')),
    last_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES cm_assets(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS cm_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    external_event_id text NOT NULL,
    source text NOT NULL,
    event_type text NOT NULL,
    severity text NOT NULL,
    asset_id uuid,
    occurred_at timestamptz NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES cm_assets(tenant_id, id) ON DELETE SET NULL,
    UNIQUE (tenant_id, source, external_event_id)
);

CREATE TABLE IF NOT EXISTS cm_alerts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    monitor_id uuid,
    fingerprint text NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','ignored')),
    severity text NOT NULL,
    title text NOT NULL,
    message text,
    opened_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    FOREIGN KEY (tenant_id, monitor_id) REFERENCES cm_monitors(tenant_id, id) ON DELETE SET NULL,
    UNIQUE (tenant_id, fingerprint, status)
);

CREATE TABLE IF NOT EXISTS cm_incidents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    alert_id uuid NOT NULL REFERENCES cm_alerts(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new','acknowledged','in_progress','resolved','ignored')),
    title text NOT NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, alert_id)
);

CREATE TABLE IF NOT EXISTS cm_action_definitions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    risk_level text NOT NULL CHECK (risk_level IN ('low','medium','high')),
    enabled boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS cm_action_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    action_id uuid NOT NULL REFERENCES cm_action_definitions(id) ON DELETE CASCADE,
    asset_id uuid NOT NULL,
    enabled boolean NOT NULL DEFAULT false,
    max_per_hour integer NOT NULL DEFAULT 1 CHECK (max_per_hour > 0),
    cooldown_seconds integer NOT NULL DEFAULT 1800,
    FOREIGN KEY (tenant_id, asset_id) REFERENCES cm_assets(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (tenant_id, action_id, asset_id)
);

CREATE TABLE IF NOT EXISTS cm_action_executions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    action_id uuid NOT NULL REFERENCES cm_action_definitions(id),
    asset_id uuid NOT NULL,
    requested_by uuid REFERENCES cm_users(id),
    idempotency_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('requested','validated','rejected','running','succeeded','failed')),
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    FOREIGN KEY (tenant_id, asset_id) REFERENCES cm_assets(tenant_id, id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS cm_outbox (
    id bigserial PRIMARY KEY,
    tenant_id uuid,
    topic text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    last_error text
);

CREATE TABLE IF NOT EXISTS cm_audit_log (
    id bigserial PRIMARY KEY,
    tenant_id uuid,
    user_id uuid,
    action text NOT NULL,
    object_type text NOT NULL,
    object_id text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO cm_action_definitions(code, name, risk_level)
VALUES
    ('rerun_idempotent_job', 'Reexecutar job idempotente', 'low'),
    ('restart_stateless_worker', 'Reiniciar worker sem estado', 'low'),
    ('collect_diagnostics', 'Coletar diagnosticos homologados', 'low')
ON CONFLICT (code) DO NOTHING;

CREATE INDEX IF NOT EXISTS ix_monitors_due ON cm_monitors(active, last_checked_at);
CREATE INDEX IF NOT EXISTS ix_alerts_tenant_status ON cm_alerts(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_incidents_tenant_status ON cm_incidents(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON cm_outbox(processed_at, id);

