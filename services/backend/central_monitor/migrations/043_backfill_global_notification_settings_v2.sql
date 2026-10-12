-- 042 tried to backfill cm_notification_settings_global by setting app.tenant_id
-- AFTER reading cm_notification_settings to discover which tenant to copy from —
-- a chicken-and-egg problem, since the app connects as central_app (no BYPASSRLS),
-- so that very first read was already hidden by cm_notification_settings' FORCE RLS
-- and found nothing. 042 then silently committed as a no-op.
--
-- DDL isn't subject to RLS, so disable it for the duration of this migration only,
-- do the copy, then restore it exactly as 007 configured it.
ALTER TABLE cm_notification_settings DISABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    source_tenant uuid;
BEGIN
    SELECT tenant_id INTO source_tenant
    FROM cm_notification_settings
    WHERE evolution_enabled OR smtp_enabled
    ORDER BY updated_at DESC
    LIMIT 1;

    IF source_tenant IS NOT NULL THEN
        UPDATE cm_notification_settings_global g
        SET smtp_enabled = s.smtp_enabled,
            smtp_host = s.smtp_host,
            smtp_port = s.smtp_port,
            smtp_username = s.smtp_username,
            smtp_password_encrypted = s.smtp_password_encrypted,
            smtp_from_address = s.smtp_from_address,
            smtp_tls = s.smtp_tls,
            evolution_enabled = s.evolution_enabled,
            evolution_api_url = s.evolution_api_url,
            evolution_api_key_encrypted = s.evolution_api_key_encrypted,
            evolution_instance = s.evolution_instance,
            updated_at = now()
        FROM cm_notification_settings s
        WHERE g.id = true AND s.tenant_id = source_tenant;
    END IF;
END $$;

ALTER TABLE cm_notification_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_notification_settings FORCE ROW LEVEL SECURITY;
