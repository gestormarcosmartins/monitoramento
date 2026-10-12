CREATE TABLE IF NOT EXISTS cm_feature_flags (
  tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE, feature text NOT NULL,
  mode text NOT NULL DEFAULT 'off' CHECK(mode IN ('off','shadow','write_pilot','enabled')),
  approved_by uuid REFERENCES cm_users(id), reason text, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,feature)
);
CREATE TABLE IF NOT EXISTS cm_knowledge_articles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  glpi_article_id bigint, title text NOT NULL, content text NOT NULL, visibility text NOT NULL DEFAULT 'internal' CHECK(visibility IN ('internal','customer','public')),
  acl_roles text[] NOT NULL DEFAULT ARRAY['admin','manager','operator'], status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','published','retired')),
  version integer NOT NULL DEFAULT 1, created_by uuid REFERENCES cm_users(id), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,id), UNIQUE(tenant_id,glpi_article_id)
);
CREATE TABLE IF NOT EXISTS cm_problems (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  title text NOT NULL, description text NOT NULL, status text NOT NULL DEFAULT 'investigating' CHECK(status IN ('investigating','root_cause_known','known_error','resolved','closed')),
  hypothesis text, root_cause text, workaround text, owner_user_id uuid REFERENCES cm_users(id), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS cm_problem_incidents (
  tenant_id uuid NOT NULL, problem_id uuid NOT NULL, incident_id uuid NOT NULL,
  PRIMARY KEY(tenant_id,problem_id,incident_id), FOREIGN KEY(tenant_id,problem_id) REFERENCES cm_problems(tenant_id,id) ON DELETE CASCADE,
  FOREIGN KEY(tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cm_changes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  title text NOT NULL, change_type text NOT NULL CHECK(change_type IN ('standard','normal','emergency')),
  risk text NOT NULL CHECK(risk IN ('low','medium','high','critical')), status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','approved','scheduled','running','validating','completed','rolled_back','cancelled')),
  implementation_plan text NOT NULL, validation_plan text NOT NULL, rollback_plan text NOT NULL,
  window_start timestamptz, window_end timestamptz, approved_by uuid REFERENCES cm_users(id), approved_at timestamptz,
  owner_user_id uuid REFERENCES cm_users(id), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS cm_runbooks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
  code text NOT NULL, name text NOT NULL, status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','published','retired')),
  current_version integer, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,code)
);
CREATE TABLE IF NOT EXISTS cm_runbook_versions (
  tenant_id uuid NOT NULL, runbook_id uuid NOT NULL, version integer NOT NULL, risk text NOT NULL CHECK(risk IN ('low','medium','high','critical')),
  parameters_schema jsonb NOT NULL DEFAULT '{}'::jsonb, target_selector jsonb NOT NULL DEFAULT '{}'::jsonb,
  preconditions jsonb NOT NULL DEFAULT '[]'::jsonb, steps jsonb NOT NULL DEFAULT '[]'::jsonb, postconditions jsonb NOT NULL DEFAULT '[]'::jsonb,
  lock_key text NOT NULL, approval_required boolean NOT NULL DEFAULT true, created_by uuid REFERENCES cm_users(id), created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,runbook_id,version), FOREIGN KEY(tenant_id,runbook_id) REFERENCES cm_runbooks(tenant_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cm_runbook_executions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, runbook_id uuid NOT NULL, version integer NOT NULL,
  status text NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','approved','running','succeeded','failed','uncertain','rolled_back')),
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb, target_asset_id uuid, approved_by uuid REFERENCES cm_users(id), requested_by uuid REFERENCES cm_users(id),
  started_at timestamptz, finished_at timestamptz, result jsonb, created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(tenant_id,runbook_id,version) REFERENCES cm_runbook_versions(tenant_id,runbook_id,version),
  FOREIGN KEY(tenant_id,target_asset_id) REFERENCES cm_assets(tenant_id,id) ON DELETE RESTRICT, UNIQUE(tenant_id,id)
);
DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_feature_flags','cm_knowledge_articles','cm_problems','cm_problem_incidents','cm_changes','cm_runbooks','cm_runbook_versions','cm_runbook_executions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name); EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
