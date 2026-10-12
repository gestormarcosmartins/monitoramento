\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    chaveou_id constant uuid := '09c3cbd0-ca9b-4f21-bc7e-c3a5dc163b64';
    asset_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM cm_tenants
        WHERE id = chaveou_id AND name = 'Chaveou' AND active
    ) THEN
        RAISE EXCEPTION 'Active Chaveou tenant was not found';
    END IF;

    INSERT INTO cm_assets(
        tenant_id, name, kind, address, criticality, metadata
    ) VALUES (
        chaveou_id,
        'Chaveou - Desenvolvimento',
        'service',
        'Projeto local Chaveou',
        'critical',
        jsonb_build_object(
            'source', 'chaveou-development',
            'sourceTenant', 'Chaveou.com',
            'sourceTenantPublicId', '9bc08223-f3e1-47d3-bc2c-52833c400eb1'
        )
    )
    ON CONFLICT (tenant_id, name) DO UPDATE SET
        kind = EXCLUDED.kind,
        address = EXCLUDED.address,
        criticality = EXCLUDED.criticality,
        active = true,
        metadata = cm_assets.metadata || EXCLUDED.metadata
    RETURNING id INTO asset_id;

    INSERT INTO cm_monitors(
        tenant_id, asset_id, name, monitor_type, configuration,
        interval_seconds, severity, active, last_checked_at,
        last_status, last_message
    )
    SELECT
        chaveou_id,
        asset_id,
        item.name,
        'push',
        jsonb_build_object(
            'source', 'chaveou-development',
            'sourceCode', item.code,
            'sourceAdapter', item.adapter
        ),
        60,
        item.severity,
        true,
        '2026-10-11 10:09:16.148+00'::timestamptz,
        item.status,
        item.message
    FROM (VALUES
        ('api', 'API Chaveou', 'api_interna', 'disaster', 'up', 'API_PROCESSO_ATIVO'),
        ('banking', 'Chaveou Banking (integrações bancárias)', 'banking', 'high', 'unknown', 'BANKING_SEM_INTEGRACAO'),
        ('coletor_facebook', 'Coletor Facebook Marketplace (Radar)', 'coletor_facebook', 'average', 'unknown', 'COLETOR_SEM_BUSCAS_ATIVAS'),
        ('evolution', 'Evolution / WhatsApp', 'http_json', 'high', 'up', 'HTTP_OK'),
        ('evonexus', 'EvoNexus', 'http_json', 'high', 'up', 'HTTP_OK'),
        ('jobs', 'Workers e fila de jobs', 'jobs', 'high', 'down', 'JOBS_FALHOS'),
        ('n8n', 'n8n', 'http_json', 'high', 'up', 'HTTP_OK'),
        ('ollama', 'Ollama / IA', 'http_json', 'high', 'up', 'HTTP_OK'),
        ('postgres', 'PostgreSQL Chaveou', 'postgres', 'disaster', 'up', 'POSTGRES_OK'),
        ('postgres-connectivity', 'PostgreSQL - Conectividade direta', 'postgres_agent', 'disaster', 'unknown', 'AGUARDANDO_AGENTE'),
        ('postgres-connections', 'PostgreSQL - Uso de conexões', 'postgres_agent', 'high', 'unknown', 'AGUARDANDO_AGENTE'),
        ('postgres-blocking', 'PostgreSQL - Bloqueios', 'postgres_agent', 'high', 'unknown', 'AGUARDANDO_AGENTE'),
        ('postgres-long-transactions', 'PostgreSQL - Transações longas', 'postgres_agent', 'average', 'unknown', 'AGUARDANDO_AGENTE'),
        ('postgres-vacuum', 'PostgreSQL - Saúde do vacuum', 'postgres_agent', 'average', 'unknown', 'AGUARDANDO_AGENTE'),
        ('postgres-replication', 'PostgreSQL - Replicação', 'postgres_agent', 'high', 'unknown', 'AGUARDANDO_AGENTE'),
        ('postgres-performance', 'PostgreSQL - Análise de performance', 'postgres_agent', 'average', 'unknown', 'AGUARDANDO_AGENTE')
    ) AS item(code, name, adapter, severity, status, message)
    ON CONFLICT (tenant_id, name) DO UPDATE SET
        asset_id = EXCLUDED.asset_id,
        monitor_type = EXCLUDED.monitor_type,
        configuration = EXCLUDED.configuration,
        interval_seconds = EXCLUDED.interval_seconds,
        severity = EXCLUDED.severity,
        active = true;
END $$;

COMMIT;
