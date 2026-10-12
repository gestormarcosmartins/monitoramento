-- WhatsApp (Evolution API) and SMTP are platform-wide, not per-client: one shared
-- configuration is used to notify every tenant, instead of each tenant configuring
-- (and paying for) its own Evolution/SMTP credentials.
CREATE TABLE IF NOT EXISTS cm_notification_settings_global (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
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

-- Seed the single global row from whichever tenant already has working credentials
-- (most recently updated wins if more than one tenant had something configured).
INSERT INTO cm_notification_settings_global (
    id,smtp_enabled,smtp_host,smtp_port,smtp_username,smtp_password_encrypted,
    smtp_from_address,smtp_tls,evolution_enabled,evolution_api_url,
    evolution_api_key_encrypted,evolution_instance,updated_at
)
SELECT true,smtp_enabled,smtp_host,smtp_port,smtp_username,smtp_password_encrypted,
    smtp_from_address,smtp_tls,evolution_enabled,evolution_api_url,
    evolution_api_key_encrypted,evolution_instance,updated_at
FROM cm_notification_settings
WHERE evolution_enabled OR smtp_enabled
ORDER BY updated_at DESC
LIMIT 1
ON CONFLICT (id) DO NOTHING;

INSERT INTO cm_notification_settings_global (id) VALUES (true) ON CONFLICT (id) DO NOTHING;
