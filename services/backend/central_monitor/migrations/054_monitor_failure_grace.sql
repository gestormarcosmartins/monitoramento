-- Carência antes de abrir alerta/chamado: o monitor precisa ficar em falha por um tempo mínimo.
-- failing_since: início da sequência de falhas atual (NULL enquanto o monitor está OK).
-- failure_grace_seconds: NULL = padrão do sistema (agentes/push: MONITOR_PUSH_FAILURE_GRACE_SECONDS; demais: 0 = imediato).
ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS failing_since timestamptz;
ALTER TABLE cm_monitors ADD COLUMN IF NOT EXISTS failure_grace_seconds integer;
ALTER TABLE cm_monitors DROP CONSTRAINT IF EXISTS cm_monitors_failure_grace_check;
ALTER TABLE cm_monitors ADD CONSTRAINT cm_monitors_failure_grace_check
    CHECK (failure_grace_seconds IS NULL OR failure_grace_seconds BETWEEN 0 AND 86400);

-- Monitores que já estão em falha agora começam a contar a partir da última checagem.
UPDATE cm_monitors SET failing_since = COALESCE(last_checked_at, now()) WHERE last_status = 'down' AND failing_since IS NULL;
