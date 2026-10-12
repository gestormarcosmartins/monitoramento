\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    morada_id constant uuid := '398c3a5e-bc75-48e9-b982-53eccae7eafb';
    asset_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM cm_tenants WHERE id = morada_id AND name = 'Morada' AND active
    ) THEN
        RAISE EXCEPTION 'Active Morada tenant was not found';
    END IF;

    INSERT INTO cm_assets(
        tenant_id, name, kind, address, criticality, metadata
    ) VALUES (
        morada_id,
        'SRVMORADABD - SQL Server',
        'database',
        'SRVMORADABD',
        'critical',
        jsonb_build_object('source', 'windows-agent-direct')
    )
    ON CONFLICT (tenant_id, name) DO UPDATE SET
        kind = EXCLUDED.kind,
        address = EXCLUDED.address,
        criticality = EXCLUDED.criticality,
        active = true,
        metadata = cm_assets.metadata || EXCLUDED.metadata
    RETURNING id INTO asset_id;

    -- Codes must match Nexora.Agent/SqlServerCollector.cs: it reads each monitor's
    -- configuration->>'code' (OperationalAsync/PerformanceAsync) to decide which of
    -- its own SQL checks to attach a result to. Without a matching 'push' monitor,
    -- GET /api/v1/agent/config returns nothing for the agent to report against.
    INSERT INTO cm_monitors(
        tenant_id, asset_id, name, monitor_type, configuration,
        interval_seconds, severity, active, last_status, last_message
    )
    SELECT
        morada_id,
        asset_id,
        item.name,
        'push',
        jsonb_build_object('source', 'windows-agent-direct', 'code', item.code),
        item.interval_seconds,
        item.severity,
        true,
        'unknown',
        'Aguardando primeiro envio do agente.'
    FROM (VALUES
        ('sql-connectivity', 'SRVMORADABD - Conectividade SQL', 'disaster', 600),
        ('database-status', 'SRVMORADABD - Status dos bancos', 'high', 600),
        ('blocking', 'SRVMORADABD - Bloqueios', 'high', 600),
        ('long-transactions', 'SRVMORADABD - Transações longas', 'average', 600),
        ('sql-performance', 'SRVMORADABD - Desempenho (queries)', 'average', 21600)
    ) AS item(code, name, severity, interval_seconds)
    ON CONFLICT (tenant_id, name) DO UPDATE SET
        asset_id = EXCLUDED.asset_id,
        monitor_type = EXCLUDED.monitor_type,
        configuration = EXCLUDED.configuration,
        interval_seconds = EXCLUDED.interval_seconds,
        severity = EXCLUDED.severity,
        active = true;
END $$;

COMMIT;
