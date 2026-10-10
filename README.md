# Central Monitor

MVP multiempresa de monitoramento para os cinco clientes iniciais, dimensionado para uma VM Oracle com 2 OCPUs e 12 GB de RAM.

## O que esta implementado

- Painel web para clientes, ativos, monitores, alertas e incidentes.
- API FastAPI com OpenAPI, autenticacao OIDC/Keycloak e bypass apenas para desenvolvimento local.
- Isolamento por cliente com memberships, perfis e Row Level Security no PostgreSQL.
- Monitores HTTP, TCP e dead-man de eventos; monitores Zabbix ficam delegados ao Zabbix.
- Worker que agenda coletas, deduplica alertas, abre incidentes e resolve ambos na recuperacao.
- Entrada idempotente de eventos customizados.
- Zabbix Server 7.4 como motor interno de monitoramento.
- GLPI 11 como motor interno de incidentes, SLA e atendimento.
- Sincronizacao automatica de abertura, andamento e resolucao dos incidentes Nexora com chamados GLPI.
- Gestao operacional de incidentes com prioridade, equipe, responsavel, comentarios, historico e repasse a terceiros.
- Politicas de SLA por prioridade, prazos de resposta/solucao, pausa de atendimento e indicadores de violacao.
- Cadastro de fornecedores, equipes, janelas de manutencao e regras de notificacao por e-mail ou webhook.
- Provisionamento automatico de monitores HTTP no Zabbix, mantendo coleta local de contingencia.
- Automacoes homologadas com permissao por ativo, limite horario, cooldown e auditoria.
- Relatorios de MTTA, MTTR, SLA e fornecedores com exportacao PDF e Excel.
- Perfis admin, gestor, operador, visualizador, cliente e auditor com isolamento por empresa.
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
- Zabbix e GLPI nao possuem portas publicadas; sao acessados apenas pela API do Nexora na rede privada.
- Keycloak Admin: `http://localhost:8090/auth/admin`

O ambiente local exige login pelo Keycloak (`DEV_AUTH_BYPASS=false`). O primeiro administrador operacional deve ser criado no realm `central-monitor`. O bypass pode ser habilitado apenas temporariamente para diagnostico local. Antes de publicar na Oracle, ajuste o dominio no issuer/realm e use HTTPS.

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
- Os consoles do Zabbix e GLPI nao sao expostos aos usuarios; manutencao excepcional deve ocorrer por acesso administrativo temporario e controlado.
- O worker usa uma fila transacional com retentativas para sincronizar incidentes com o GLPI sem bloquear as coletas.
- A API do GLPI aceita apenas os enderecos internos fixos da API e do worker; a faixa dinamica Docker e separada para impedir colisoes.
- O arquivo `.env` nao entra no Git.

Antes da implantacao publica ainda sao obrigatorios: DNS e TLS, firewall/NSG da Oracle, backups testados, SMTP/webhook de notificacao e troca das credenciais iniciais do Zabbix, GLPI e Keycloak.

