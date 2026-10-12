CREATE TABLE IF NOT EXISTS cm_asset_external_identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, asset_id uuid NOT NULL,
    source text NOT NULL CHECK(source IN ('glpi','zabbix','agent','nexora')),
    connection_key text NOT NULL, external_id text NOT NULL, verified boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(), verified_at timestamptz,
    FOREIGN KEY(tenant_id,asset_id) REFERENCES cm_assets(tenant_id,id) ON DELETE CASCADE,
    UNIQUE(tenant_id,source,connection_key,external_id), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_asset_dependencies (
    tenant_id uuid NOT NULL, upstream_asset_id uuid NOT NULL, downstream_asset_id uuid NOT NULL,
    dependency_type text NOT NULL DEFAULT 'depends_on', source text NOT NULL DEFAULT 'manual',
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,upstream_asset_id,downstream_asset_id,dependency_type),
    FOREIGN KEY(tenant_id,upstream_asset_id) REFERENCES cm_assets(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id,downstream_asset_id) REFERENCES cm_assets(tenant_id,id) ON DELETE CASCADE,
    CHECK(upstream_asset_id<>downstream_asset_id)
);

CREATE TABLE IF NOT EXISTS cm_event_quarantine (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    source text NOT NULL, connection_key text NOT NULL, external_event_id text NOT NULL, external_asset_id text,
    reason text NOT NULL, payload jsonb NOT NULL, status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','mapped','discarded')),
    resolved_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, resolved_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(tenant_id,source,connection_key,external_event_id)
);

CREATE TABLE IF NOT EXISTS cm_event_episodes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    connection_key text NOT NULL, asset_id uuid NOT NULL, service_key text NOT NULL, trigger_key text NOT NULL,
    episode_key text NOT NULL, status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','recovering','closed')),
    technical_severity text NOT NULL, commercial_priority text, first_event_at timestamptz NOT NULL,
    last_event_at timestamptz NOT NULL, recovery_at timestamptz, incident_id uuid,
    FOREIGN KEY(tenant_id,asset_id) REFERENCES cm_assets(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE SET NULL,
    UNIQUE(tenant_id,connection_key,episode_key), UNIQUE(tenant_id,id)
);
ALTER TABLE cm_events ADD COLUMN IF NOT EXISTS connection_key text;
ALTER TABLE cm_events ADD COLUMN IF NOT EXISTS episode_id uuid;
ALTER TABLE cm_events ADD COLUMN IF NOT EXISTS processing_state text NOT NULL DEFAULT 'accepted';
DO $$ BEGIN ALTER TABLE cm_events ADD CONSTRAINT cm_events_episode_fk FOREIGN KEY(tenant_id,episode_id) REFERENCES cm_event_episodes(tenant_id,id) ON DELETE SET NULL; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE cm_events ADD CONSTRAINT cm_events_processing_state_check CHECK(processing_state IN ('accepted','correlated','quarantined','ignored')); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS cm_major_incidents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), operator_tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    title text NOT NULL, summary text NOT NULL, status text NOT NULL DEFAULT 'declared' CHECK(status IN ('declared','investigating','monitoring','resolved','closed')),
    severity text NOT NULL CHECK(severity IN ('high','critical')), commander_id uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    declared_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(operator_tenant_id,id)
);
CREATE TABLE IF NOT EXISTS cm_major_incident_impacts (
    major_incident_id uuid NOT NULL, operator_tenant_id uuid NOT NULL, impacted_tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    incident_id uuid, public_summary text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(major_incident_id,impacted_tenant_id),
    FOREIGN KEY(operator_tenant_id,major_incident_id) REFERENCES cm_major_incidents(operator_tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(impacted_tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS cm_channel_capabilities (
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE, channel text NOT NULL,
    provider text, identity_verified boolean NOT NULL DEFAULT false, consent_verified boolean NOT NULL DEFAULT false,
    template_approved boolean NOT NULL DEFAULT false, capability_verified boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb, updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,channel)
);

CREATE TABLE IF NOT EXISTS cm_external_dependency_status (
    dependency text PRIMARY KEY, status text NOT NULL CHECK(status IN ('up','degraded','down','unknown')),
    checked_at timestamptz NOT NULL, details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_cm_external_identity_lookup ON cm_asset_external_identities(tenant_id,source,connection_key,external_id,verified);
CREATE INDEX IF NOT EXISTS ix_cm_quarantine_pending ON cm_event_quarantine(tenant_id,status,created_at);
CREATE INDEX IF NOT EXISTS ix_cm_episodes_open ON cm_event_episodes(tenant_id,status,last_event_at DESC);

DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_asset_external_identities','cm_asset_dependencies','cm_event_quarantine','cm_event_episodes','cm_channel_capabilities'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name); EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
