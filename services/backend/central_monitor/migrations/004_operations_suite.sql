ALTER TABLE cm_user_tenants DROP CONSTRAINT IF EXISTS cm_user_tenants_role_check;
ALTER TABLE cm_user_tenants ADD CONSTRAINT cm_user_tenants_role_check
    CHECK (role IN ('admin','manager','operator','viewer','client','auditor'));

ALTER TABLE cm_incidents DROP CONSTRAINT IF EXISTS cm_incidents_status_check;
ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_status_check
    CHECK (status IN ('new','acknowledged','in_progress','waiting_customer','waiting_supplier','resolved','closed','ignored'));
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'medium'
    CHECK (priority IN ('low','medium','high','critical'));
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'availability';
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'monitoring';
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS assignee_user_id uuid REFERENCES cm_users(id) ON DELETE SET NULL;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS first_response_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS acknowledged_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS closed_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS response_due_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS resolution_due_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS sla_paused_at timestamptz;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS sla_paused_seconds bigint NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS ux_incidents_tenant_id ON cm_incidents(tenant_id,id);

CREATE TABLE IF NOT EXISTS cm_teams (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,name), UNIQUE (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS cm_team_members (
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    team_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    PRIMARY KEY (team_id,user_id), FOREIGN KEY (tenant_id,team_id) REFERENCES cm_teams(tenant_id,id) ON DELETE CASCADE
);
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS assignee_team_id uuid;
ALTER TABLE cm_incidents DROP CONSTRAINT IF EXISTS cm_incidents_assignee_team_fk;
ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_assignee_team_fk FOREIGN KEY (tenant_id,assignee_team_id) REFERENCES cm_teams(tenant_id,id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS cm_suppliers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL, contact_name text, email text, phone text, services text,
    contract_reference text, response_minutes integer NOT NULL DEFAULT 240,
    resolution_minutes integer NOT NULL DEFAULT 1440, active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id,name), UNIQUE (tenant_id,id)
);
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS supplier_id uuid;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS supplier_protocol text;
ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS supplier_due_at timestamptz;
ALTER TABLE cm_incidents DROP CONSTRAINT IF EXISTS cm_incidents_supplier_fk;
ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_supplier_fk FOREIGN KEY (tenant_id,supplier_id) REFERENCES cm_suppliers(tenant_id,id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS cm_sla_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL, priority text NOT NULL CHECK (priority IN ('low','medium','high','critical')),
    response_minutes integer NOT NULL CHECK (response_minutes > 0), resolution_minutes integer NOT NULL CHECK (resolution_minutes > 0),
    coverage text NOT NULL DEFAULT '24x7' CHECK (coverage IN ('24x7','business_hours')),
    business_start time NOT NULL DEFAULT '08:00', business_end time NOT NULL DEFAULT '18:00',
    business_days integer[] NOT NULL DEFAULT ARRAY[1,2,3,4,5], pause_on_waiting boolean NOT NULL DEFAULT true,
    active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id,priority), UNIQUE (tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_incident_comments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    incident_id uuid NOT NULL, author_id uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    body text NOT NULL, is_internal boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cm_incident_history (
    id bigserial PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    incident_id uuid NOT NULL, actor_id uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    event_type text NOT NULL, details jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cm_notification_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL, channel text NOT NULL CHECK (channel IN ('email','webhook')),
    target text NOT NULL, minimum_priority text NOT NULL DEFAULT 'high', events text[] NOT NULL DEFAULT ARRAY['incident.created'],
    active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id,name)
);
CREATE TABLE IF NOT EXISTS cm_notification_deliveries (
    id bigserial PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    rule_id uuid REFERENCES cm_notification_rules(id) ON DELETE SET NULL, incident_id uuid,
    event text NOT NULL, channel text NOT NULL, target text NOT NULL, status text NOT NULL CHECK (status IN ('pending','sent','failed')),
    attempts integer NOT NULL DEFAULT 0, last_error text, created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cm_maintenance_windows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    name text NOT NULL, asset_id uuid, starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
    reason text, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at), FOREIGN KEY (tenant_id,asset_id) REFERENCES cm_assets(tenant_id,id) ON DELETE CASCADE
);

INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes)
SELECT id,'SLA Baixo','low',240,2880 FROM cm_tenants ON CONFLICT (tenant_id,priority) DO NOTHING;
INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes)
SELECT id,'SLA Médio','medium',120,1440 FROM cm_tenants ON CONFLICT (tenant_id,priority) DO NOTHING;
INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes)
SELECT id,'SLA Alto','high',30,480 FROM cm_tenants ON CONFLICT (tenant_id,priority) DO NOTHING;
INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes)
SELECT id,'SLA Crítico','critical',15,240 FROM cm_tenants ON CONFLICT (tenant_id,priority) DO NOTHING;

CREATE INDEX IF NOT EXISTS ix_incidents_sla ON cm_incidents(tenant_id,resolution_due_at,status);
CREATE INDEX IF NOT EXISTS ix_incident_history ON cm_incident_history(tenant_id,incident_id,created_at);
CREATE INDEX IF NOT EXISTS ix_notification_deliveries ON cm_notification_deliveries(tenant_id,status,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_delivery_event ON cm_notification_deliveries(rule_id,incident_id,event);

ALTER TABLE cm_teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_teams FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_team_members FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_suppliers ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_suppliers FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_sla_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_sla_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_incident_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_incident_comments FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_incident_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_incident_history FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_notification_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_notification_rules FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_notification_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_notification_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_maintenance_windows ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_maintenance_windows FORCE ROW LEVEL SECURITY;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_teams','cm_team_members','cm_suppliers','cm_sla_policies','cm_incident_comments','cm_incident_history','cm_notification_rules','cm_notification_deliveries','cm_maintenance_windows']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid)', table_name);
  END LOOP;
END $$;
