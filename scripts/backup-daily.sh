#!/usr/bin/env bash
# Backup FULL diário do ambiente Nexora (Linux/Oracle), com envio para a máquina de backup.
#
# O que entra: PostgreSQL (central_monitor, keycloak, zabbix), MariaDB do GLPI, .env, .secrets,
# compose, infra/ (Caddy, Keycloak, wellknown) e o código implantado (services/backend, services/web/src, scripts).
# Cada execução gera uma pasta datada, com manifesto SHA-256, e só é considerada válida depois de verificada.
#
# Configuração: /etc/nexora-backup.conf  (modelo em scripts/nexora-backup.conf.example)
#   LOCAL_DIR       destino local            (padrão /home/opc/nexora-backups/daily)
#   LOCAL_KEEP      quantos dias manter local (padrão 7)
#   REMOTE_HOST     máquina de backup (vazio = só local)
#   REMOTE_USER     usuário SSH remoto
#   REMOTE_DIR      pasta remota             (com chave restrita (rrsync), use "." = pasta de entrada)
#   REMOTE_KEY      chave SSH deste servidor para a máquina de backup
#                   (a retenção remota é feita pela própria máquina de backup)
#
# Sai com código != 0 se qualquer etapa falhar (o systemd registra a falha) e nunca apaga backups antigos
# se o backup novo não foi verificado.
set -euo pipefail

CONF="${NEXORA_BACKUP_CONF:-/etc/nexora-backup.conf}"
[ -r "$CONF" ] && . "$CONF"
APP_DIR="${APP_DIR:-/opt/nexora}"
LOCAL_DIR="${LOCAL_DIR:-/home/opc/nexora-backups/daily}"
LOCAL_KEEP="${LOCAL_KEEP:-7}"
REMOTE_HOST="${REMOTE_HOST:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$LOCAL_DIR/nexora-$STAMP"
log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
fail() { log "ERRO: $*"; rm -rf "$DEST.partial"; exit 1; }

umask 077
mkdir -p "$LOCAL_DIR"
TMP="$DEST.partial"; mkdir -p "$TMP"
cd "$APP_DIR"

log "iniciando backup em $DEST"
for db in central_monitor keycloak zabbix; do
  docker compose exec -T postgres pg_dump -U platform_admin -d "$db" -Fc --no-owner --no-acl </dev/null > "$TMP/$db.dump" \
    || fail "pg_dump de $db"
done
docker compose exec -T glpi-db sh -c 'mariadb-dump -uglpi -p"$MARIADB_PASSWORD" --single-transaction glpi' </dev/null > "$TMP/glpi.sql" \
  || fail "mariadb-dump do GLPI"
tar czf "$TMP/arquivos.tar.gz" .env .secrets compose.yaml infra services/backend services/web/src scripts 2>/dev/null \
  || fail "arquivamento de configuração"

# Verificação: cada dump precisa ser legível e ter conteúdo; o GLPI precisa terminar com a marca de conclusão.
for db in central_monitor keycloak zabbix; do
  docker compose exec -T postgres sh -c 'cat > /tmp/verify.dump' < "$TMP/$db.dump"
  tables=$(docker compose exec -T postgres pg_restore --list /tmp/verify.dump </dev/null 2>/dev/null | grep -c 'TABLE DATA' || true)
  docker compose exec -T postgres rm -f /tmp/verify.dump </dev/null
  [ "$tables" -gt 10 ] || fail "dump de $db ilegível ou vazio ($tables tabelas)"
done
tail -1 "$TMP/glpi.sql" | grep -q 'Dump completed' || fail "dump do GLPI incompleto"
tar tzf "$TMP/arquivos.tar.gz" >/dev/null || fail "arquivo de configuração corrompido"
( cd "$TMP" && sha256sum ./* > manifest.sha256 )
mv "$TMP" "$DEST"
log "backup local verificado: $(du -sh "$DEST" | cut -f1)"

if [ -n "$REMOTE_HOST" ]; then
  : "${REMOTE_USER:?defina REMOTE_USER}" "${REMOTE_KEY:?defina REMOTE_KEY}"
  # A chave deste servidor é RESTRITA na máquina de backup (rrsync write-only): ela só consegue enviar arquivos para a
  # pasta de entrada, sem terminal, sem leitura, sem listar e sem apagar. Por isso não há verificação nem poda remota
  # a partir daqui: quem confere o manifesto, arquiva e poda é a própria máquina de backup (nexora-backup-intake).
  SSH="ssh -i $REMOTE_KEY -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes"
  rsync -a --partial --timeout=600 -e "$SSH" "$DEST" "$REMOTE_USER@$REMOTE_HOST:${REMOTE_DIR:-.}/" || fail "envio para a máquina de backup"
  log "enviado para a máquina de backup (a verificação e o arquivamento acontecem lá, em até ~20 min)"
fi

# Poda local: mantém os LOCAL_KEEP mais recentes (o novo já foi verificado).
ls -1dt "$LOCAL_DIR"/nexora-* 2>/dev/null | grep -v '\.partial$' | tail -n +$((LOCAL_KEEP + 1)) | xargs -r rm -rf
log "concluído"
