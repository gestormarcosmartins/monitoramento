-- Início real da falha: com a carência (054) o alerta/chamado abre alguns minutos depois da queda.
-- failure_started_at guarda quando a sequência de falhas começou, para o aviso e a duração serem fiéis.
ALTER TABLE cm_alerts ADD COLUMN IF NOT EXISTS failure_started_at timestamptz;
