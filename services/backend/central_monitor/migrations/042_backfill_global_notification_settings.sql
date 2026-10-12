-- 041 seeded cm_notification_settings_global by reading cm_notification_settings
-- without an app.tenant_id context. That table has FORCE ROW LEVEL SECURITY, so the
-- seed query silently saw zero rows and the global config was created empty even
-- though Morada's working credentials were (and still are) in place. Backfill it by
-- setting the tenant context before reading, so RLS lets the row through.
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
        PERFORM set_config('app.tenant_id', source_tenant::text, true);

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
