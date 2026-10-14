-- Push Notifications Nexora (Expo Push Service). Especificação v1.0, PUSH-001..008.
-- Independente do Web Push (cm_push_*): token web nunca é tratado como ExpoPushToken.

INSERT INTO cm_permission_catalog(code,description,object_scope,sensitive) VALUES
('infra.view','Consultar monitoramento de infraestrutura e receber seus alertas','company',false),
('notification.rules.manage','Gerenciar regras e políticas de notificação','company',true),
('notification.audit.view','Consultar entregas e auditoria de notificações','company',true)
ON CONFLICT (code) DO NOTHING;
INSERT INTO cm_role_permissions(role,permission_code) VALUES
('admin','infra.view'),('manager','infra.view'),('operator','infra.view'),
('admin','notification.rules.manage'),('manager','notification.rules.manage'),
('admin','notification.audit.view'),('manager','notification.audit.view'),('auditor','notification.audit.view')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS nx_push_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    platform text NOT NULL CHECK (platform IN ('android','ios')),
    expo_push_token_encrypted text NOT NULL,
    token_hash text NOT NULL,
    expo_project_id text NOT NULL,
    installation_id text NOT NULL,
    app_version text,
    enabled boolean NOT NULL DEFAULT true,
    permission_status text NOT NULL DEFAULT 'granted',
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    invalidated_at timestamptz,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (token_hash, expo_project_id)
);
CREATE INDEX IF NOT EXISTS ix_nx_push_devices_user ON nx_push_devices(user_id) WHERE enabled;
CREATE INDEX IF NOT EXISTS ix_nx_push_devices_install ON nx_push_devices(user_id,installation_id);

CREATE TABLE IF NOT EXISTS nx_notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    domain text NOT NULL CHECK (domain IN ('INFRA','BUSINESS','SERVICE')),
    source_type text NOT NULL,
    source_id uuid,
    event_key text NOT NULL,
    correlation_key text,
    severity text NOT NULL CHECK (severity IN ('P0','P1','P2','P3')),
    category text NOT NULL,
    title text NOT NULL,
    body_safe text NOT NULL,
    action_type text NOT NULL,
    action_ref uuid,
    require_ack boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    actor_id uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    UNIQUE (tenant_id,event_key)
);
CREATE INDEX IF NOT EXISTS ix_nx_notifications_tenant_created ON nx_notifications(tenant_id,created_at DESC);
CREATE INDEX IF NOT EXISTS ix_nx_notifications_correlation ON nx_notifications(tenant_id,correlation_key);

CREATE TABLE IF NOT EXISTS nx_notification_recipients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    notification_id uuid NOT NULL REFERENCES nx_notifications(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    opened_at timestamptz,
    acknowledged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (notification_id,user_id)
);
CREATE INDEX IF NOT EXISTS ix_nx_recipients_user ON nx_notification_recipients(tenant_id,user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS nx_push_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    recipient_id uuid NOT NULL REFERENCES nx_notification_recipients(id) ON DELETE CASCADE,
    device_id uuid NOT NULL REFERENCES nx_push_devices(id) ON DELETE CASCADE,
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN
        ('PENDING','PROCESSING','SENT_TO_EXPO','PROVIDER_ACCEPTED','RETRY_WAIT','FAILED_FINAL','DEVICE_INVALID','EXPIRED')),
    attempt_no integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    expo_ticket_id text,
    accepted_at timestamptz,
    receipt_checked_at timestamptz,
    provider_error_code text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recipient_id,device_id)
);
CREATE INDEX IF NOT EXISTS ix_nx_deliveries_queue ON nx_push_deliveries(next_attempt_at) WHERE state IN ('PENDING','RETRY_WAIT');
CREATE INDEX IF NOT EXISTS ix_nx_deliveries_processing ON nx_push_deliveries(updated_at) WHERE state='PROCESSING';
CREATE INDEX IF NOT EXISTS ix_nx_deliveries_receipts ON nx_push_deliveries(accepted_at) WHERE state='SENT_TO_EXPO';

CREATE TABLE IF NOT EXISTS nx_notification_preferences (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    domain text NOT NULL CHECK (domain IN ('INFRA','BUSINESS','SERVICE')),
    category text NOT NULL,
    push_enabled boolean NOT NULL DEFAULT true,
    in_app_enabled boolean NOT NULL DEFAULT true,
    quiet_hours jsonb,
    timezone text NOT NULL DEFAULT 'America/Sao_Paulo',
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,user_id,domain,category)
);

CREATE TABLE IF NOT EXISTS nx_push_audit (
    id bigserial PRIMARY KEY,
    tenant_id uuid REFERENCES cm_tenants(id) ON DELETE CASCADE,
    actor uuid,
    action text NOT NULL,
    target text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_nx_push_audit_tenant ON nx_push_audit(tenant_id,created_at DESC);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['nx_notifications','nx_notification_recipients','nx_push_deliveries','nx_notification_preferences','nx_push_audit']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
      USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
      WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)$p$, t);
  END LOOP;
END $$;
