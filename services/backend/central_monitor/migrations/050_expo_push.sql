-- Mobile push through the Expo Push Service (PUSH-001..008).
-- Devices belong to a user (like cm_push_subscriptions); notifications, recipients and deliveries
-- are company-scoped with forced RLS. The Expo token is stored encrypted; only its hash is searchable.
CREATE TABLE cm_push_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    installation_id text NOT NULL CHECK (length(installation_id) BETWEEN 8 AND 128),
    platform text NOT NULL CHECK (platform IN ('android','ios')),
    token_cipher text NOT NULL,
    token_hash char(64) NOT NULL,
    token_hint text NOT NULL,
    expo_project_id text NOT NULL,
    app_version text,
    permission_status text NOT NULL DEFAULT 'granted' CHECK (permission_status IN ('granted','denied','undetermined')),
    enabled boolean NOT NULL DEFAULT true,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    invalidated_at timestamptz,
    invalid_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, installation_id)
);
-- A token routes to one active owner at a time (a shared or reassigned phone never double-delivers).
CREATE UNIQUE INDEX ux_cm_push_devices_token ON cm_push_devices(token_hash) WHERE enabled;
CREATE INDEX ix_cm_push_devices_user ON cm_push_devices(user_id) WHERE enabled;

CREATE TABLE cm_notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE RESTRICT,
    domain text NOT NULL CHECK (domain IN ('INFRA','BUSINESS','SERVICE')),
    source_type text NOT NULL,
    source_id text NOT NULL,
    event_key text NOT NULL,
    correlation_key text,
    severity text NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    category text NOT NULL,
    title text NOT NULL,
    body_safe text NOT NULL,
    action_type text NOT NULL,
    action_ref text NOT NULL,
    requires_ack boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, domain, event_key)
);
CREATE INDEX ix_cm_notifications_recent ON cm_notifications(tenant_id, created_at DESC);

CREATE TABLE cm_notification_recipients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    notification_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    opened_at timestamptz,
    acknowledged_at timestamptz,
    FOREIGN KEY (tenant_id, notification_id) REFERENCES cm_notifications(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (notification_id, user_id),
    UNIQUE (tenant_id, id)
);
CREATE INDEX ix_cm_notification_recipients_user ON cm_notification_recipients(tenant_id, user_id, created_at DESC);

CREATE TABLE cm_push_expo_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    device_id uuid NOT NULL REFERENCES cm_push_devices(id) ON DELETE CASCADE,
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','PROCESSING','SENT_TO_EXPO','PROVIDER_ACCEPTED',
        'RETRY_WAIT','FAILED_FINAL','DEVICE_INVALID','EXPIRED')),
    attempt_no integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    expo_ticket_id text,
    sent_at timestamptz,
    receipt_checked_at timestamptz,
    provider_error_code text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, recipient_id) REFERENCES cm_notification_recipients(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (recipient_id, device_id)
);
CREATE INDEX ix_cm_push_expo_dispatch ON cm_push_expo_deliveries(tenant_id, next_attempt_at)
    WHERE state IN ('PENDING','RETRY_WAIT');
CREATE INDEX ix_cm_push_expo_receipts ON cm_push_expo_deliveries(tenant_id, sent_at)
    WHERE state = 'SENT_TO_EXPO';

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['cm_notifications','cm_notification_recipients','cm_push_expo_deliveries'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING '
      || '(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) '
      || 'WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
  END LOOP;
END $$;
