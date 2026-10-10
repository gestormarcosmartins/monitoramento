# Central Monitor

MVP multiempresa de monitoramento para os cinco clientes iniciais, dimensionado para uma VM Oracle com 2 OCPUs e 12 GB de RAM.

## O que esta implementado

- Painel web para clientes, ativos, monitores, alertas e incidentes.
- API FastAPI com OpenAPI, autenticacao OIDC/Keycloak e bypass apenas para desenvolvimento local.
- Isolamento por cliente com memberships, perfis e Row Level Security no PostgreSQL.
- Monitores HTTP, TCP e dead-man de eventos; monitores Zabbix ficam delegados ao Zabbix.
- Worker que agenda coletas, deduplica alertas, abre incidentes e resolve ambos na recuperacao.
- Entrada idempotente de eventos customizados.
- Zabbix Server 7.4 e console Zabbix Web.
- Keycloak 26.5 com realm e cliente PKCE importados.
- Um PostgreSQL 17 com bases separadas para Central, Zabbix e Keycloak.
- Redis, outbox transacional e trilha de auditoria.
- Orquestrador de acoes separado, com catalogo fechado. A API exige grant por ativo, risco baixo, idempotencia, limite horario e cooldown. A execucao remota permanece desligada por padrao ate existir um agente homologado no cliente.

## Iniciar no Windows

Com o Docker Desktop aberto, execute:

```powershell
cd "C:\Users\Marcos Martins\Documents\ChatGPT\Monitoramento Benner\central_monitor"
.\scripts\bootstrap.ps1
```

O script cria `.env` com segredos aleatorios na primeira execucao e sobe toda a pilha.

- Central Monitor: `http://localhost:8090`
- Documentacao da API: `http://localhost:8090/api/docs`
- Zabbix: `http://localhost:8081` (`Admin` / `zabbix` no primeiro acesso)
- Keycloak Admin: `http://localhost:8090/auth/admin`

O ambiente local inicia com `DEV_AUTH_BYPASS=true`, portanto o painel pode ser testado sem login. Antes de publicar na Oracle, altere para `false`, ajuste o dominio no issuer/realm e use HTTPS.

Para zerar apenas os dados desta Central e recriar os volumes:

```powershell
.\scripts\bootstrap.ps1 -ResetData
```

## Primeiro teste

1. Abra a Central e crie um cliente.
2. Cadastre um ativo do tipo Website ou API.
3. Crie um monitor HTTP com uma URL publica, ou TCP com host e porta.
4. Aguarde o ciclo do worker e clique em **Atualizar**.
5. Use uma URL/porta indisponivel para validar a abertura automatica de alerta e incidente.

O intervalo minimo de monitor e 30 segundos. O worker consulta os vencimentos a cada 10 segundos no ambiente local.

## API de eventos

Envie `X-Tenant-ID` e, no modo local, `X-Dev-User`. O `eventId` torna a recepcao idempotente.

```json
{
  "eventId": "backup-2026-10-10-001",
  "source": "backup-agent",
  "eventType": "backup.completed",
  "occurredAt": "2026-10-10T12:00:00Z",
  "severity": "info",
  "attributes": {"result": "ok"}
}
```

## Operacao e seguranca

- Nao ha endpoint para shell, PowerShell ou comando arbitrario.
- Tokens do Zabbix ficam somente no backend.
- Series historicas permanecem no Zabbix; a Central armazena inventario, estados, alertas, incidentes e auditoria.
- PostgreSQL e Redis nao publicam portas no host.
- O console Zabbix esta ligado apenas ao localhost; na Oracle ele deve ficar atras de VPN, bastion ou allowlist.
- O arquivo `.env` nao entra no Git.

Antes da implantacao publica ainda sao obrigatorios: DNS e TLS, firewall/NSG da Oracle, backups testados, SMTP/webhook de notificacao e troca das credenciais iniciais do Zabbix e Keycloak.

