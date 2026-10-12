CREATE TABLE IF NOT EXISTS cm_integration_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  integration text NOT NULL CHECK(integration IN ('glpi','zabbix','agent','smtp','whatsapp','erp')),
  connection_key text NOT NULL, display_name text NOT NULL, base_url text, secret_reference text,
  active boolean NOT NULL DEFAULT true, homologation_state text NOT NULL DEFAULT 'draft' CHECK(homologation_state IN ('draft','discovered','validated','homologated','blocked')),
  last_success_at timestamptz, last_failure_at timestamptz, last_error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,id), UNIQUE(tenant_id,integration,connection_key)
);
CREATE TABLE IF NOT EXISTS cm_integration_mappings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
  mapping_type text NOT NULL, external_id text NOT NULL, internal_value text NOT NULL,
  verified boolean NOT NULL DEFAULT false, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), verified_at timestamptz,
  FOREIGN KEY(tenant_id,connection_id) REFERENCES cm_integration_connections(tenant_id,id) ON DELETE CASCADE,
  UNIQUE(tenant_id,connection_id,mapping_type,external_id)
);
DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_integration_connections','cm_integration_mappings'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name); EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
