ALTER TABLE cm_incidents ADD COLUMN IF NOT EXISTS sla_policy_id uuid;
DO $$ BEGIN
  ALTER TABLE cm_incidents ADD CONSTRAINT cm_incidents_sla_policy_fk
    FOREIGN KEY (tenant_id,sla_policy_id) REFERENCES cm_sla_policies(tenant_id,id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE cm_incident_comments ADD COLUMN IF NOT EXISTS updated_at timestamptz;
ALTER TABLE cm_incident_comments ADD COLUMN IF NOT EXISTS edited_by uuid REFERENCES cm_users(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS cm_in_app_notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    ticket_id uuid,
    event text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    UNIQUE (tenant_id,user_id,ticket_id,event)
);

CREATE INDEX IF NOT EXISTS ix_cm_notifications_user_unread
  ON cm_in_app_notifications(tenant_id,user_id,created_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_cm_tickets_assignee_queue
  ON cm_incidents(tenant_id,assignee_user_id,assignee_team_id,status,updated_at DESC)
  WHERE inactive_at IS NULL;

ALTER TABLE cm_in_app_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_in_app_notifications FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_in_app_notifications;
CREATE POLICY tenant_isolation ON cm_in_app_notifications
  USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
