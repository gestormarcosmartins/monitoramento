#!/usr/bin/env bash
# Installs the Nexora Business Worker as its OWN systemd service (separate from the infrastructure agent).
#
#   sudo ./install-business-worker.sh --server-url https://monitoramento.suaempresa.com --tenant-id GUID --worker-id GUID \
#        --signing-key BASE64 --token-file /root/worker.token \
#        [--database-ref erp_principal --connection-file /root/erp.conn]
#
# The token and connection strings are read from files (never from the command line, which leaks into history).
# They are copied to /etc/nexora-business-worker, readable only by root and the service user.
set -euo pipefail

SOURCE="$(cd "$(dirname "$0")" && pwd)/dist/business-worker/linux-x64"
SERVER_URL="" TENANT_ID="" WORKER_ID="" SIGNING_KEY="" TOKEN_FILE="" DATABASE_REF="" CONNECTION_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --server-url) SERVER_URL="$2"; shift 2;;
    --tenant-id) TENANT_ID="$2"; shift 2;;
    --worker-id) WORKER_ID="$2"; shift 2;;
    --signing-key) SIGNING_KEY="$2"; shift 2;;
    --token-file) TOKEN_FILE="$2"; shift 2;;
    --database-ref) DATABASE_REF="$2"; shift 2;;
    --connection-file) CONNECTION_FILE="$2"; shift 2;;
    --source) SOURCE="$2"; shift 2;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
case "$SERVER_URL" in https://*) ;; *) echo "--server-url must be https" >&2; exit 2;; esac
[ -n "$TENANT_ID" ] && [ -n "$WORKER_ID" ] || { echo "--tenant-id and --worker-id are required" >&2; exit 2; }
[ -n "$SIGNING_KEY" ] || { echo "--signing-key is required (pin it at install time)" >&2; exit 2; }
[ -r "$TOKEN_FILE" ] || { echo "--token-file must be a readable file" >&2; exit 2; }
[ -x "$SOURCE/Nexora.BusinessWorker" ] || { echo "Worker binary not found in $SOURCE (run build-business-worker.ps1)" >&2; exit 1; }
if [ -n "$DATABASE_REF" ]; then
  echo "$DATABASE_REF" | grep -Eq '^[a-z][a-z0-9_]{1,63}$' || { echo "--database-ref must match ^[a-z][a-z0-9_]{1,63}$" >&2; exit 2; }
  [ -r "$CONNECTION_FILE" ] || { echo "--connection-file is required with --database-ref (use a READ-ONLY login)" >&2; exit 2; }
fi

id nexora-bw >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin nexora-bw
install -d -m 0755 /opt/nexora-business-worker
install -d -m 0750 -o root -g nexora-bw /etc/nexora-business-worker /etc/nexora-business-worker/secrets
cp -a "$SOURCE"/. /opt/nexora-business-worker/
install -d -m 0755 /usr/local/lib/nexora-business-worker
install -m 0755 -o root -g root "$(dirname "$0")/apply-business-worker-update.sh" /usr/local/lib/nexora-business-worker/apply-update.sh
chmod 0755 /opt/nexora-business-worker/Nexora.BusinessWorker
install -m 0640 -o root -g nexora-bw "$TOKEN_FILE" /etc/nexora-business-worker/secrets/worker.token
if [ -n "$DATABASE_REF" ]; then
  install -m 0640 -o root -g nexora-bw "$CONNECTION_FILE" "/etc/nexora-business-worker/secrets/$DATABASE_REF.conn"
fi

cat > /opt/nexora-business-worker/appsettings.json <<JSON
{
  "Nexora": {
    "ServerUrl": "${SERVER_URL%/}",
    "TenantId": "$TENANT_ID",
    "WorkerId": "$WORKER_ID",
    "SigningPublicKey": "$SIGNING_KEY",
    "TokenFile": "/etc/nexora-business-worker/secrets/worker.token",
    "SecretsDirectory": "/etc/nexora-business-worker/secrets",
    "PollSeconds": 30,
    "HeartbeatSeconds": 30,
    "AllowUntrustedServerCertificate": false,
    "AssumeUtcForUnspecified": true,
    "UpdateCheckMinutes": 10,
    "UpdateDirectory": "/var/lib/nexora-business-worker/updates"
  },
  "Logging": { "LogLevel": { "Default": "Information", "Microsoft": "Warning" } }
}
JSON

install -m 0644 "$(dirname "$0")/nexora-business-worker.service" /etc/systemd/system/nexora-business-worker.service
systemctl daemon-reload
systemctl enable --now nexora-business-worker.service
echo "Nexora Business Worker installed and started. The infrastructure agent was not touched."
echo "Logs: journalctl -u nexora-business-worker -f"
