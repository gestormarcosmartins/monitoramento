\set ON_ERROR_STOP on

-- SRVHYPERAMC already enrolled before the host-monitor auto-provisioning feature
-- existed, so (unlike future agents) it needs this one-off backfill. Codes must
-- match Nexora.Agent/HostCollector.cs's Operational() method.
BEGIN;

DO $$
DECLARE
    morada_id constant uuid := '398c3a5e-bc75-48e9-b982-53eccae7eafb';
    agent_name constant text := 'Morada - SRVHYPERAMC';
    asset_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM cm_tenants WHERE id = morada_id AND name = 'Morada' AND active
    ) THEN
        RAISE EXCEPTION 'Active Morada tenant was not found';
    END IF;

    INSERT INTO cm_assets(tenant_id, name, kind, address, criticality, metadata)
    VALUES (morada_id, agent_name, 'windows', agent_name, 'critical', jsonb_build_object('source', 'windows-agent-direct'))
    ON CONFLICT (tenant_id, name) DO UPDATE SET active = true
    RETURNING id INTO asset_id;

    INSERT INTO cm_monitors(
        tenant_id, asset_id, name, monitor_type, configuration,
        interval_seconds, severity, active, last_status, last_message
    )
    SELECT
        morada_id,
        asset_id,
        agent_name || ' - ' || item.label,
        'push',
        jsonb_build_object('source', 'windows-agent-direct', 'code', item.code),
        item.interval_seconds,
        item.severity,
        true,
        'unknown',
        'Aguardando primeiro envio do agente.'
    FROM (VALUES
        ('host-cpu', 'CPU', 'high', 300),
        ('host-memory', 'Memória', 'high', 300),
        ('host-disk', 'Espaço em disco', 'high', 300),
        ('host-services', 'Serviços essenciais', 'disaster', 300)
    ) AS item(code, label, severity, interval_seconds)
    ON CONFLICT (tenant_id, name) DO UPDATE SET
        asset_id = EXCLUDED.asset_id,
        monitor_type = EXCLUDED.monitor_type,
        configuration = EXCLUDED.configuration,
        interval_seconds = EXCLUDED.interval_seconds,
        severity = EXCLUDED.severity,
        active = true;
END $$;

COMMIT;
