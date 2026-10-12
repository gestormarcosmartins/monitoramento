ALTER TABLE cm_incident_comments ADD COLUMN IF NOT EXISTS external_id text;
ALTER TABLE cm_ticket_tasks ADD COLUMN IF NOT EXISTS external_id text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_incident_comments_external
    ON cm_incident_comments(tenant_id,incident_id,external_id) WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_ticket_tasks_external
    ON cm_ticket_tasks(tenant_id,incident_id,external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS cm_reconciliation_cursors (
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    connection_id uuid NOT NULL,
    resource text NOT NULL,
    cursor text,
    last_polled_at timestamptz,
    last_success_at timestamptz,
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,connection_id,resource),
    FOREIGN KEY (tenant_id,connection_id) REFERENCES cm_integration_connections(tenant_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cm_reconciliation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    connection_id uuid NOT NULL,
    resource text NOT NULL,
    parent_external_id text NOT NULL,
    cursor_before text,
    cursor_after text,
    snapshot_hash text NOT NULL,
    state text NOT NULL DEFAULT 'completed' CHECK (state IN ('running','completed','failed')),
    observed_count integer NOT NULL DEFAULT 0,
    finding_count integer NOT NULL DEFAULT 0,
    requested_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (tenant_id,connection_id,resource,parent_external_id,snapshot_hash),
    FOREIGN KEY (tenant_id,connection_id) REFERENCES cm_integration_connections(tenant_id,id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_reconciliation_runs_tenant_id ON cm_reconciliation_runs(tenant_id,id);

CREATE TABLE IF NOT EXISTS cm_reconciliation_findings (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    run_id uuid NOT NULL,
    child_kind text NOT NULL CHECK (child_kind IN ('followup','task')),
    external_id text NOT NULL,
    finding_type text NOT NULL CHECK (finding_type IN ('import_required','remote_missing_requires_review')),
    state text NOT NULL DEFAULT 'open' CHECK (state IN ('open','resolved','dismissed')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    detected_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    UNIQUE (tenant_id,run_id,child_kind,external_id,finding_type),
    FOREIGN KEY (tenant_id,run_id) REFERENCES cm_reconciliation_runs(tenant_id,id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_cm_reconciliation_findings_open ON cm_reconciliation_findings(tenant_id,state,detected_at DESC);

DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_reconciliation_cursors','cm_reconciliation_runs','cm_reconciliation_findings'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
