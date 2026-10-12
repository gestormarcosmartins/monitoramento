ALTER TABLE cm_incidents ALTER COLUMN alert_id DROP NOT NULL;
ALTER TABLE cm_incidents DROP CONSTRAINT IF EXISTS cm_incidents_status_check;
ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_status_check CHECK (status IN (
    'new','triaged','assigned','acknowledged','in_progress','waiting_customer','waiting_supplier',
    'resolved','closed','cancelled','duplicate','ignored'
));
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS ticket_type text NOT NULL DEFAULT 'incident';
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS requester_user_id uuid REFERENCES cm_users(id) ON DELETE SET NULL;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS engagement_id uuid;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS service_code text;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS source_reference text;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS inactive_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS inactive_reason text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_service_engagements_tenant_id
    ON cm_service_engagements(tenant_id,id);

DO $$ BEGIN
    ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_ticket_type_check
        CHECK (ticket_type IN ('incident','request'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_engagement_fk
        FOREIGN KEY (tenant_id,engagement_id) REFERENCES cm_service_engagements(tenant_id,id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS cm_ticket_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, incident_id uuid NOT NULL,
    title text NOT NULL, description text, status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','completed','cancelled')),
    assignee_user_id uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    due_at timestamptz, completed_at timestamptz, created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cm_ticket_solutions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, incident_id uuid NOT NULL,
    solution_type text NOT NULL CHECK (solution_type IN ('permanent','workaround','not_reproduced','cancelled')),
    description text NOT NULL, evidence text, status text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','accepted','rejected','superseded')),
    proposed_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, validated_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(), validated_at timestamptz,
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cm_ticket_duplicates (
    tenant_id uuid NOT NULL, incident_id uuid NOT NULL, canonical_incident_id uuid NOT NULL,
    linked_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,incident_id),
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id,canonical_incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE RESTRICT,
    CHECK (incident_id<>canonical_incident_id)
);

CREATE INDEX IF NOT EXISTS ix_cm_tickets_queue ON cm_incidents(tenant_id,status,priority,updated_at DESC) WHERE inactive_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_cm_ticket_tasks_incident ON cm_ticket_tasks(tenant_id,incident_id,status);
CREATE INDEX IF NOT EXISTS ix_cm_ticket_solutions_incident ON cm_ticket_solutions(tenant_id,incident_id,created_at DESC);

ALTER TABLE cm_ticket_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_ticket_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_ticket_solutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_ticket_solutions FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_ticket_duplicates ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_ticket_duplicates FORCE ROW LEVEL SECURITY;
DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_ticket_tasks','cm_ticket_solutions','cm_ticket_duplicates'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
