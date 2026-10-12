# Central Monitor

MVP multiempresa de monitoramento para os cinco clientes iniciais, dimensionado para uma VM Oracle com 2 OCPUs e 12 GB de RAM.

O desenvolvimento funcional e arquitetural deve seguir `docs/NEXORA_DEVELOPMENT_BLUEPRINT.md`, com execução pelos cortes de `docs/NEXORA_DEVELOPMENT_SLICES.md`. O padrão obrigatório das telas permanece em `docs/NEXORA_FRONTEND_STANDARD.md`.

O andamento verificável de cada corte fica em `docs/NEXORA_IMPLEMENTATION_STATUS.md`.

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
- Keycloak 26.5 com realm, cliente PKCE S256, validação de nonce, TOTP obrigatório, rotação de refresh token e limites de sessão importados.
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

O ambiente local exige login pelo Keycloak (`DEV_AUTH_BYPASS=false`). O primeiro administrador operacional deve ser criado no realm `central-monitor` e todos os usuários configuram TOTP no primeiro login. O bypass pode ser habilitado apenas temporariamente para diagnostico local. Antes de publicar na Oracle, ajuste o domínio no issuer/realm, use HTTPS e conclua a migração de sessão para BFF descrita em `docs/ADR_SESSION_SECURITY.md`.

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

## Integração Benner Health Monitor

O endpoint `/api/v1/agents/benner/snapshots` recebe os 19 diagnósticos SQL Server, preserva os resultados, cria o ativo e seus monitores e transforma novas falhas em alertas, incidentes, SLA e chamados GLPI.

Defina `BENNER_INGEST_TOKEN` no `.env`. Na primeira carga use `--historical` para não abrir incidentes retroativos:

```powershell
python tools/benner_shipper.py "..\benner_health_monitor\logs\archive" --tenant UUID_DA_EMPRESA --token SEU_TOKEN --historical
```

Nas coletas seguintes envie o novo JSON sem `--historical`. O endpoint é idempotente e ignora snapshots já recebidos.

# Endereços parametrizáveis

Nenhum agente precisa ser recompilado quando a Central mudar de endereço. Configure no `.env`:

```env
# Ambiente local
CENTRAL_PUBLIC_URL=http://localhost:8090
CENTRAL_BIND_ADDRESS=127.0.0.1
CENTRAL_HTTP_PORT=8090
BENNER_MONITOR_UPSTREAM=http://host.docker.internal:8091
OIDC_ISSUER=http://localhost:8090/auth/realms/central-monitor
```

Na Oracle, altere somente os valores, por exemplo:

```env
CENTRAL_PUBLIC_URL=https://monitoramento.seudominio.com.br
CENTRAL_BIND_ADDRESS=0.0.0.0
CENTRAL_HTTP_PORT=8090
BENNER_MONITOR_UPSTREAM=http://endereco-interno-do-monitor:8091
OIDC_ISSUER=https://monitoramento.seudominio.com.br/auth/realms/central-monitor
```

O futuro agente e o transportador atual usam as variáveis `NEXORA_SERVER_URL`,
`NEXORA_TENANT_ID` e `NEXORA_AGENT_TOKEN`. Os argumentos de linha de comando
continuam disponíveis para sobrescrever esses valores quando necessário.

## Agente de coleta Windows

A Central possui APIs para cadastro, autenticação individual, heartbeat, configuração remota, snapshots, eventos e logs. Cada empresa e cada instalação têm credenciais isoladas.

O código em `agent/Nexora.Agent` lê os JSONs do Benner Health Monitor, relaciona os diagnósticos aos monitores e mantém uma fila local em `C:\ProgramData\Nexora\Agent\queue` quando a internet estiver indisponível. O agente só abre conexões de saída para a Central.

### Coleta do Chaveou em servidor Linux externo

O mesmo agente pode coletar a Torre de Controle do Chaveou diretamente do PostgreSQL local e enviar os estados para monitores `push` cadastrados no cliente Chaveou. O relacionamento é feito por `configuration.sourceCode`, sem depender dos IDs internos entre os dois sistemas.

O pacote Linux fica em `agent/dist/Nexora-Agent-linux-x64.tar.gz`. No servidor do Chaveou, extraia o pacote, copie `agent.env.example` para um arquivo temporário `agent.env`, preencha uma conexão PostgreSQL restrita ao banco local e o `public_id` do tenant e instale:

```bash
tar -xzf Nexora-Agent-linux-x64.tar.gz
cp agent.env.example agent.env
chmod 600 agent.env
# Edite agent.env sem registrar os segredos no histórico do shell.
sudo ./install-agent.sh --config "$PWD/agent.env"
```

O ciclo padrão é de 30 segundos. A cada ciclo o agente:

1. envia heartbeat para a Central;
2. lê apenas os serviços habilitados de `mo_services` no tenant configurado;
3. envia estado, código do resultado, latência e horário da última verificação;
4. mantém o snapshot na fila local se a Central estiver temporariamente indisponível.

A string de conexão não é enviada para a Central nem escrita em logs. O instalador cria o usuário de sistema `nexora-agent`, guarda a configuração como `/etc/nexora-agent/agent.env` com leitura restrita e mantém credenciais e fila offline em `/var/lib/nexora-agent`. O serviço é instalado como `nexora-agent.service` e pode ser acompanhado com `journalctl -u nexora-agent -f`.

Instalação:

1. Em **Administração > Agentes de coleta**, gere uma chave de uso único.
2. Na máquina de compilação com o SDK .NET 8, execute `agent\build-agent.ps1`.
3. Em PowerShell como administrador no servidor monitorado, execute:

```powershell
.\install-agent.ps1 -ServerUrl "https://monitoramento.suaempresa.com" -EnrollmentToken "CHAVE_GERADA" -AgentName "SQL Morada" -SnapshotDirectory "C:\caminho\archive"
```

Para o teste local, use `http://localhost:8090`. Na Oracle, troque somente `ServerUrl` pelo endereço público HTTPS. Também são aceitas as variáveis `NEXORA_SERVER_URL`, `NEXORA_ENROLLMENT_TOKEN`, `NEXORA_AGENT_NAME` e `NEXORA_SNAPSHOT_DIRECTORY`.

## Validação do frontend

O build Docker do serviço `web` executa, antes de produzir a imagem Nginx:

1. testes de contrato com `npm test`;
2. build Vite de produção;
3. matriz Playwright/Chromium com `npm run test:responsive` em 320, 360, 390, 768, 1024, 1366 e 1920 px.

O gate responsivo falha quando o shell ou o conteúdo não aparece, existe overflow horizontal, o botão móvel principal não possui alvo mínimo de 44 px, o foco por teclado não está visível, faltam landmarks acessíveis, o menu não abre por Enter/fecha por Escape ou o axe-core encontra violação WCAG séria ou crítica.

