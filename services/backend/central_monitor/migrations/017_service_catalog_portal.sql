CREATE TABLE IF NOT EXISTS cm_catalog_services (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    code text NOT NULL, name text NOT NULL, description text NOT NULL, category text NOT NULL DEFAULT 'Geral',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','retired')),
    active_version integer, created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(tenant_id,code), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_catalog_service_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, service_id uuid NOT NULL,
    version integer NOT NULL, description text NOT NULL, form_schema jsonb NOT NULL DEFAULT '{"fields":[]}'::jsonb,
    eligibility jsonb NOT NULL DEFAULT '{"roles":["client","viewer","operator","manager","admin"]}'::jsonb,
    routing jsonb NOT NULL DEFAULT '{}'::jsonb, approval_policy jsonb NOT NULL DEFAULT '{"required":false}'::jsonb,
    coverage jsonb NOT NULL DEFAULT '{}'::jsonb, sla_policy_id uuid, state text NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft','review','published','retired')),
    content_hash text NOT NULL, created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
    FOREIGN KEY (tenant_id,service_id) REFERENCES cm_catalog_services(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id,sla_policy_id) REFERENCES cm_sla_policies(tenant_id,id) ON DELETE SET NULL,
    UNIQUE(tenant_id,service_id,version), UNIQUE(tenant_id,id)
);

CREATE OR REPLACE FUNCTION cm_protect_published_catalog_version() RETURNS trigger AS $$
BEGIN
  IF OLD.state='published' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'published catalog versions are immutable';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS protect_published_catalog_version ON cm_catalog_service_versions;
CREATE TRIGGER protect_published_catalog_version BEFORE UPDATE OR DELETE ON cm_catalog_service_versions
FOR EACH ROW EXECUTE FUNCTION cm_protect_published_catalog_version();

CREATE TABLE IF NOT EXISTS cm_service_submissions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
    service_id uuid NOT NULL, service_version_id uuid NOT NULL, requester_id uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    answers jsonb NOT NULL, schema_snapshot jsonb NOT NULL, snapshot_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id,service_id) REFERENCES cm_catalog_services(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id,service_version_id) REFERENCES cm_catalog_service_versions(tenant_id,id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,ticket_id), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_ticket_approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
    submission_id uuid NOT NULL, requested_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    approver_id uuid REFERENCES cm_users(id) ON DELETE SET NULL, status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','cancelled')),
    snapshot_hash text NOT NULL, reason text, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id,submission_id) REFERENCES cm_service_submissions(tenant_id,id) ON DELETE CASCADE,
    CHECK (approver_id IS NULL OR approver_id<>requested_by), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_ticket_attachments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
    uploaded_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, original_name text NOT NULL,
    declared_type text, detected_type text NOT NULL, size_bytes bigint NOT NULL CHECK(size_bytes>=0), sha256 text NOT NULL,
    scan_status text NOT NULL DEFAULT 'quarantine' CHECK(scan_status IN ('quarantine','clean','rejected')),
    scan_details text, content bytea NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), scanned_at timestamptz,
    FOREIGN KEY (tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    UNIQUE(tenant_id,sha256,ticket_id)
);

CREATE INDEX IF NOT EXISTS ix_cm_catalog_published ON cm_catalog_services(tenant_id,status,category,name);
CREATE INDEX IF NOT EXISTS ix_cm_approvals_pending ON cm_ticket_approvals(tenant_id,status,created_at);
CREATE INDEX IF NOT EXISTS ix_cm_attachments_ticket ON cm_ticket_attachments(tenant_id,ticket_id,scan_status);

DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_catalog_services','cm_catalog_service_versions','cm_service_submissions','cm_ticket_approvals','cm_ticket_attachments'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
