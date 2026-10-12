#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Execute como root: sudo ./install-agent.sh --config /caminho/agent.env" >&2
  exit 1
fi

if [[ ${1:-} != "--config" || -z ${2:-} || ! -f $2 ]]; then
  echo "Uso: sudo ./install-agent.sh --config /caminho/agent.env" >&2
  exit 1
fi

source_config=$(readlink -f "$2")
for key in NEXORA_SERVER_URL NEXORA_ENROLLMENT_TOKEN NEXORA_CHAVEOU_DATABASE_URL NEXORA_CHAVEOU_TENANT_ID; do
  if ! grep -q "^${key}=." "$source_config"; then
    echo "Configuração obrigatória ausente: ${key}" >&2
    exit 1
  fi
done

id nexora-agent >/dev/null 2>&1 || useradd --system --home-dir /var/lib/nexora-agent --shell /usr/sbin/nologin nexora-agent
install -d -o root -g root -m 0755 /opt/nexora-agent
install -d -o root -g nexora-agent -m 0750 /etc/nexora-agent
install -d -o nexora-agent -g nexora-agent -m 0750 /var/lib/nexora-agent
install -o root -g root -m 0755 "$(dirname "$0")/Nexora.Agent" /opt/nexora-agent/Nexora.Agent
install -o root -g nexora-agent -m 0640 "$source_config" /etc/nexora-agent/agent.env

cat >/etc/systemd/system/nexora-agent.service <<'UNIT'
[Unit]
Description=Nexora Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=nexora-agent
Group=nexora-agent
EnvironmentFile=/etc/nexora-agent/agent.env
ExecStart=/opt/nexora-agent/Nexora.Agent
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/nexora-agent

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now nexora-agent.service
systemctl --no-pager --full status nexora-agent.service
