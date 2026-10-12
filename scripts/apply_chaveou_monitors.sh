#!/usr/bin/env bash
# Aplica sync_chaveou_development_monitors.sql na Central (Oracle) com verificação e backup.
# Uso (na VM, dentro da pasta do projeto, onde está o compose.yaml):
#   bash scripts/apply_chaveou_monitors.sh
set -euo pipefail

cd "$(dirname "$0")/.."
SQL_FILE="scripts/sync_chaveou_development_monitors.sql"
TENANT_ID="09c3cbd0-ca9b-4f21-bc7e-c3a5dc163b64"
BACKUP="chaveou_monitors_backup_$(date +%Y%m%d%H%M%S).sql"
psql_central() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U platform_admin -d central_monitor "$@"; }

[[ -f $SQL_FILE ]] || { echo "Arquivo não encontrado: $SQL_FILE" >&2; exit 1; }

echo "1/4 Verificando o tenant Chaveou..."
found=$(psql_central -tA -c "SELECT count(*) FROM cm_tenants WHERE id='${TENANT_ID}' AND name='Chaveou' AND active")
[[ $found == 1 ]] || { echo "Tenant Chaveou ativo não encontrado; nada foi alterado." >&2; exit 1; }

echo "2/4 Backup de cm_assets e cm_monitors do tenant em $BACKUP ..."
docker compose exec -T postgres pg_dump -U platform_admin -d central_monitor --data-only --inserts -t cm_assets -t cm_monitors > "$BACKUP"
chmod 600 "$BACKUP"

echo "3/4 Aplicando o script (transação única)..."
psql_central < "$SQL_FILE"

echo "4/4 Resultado:"
psql_central -c "SELECT name, monitor_type, last_status, last_message FROM cm_monitors WHERE tenant_id='${TENANT_ID}' AND configuration->>'source'='chaveou-development' ORDER BY name"
echo "Concluído. Backup: $BACKUP"
