CREATE TABLE IF NOT EXISTS cm_queues (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL, description text, team_id uuid, active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,team_id) REFERENCES cm_teams(tenant_id,id) ON DELETE SET NULL,
    UNIQUE(tenant_id,name), UNIQUE(tenant_id,id)
);
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS queue_id uuid;
DO $$ BEGIN ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_queue_fk FOREIGN KEY(tenant_id,queue_id) REFERENCES cm_queues(tenant_id,id) ON DELETE SET NULL; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS cm_sla_policy_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, policy_id uuid NOT NULL,
    version integer NOT NULL, timezone text NOT NULL DEFAULT 'America/Sao_Paulo', response_minutes integer NOT NULL CHECK(response_minutes>0),
    resolution_minutes integer NOT NULL CHECK(resolution_minutes>0), coverage text NOT NULL CHECK(coverage IN ('24x7','business_hours')),
    business_start time NOT NULL DEFAULT '08:00', business_end time NOT NULL DEFAULT '18:00', business_days integer[] NOT NULL DEFAULT ARRAY[1,2,3,4,5],
    pause_states text[] NOT NULL DEFAULT ARRAY['waiting_customer'], effective_from timestamptz NOT NULL,
    effective_until timestamptz, state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','published','retired')),
    created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
    FOREIGN KEY(tenant_id,policy_id) REFERENCES cm_sla_policies(tenant_id,id) ON DELETE CASCADE,
    CHECK(effective_until IS NULL OR effective_until>effective_from), UNIQUE(tenant_id,policy_id,version), UNIQUE(tenant_id,id)
);
CREATE OR REPLACE FUNCTION cm_protect_published_sla_version() RETURNS trigger AS $$ BEGIN
  IF OLD.state='published' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'published SLA versions are immutable'; END IF; RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS protect_published_sla_version ON cm_sla_policy_versions;
CREATE TRIGGER protect_published_sla_version BEFORE UPDATE OR DELETE ON cm_sla_policy_versions FOR EACH ROW EXECUTE FUNCTION cm_protect_published_sla_version();

CREATE TABLE IF NOT EXISTS cm_ticket_obligations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('assignment_tto','first_response','resolution_ttr','supplier_ola')),
    due_at timestamptz NOT NULL, completed_at timestamptz, paused_at timestamptz, paused_seconds bigint NOT NULL DEFAULT 0,
    state text NOT NULL DEFAULT 'running' CHECK(state IN ('running','paused','met','breached','cancelled')),
    policy_version_id uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id,policy_version_id) REFERENCES cm_sla_policy_versions(tenant_id,id) ON DELETE SET NULL,
    UNIQUE(tenant_id,ticket_id,kind)
);

CREATE TABLE IF NOT EXISTS cm_time_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL, task_id uuid,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE RESTRICT, description text NOT NULL,
    started_at timestamptz NOT NULL, ended_at timestamptz, minutes integer,
    state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','pending_glpi','confirmed','rejected','cancelled')),
    glpi_task_id bigint, rejection_reason text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES cm_ticket_tasks(id) ON DELETE SET NULL,
    CHECK(ended_at IS NULL OR ended_at>started_at), CHECK(minutes IS NULL OR minutes>0), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_notification_preferences (
    tenant_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    email_enabled boolean NOT NULL DEFAULT true, whatsapp_enabled boolean NOT NULL DEFAULT false,
    in_app_enabled boolean NOT NULL DEFAULT true, muted_events text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,user_id)
);

CREATE TABLE IF NOT EXISTS cm_escalation_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
    obligation_id uuid NOT NULL, level integer NOT NULL CHECK(level BETWEEN 1 AND 5), reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(obligation_id) REFERENCES cm_ticket_obligations(id) ON DELETE CASCADE,
    UNIQUE(tenant_id,obligation_id,level)
);

CREATE INDEX IF NOT EXISTS ix_cm_queue_tickets ON cm_incidents(tenant_id,queue_id,status,priority,updated_at DESC) WHERE inactive_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_cm_obligations_due ON cm_ticket_obligations(tenant_id,state,due_at);
CREATE INDEX IF NOT EXISTS ix_cm_time_entries_user ON cm_time_entries(tenant_id,user_id,state,started_at DESC);

DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_queues','cm_sla_policy_versions','cm_ticket_obligations','cm_time_entries','cm_notification_preferences','cm_escalation_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name); EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
