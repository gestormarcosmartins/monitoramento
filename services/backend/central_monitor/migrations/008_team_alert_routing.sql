ALTER TABLE cm_teams ADD COLUMN IF NOT EXISTS emails text[] NOT NULL DEFAULT '{}';
ALTER TABLE cm_teams ADD COLUMN IF NOT EXISTS whatsapp_numbers text[] NOT NULL DEFAULT '{}';
ALTER TABLE cm_teams ADD COLUMN IF NOT EXISTS whatsapp_group_id text;

ALTER TABLE cm_users ADD COLUMN IF NOT EXISTS whatsapp_number text;

ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS responsible_team_id uuid;
ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS responsible_user_id uuid REFERENCES cm_users(id) ON DELETE SET NULL;
ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS notify_on_failure boolean NOT NULL DEFAULT true;
ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS notify_on_recovery boolean NOT NULL DEFAULT true;
ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS auto_resolve_incident boolean NOT NULL DEFAULT true;
ALTER TABLE cm_monitors DROP CONSTRAINT IF EXISTS cm_monitors_responsible_team_fk;
ALTER TABLE cm_monitors ADD CONSTRAINT cm_monitors_responsible_team_fk
    FOREIGN KEY (tenant_id,responsible_team_id) REFERENCES cm_teams(tenant_id,id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS cm_routing_deliveries (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    incident_id uuid NOT NULL,
    event text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('email','whatsapp')),
    target text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    FOREIGN KEY (tenant_id,incident_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    UNIQUE (incident_id,event,channel,target)
);

ALTER TABLE cm_routing_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_routing_deliveries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_routing_deliveries;
CREATE POLICY tenant_isolation ON cm_routing_deliveries
    USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

CREATE INDEX IF NOT EXISTS ix_routing_deliveries_status
    ON cm_routing_deliveries(tenant_id,status,created_at);
