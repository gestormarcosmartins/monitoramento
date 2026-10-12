CREATE TABLE IF NOT EXISTS cm_departments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,name),
    UNIQUE (tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_user_departments (
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    department_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,user_id,department_id),
    FOREIGN KEY (tenant_id,department_id) REFERENCES cm_departments(tenant_id,id) ON DELETE CASCADE
);

ALTER TABLE cm_access_grants ADD COLUMN IF NOT EXISTS department_id uuid;
ALTER TABLE cm_access_grants ADD COLUMN IF NOT EXISTS team_id uuid;

DO $$ BEGIN
    ALTER TABLE cm_access_grants ADD CONSTRAINT cm_access_grants_department_fk
        FOREIGN KEY (tenant_id,department_id) REFERENCES cm_departments(tenant_id,id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE cm_access_grants ADD CONSTRAINT cm_access_grants_team_fk
        FOREIGN KEY (tenant_id,team_id) REFERENCES cm_teams(tenant_id,id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Historical grants had no target columns because the old API never enforced them.
-- Downgrade impossible targeted scopes to the secure own-record scope before adding validation.
UPDATE cm_access_grants SET row_scope='own'
WHERE row_scope='department' AND department_id IS NULL;
UPDATE cm_access_grants SET row_scope='own'
WHERE row_scope='team_service' AND team_id IS NULL AND engagement_id IS NULL;

DO $$ BEGIN
    ALTER TABLE cm_access_grants ADD CONSTRAINT cm_access_grants_scope_target_check CHECK (
        (row_scope='department' AND department_id IS NOT NULL)
        OR (row_scope='team_service' AND (team_id IS NOT NULL OR engagement_id IS NOT NULL))
        OR row_scope IN ('own','company')
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DROP INDEX IF EXISTS ux_cm_access_grants_active;
CREATE UNIQUE INDEX ux_cm_access_grants_active
    ON cm_access_grants(
        workspace_id,tenant_id,user_id,role,row_scope,
        COALESCE(engagement_id,'00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(department_id,'00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(team_id,'00000000-0000-0000-0000-000000000000'::uuid)
    ) WHERE active;

ALTER TABLE cm_departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_departments FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_user_departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_user_departments FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON cm_departments;
CREATE POLICY tenant_isolation ON cm_departments
    USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

DROP POLICY IF EXISTS tenant_isolation ON cm_user_departments;
CREATE POLICY tenant_isolation ON cm_user_departments
    USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE INDEX IF NOT EXISTS ix_cm_user_departments_user
    ON cm_user_departments(tenant_id,user_id,department_id);
CREATE INDEX IF NOT EXISTS ix_cm_access_grants_scope
    ON cm_access_grants(tenant_id,user_id,row_scope,department_id,team_id,engagement_id)
    WHERE active;
