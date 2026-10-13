-- Business Monitor isolated data domain. Never grants rights via infrastructure roles.
-- This migration is additive; verify next free prefix on target main branch before merging.
CREATE TABLE cm_business_grants (
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    capability text NOT NULL CHECK (capability IN (
        'business.view','business.monitor.create','business.monitor.update',
        'business.monitor.inactivate','business.occurrence.manage',
        'business.source.manage','business.grants.manage')),
    scope_type text NOT NULL DEFAULT 'company' CHECK (scope_type IN ('own','company')),
    granted_by uuid NOT NULL REFERENCES cm_users(id),
    reason text NOT NULL CHECK (length(trim(reason)) > 3),
    active boolean NOT NULL DEFAULT true,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,user_id,capability)
);
CREATE TABLE cm_business_indicators (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    code varchar(96) NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{2,95}$'),
    name varchar(180) NOT NULL,
    description text NOT NULL DEFAULT '',
    unit varchar(32) NOT NULL DEFAULT 'unidades',
    direction text NOT NULL DEFAULT 'above' CHECK (direction IN ('above','below')),
    warning_value numeric(22,6),
    critical_value numeric(22,6),
    stale_after_seconds integer NOT NULL DEFAULT 1800 CHECK (stale_after_seconds BETWEEN 30 AND 2592000),
    owner_user_id uuid NOT NULL REFERENCES cm_users(id),
    active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,id), UNIQUE (tenant_id,code),
    CHECK ((warning_value IS NULL AND critical_value IS NULL) OR
           (warning_value IS NOT NULL AND critical_value IS NOT NULL AND
           ((direction='above' AND warning_value < critical_value) OR
           (direction='below' AND warning_value > critical_value))))
);
CREATE TABLE cm_business_values (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    indicator_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    event_id varchar(160) NOT NULL,
    payload_sha256 char(64) NOT NULL,
    value numeric(22,6) NOT NULL,
    collected_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,agent_id) REFERENCES cm_agents(tenant_id,id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,indicator_id,event_id), UNIQUE(tenant_id,id)
);
CREATE TABLE cm_business_states (
    tenant_id uuid NOT NULL,
    indicator_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'sem_dados' CHECK (state IN ('normal','atencao','critico','sem_dados','suspenso')),
    last_value numeric(22,6),
    last_collected_at timestamptz,
    evaluated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,indicator_id),
    FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id) ON DELETE RESTRICT
);
CREATE TABLE cm_business_occurrences (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    indicator_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('open','acknowledged','normalized','closed')),
    severity text NOT NULL CHECK (severity IN ('atencao','critico')),
    opened_at timestamptz NOT NULL DEFAULT now(),
    normalized_at timestamptz,
    closed_at timestamptz,
    acknowledged_by uuid REFERENCES cm_users(id),
    FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,id)
);
CREATE UNIQUE INDEX ux_business_active_episode ON cm_business_occurrences(tenant_id,indicator_id)
    WHERE state IN ('open','acknowledged');
CREATE TABLE cm_business_agent_bindings (
    tenant_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    indicator_id uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_by uuid NOT NULL REFERENCES cm_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,agent_id,indicator_id),
    FOREIGN KEY(tenant_id,agent_id) REFERENCES cm_agents(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id) ON DELETE RESTRICT
);
CREATE INDEX ix_business_values_history ON cm_business_values(tenant_id,indicator_id,collected_at DESC);
CREATE INDEX ix_business_occurrences_recent ON cm_business_occurrences(tenant_id,state,opened_at DESC);
CREATE INDEX ix_business_grants_user ON cm_business_grants(tenant_id,user_id,active);
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['cm_business_grants','cm_business_indicators','cm_business_values',
                         'cm_business_states','cm_business_occurrences','cm_business_agent_bindings'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY business_tenant ON %I USING '
      || '(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) '
      || 'WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
  END LOOP;
END $$;
-- No default grants and no auto-enabled tenants. Provision explicitly.
