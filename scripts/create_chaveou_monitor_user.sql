-- Cria um usuário somente leitura para o agente de monitoramento Nexora.
-- Rode como superusuário (ex: sudo -u postgres psql -d chaveou -f create_chaveou_monitor_user.sql)
-- Antes de rodar, troque TROQUE_ESTA_SENHA por uma senha forte e nova.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chaveou_monitor') THEN
        CREATE ROLE chaveou_monitor LOGIN PASSWORD 'TROQUE_ESTA_SENHA';
    ELSE
        ALTER ROLE chaveou_monitor WITH LOGIN PASSWORD 'TROQUE_ESTA_SENHA';
    END IF;
END $$;

-- Permissão só de conectar e consultar estatísticas (pg_monitor é um papel nativo do Postgres).
GRANT CONNECT ON DATABASE chaveou TO chaveou_monitor;
GRANT pg_monitor TO chaveou_monitor;
GRANT USAGE ON SCHEMA public TO chaveou_monitor;

-- Leitura das tabelas que o coletor consulta.
GRANT SELECT ON mo_services TO chaveou_monitor;
GRANT SELECT ON sy_tenants TO chaveou_monitor;

-- Garante que tabelas criadas no futuro no schema public continuem legíveis (opcional, remova se não quiser).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO chaveou_monitor;

-- Reduz o tempo de consulta e impede qualquer escrita acidental nesta sessão (reforço, não é a única proteção).
ALTER ROLE chaveou_monitor SET default_transaction_read_only = on;
ALTER ROLE chaveou_monitor SET statement_timeout = '30s';

SELECT 'CHAVEOU_MONITOR_OK' AS resultado;
