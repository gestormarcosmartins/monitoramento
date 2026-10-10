ALTER TABLE cm_notification_rules DROP CONSTRAINT IF EXISTS cm_notification_rules_channel_check;
ALTER TABLE cm_notification_rules ADD CONSTRAINT cm_notification_rules_channel_check
    CHECK (channel IN ('email','whatsapp','webhook'));

CREATE TABLE IF NOT EXISTS cm_notification_settings (
    tenant_id uuid PRIMARY KEY REFERENCES cm_tenants(id) ON DELETE CASCADE,
    smtp_enabled boolean NOT NULL DEFAULT false,
    smtp_host text,
    smtp_port integer NOT NULL DEFAULT 587 CHECK (smtp_port BETWEEN 1 AND 65535),
    smtp_username text,
    smtp_password_encrypted text,
    smtp_from_address text,
    smtp_tls boolean NOT NULL DEFAULT true,
    evolution_enabled boolean NOT NULL DEFAULT false,
    evolution_api_url text,
    evolution_api_key_encrypted text,
    evolution_instance text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE cm_notification_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_notification_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_notification_settings;
CREATE POLICY tenant_isolation ON cm_notification_settings
    USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
