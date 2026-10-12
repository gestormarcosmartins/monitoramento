CREATE TABLE IF NOT EXISTS cm_governance_history (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  object_type text NOT NULL, object_id uuid NOT NULL, from_status text, to_status text NOT NULL,
  actor_id uuid REFERENCES cm_users(id), details jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cm_oncall_schedules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  name text NOT NULL, timezone text NOT NULL DEFAULT 'America/Sao_Paulo', recurrence_rule text NOT NULL,
  starts_at timestamptz NOT NULL, ends_at timestamptz, team_id uuid,
  FOREIGN KEY(tenant_id,team_id) REFERENCES cm_teams(tenant_id,id) ON DELETE RESTRICT,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,name)
);
CREATE TABLE IF NOT EXISTS cm_oncall_substitutions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, schedule_id uuid NOT NULL,
  original_user_id uuid NOT NULL REFERENCES cm_users(id), substitute_user_id uuid NOT NULL REFERENCES cm_users(id),
  starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL, reason text NOT NULL, approved_by uuid REFERENCES cm_users(id), created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(tenant_id,schedule_id) REFERENCES cm_oncall_schedules(tenant_id,id) ON DELETE CASCADE,
  CHECK(original_user_id<>substitute_user_id), CHECK(ends_at>starts_at)
);
DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_governance_history','cm_oncall_schedules','cm_oncall_substitutions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name); EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
