-- 042 tried to discover which tenant to copy from by reading
-- cm_notification_settings before setting app.tenant_id — a chicken-and-egg
-- problem, since the app connects as central_app (no BYPASSRLS), so that
-- initial read was already hidden by the table's FORCE ROW LEVEL SECURITY and
-- returned nothing. 042 committed successfully as a silent no-op.
--
-- central_app does not own cm_notification_settings either (ALTER TABLE ...
-- DISABLE ROW LEVEL SECURITY fails with "must be owner of table"), so RLS
-- cannot be lifted from inside a migration. Instead, set app.tenant_id to the
-- one tenant already confirmed (via manual inspection) to hold the working
-- Evolution/SMTP credentials, so the policy lets that single row through.
DO $$
BEGIN
    PERFORM set_config('app.tenant_id', '398c3a5e-bc75-48e9-b982-53eccae7eafb', true);

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
    WHERE g.id = true
        AND s.tenant_id = '398c3a5e-bc75-48e9-b982-53eccae7eafb'
        AND (s.evolution_enabled OR s.smtp_enabled);
END $$;
