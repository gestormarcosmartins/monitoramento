# Business Monitor — monitores de consulta: central, RabbitMQ e Business Worker

**Base:** `Nexora_Business_Monitor_Especificacao_Oficial_v1_0.docx` (14/10/2026).
**Código:** central (`business_monitors.py`, `business_sql.py`, `business_results.py`, `business_broker.py`, migrations 051–052), Worker (`agent/Nexora.BusinessWorker`), testes (`agent/Nexora.BusinessWorker.Tests`, `services/backend/tests`), ferramentas (`tools/`).

## 1. Situação por item da spec (backlog P0)

| Item | Situação | Evidência |
|---|---|---|
| P0-01 Extrair negócio do `AgentWorker` | **Feito.** O agente de infraestrutura não referencia mais nenhum código de negócio (coletor, fila SQLite, leitor, extensões de cliente); pacotes do `csproj` devolvidos ao estado original | compila sem avisos; `grep -i business agent/Nexora.Agent` vazio |
| P0-02 Executável e serviços separados | **Feito (código).** `Nexora.BusinessWorker` é outro executável, com instaladores próprios para Windows Service e systemd (conta de serviço de privilégio mínimo, reinício automático, limites de memória/CPU, prioridade baixa) | publish single-file executado; instaladores **não executados** (exigem root/administrador) |
| P0-03 Identidade e heartbeat próprios | **Feito.** Credencial por instalação (`X-Worker-*`), revogável; heartbeat em serviço independente do loop de jobs; central marca `OFFLINE` | testes de integração + E2E |
| P0-04 RabbitMQ + scheduler/outbox + gateway | **Feito, com ressalva.** Outbox transacional → publicação com *confirm* → fila por worker com DLX/DLQ → claim consome, concede lease no banco e só então dá ACK. Sem `RABBITMQ_URL` opera em modo `db` | testes de integração em PostgreSQL real com um *double* do broker; cliente `pika` testado só com simulação. **Nunca foi executado contra um RabbitMQ real** (ver 3) |
| P0-05 SQL read-only | **Feito.** Validação por AST (T-SQL e PostgreSQL), texto normalizado, TLS obrigatório, sessão somente leitura, timeout e cancelamento | `test_business_sql.py`, `PostgresExecutorTests` |
| P0-06 JSON tabular tipado | **Feito.** Colunas fixas, nulos, vazio, erro, truncamento, decimal exato, data com fuso | testes Python e C# |
| P0-07 Master único e auditoria | **Feito.** Só o Master técnico governa; rascunho → teste → publicação; auditoria; TOTP no Keycloak | integração |
| P0-08 Regras e alertas | **Feito** reaproveitando o motor existente (estado, ocorrência, SLA, GLPI, push) | integração + E2E |
| P0-09 Instalador/upgrade/rollback | **Feito (código e testes), não executado como serviço real.** Atualização remota assinada com anel canário e reversão (seção 7). Instaladores e build independentes | testes Python/C#, scripts testados em pastas temporárias, `tools/e2e_business_worker.py` (download real por HTTP) |
| P0-10 Testes E2E e de falha | **Parcial.** H03, H04, H07, H09, H11 (parcial) e H12 cobertos; **H01, H05, H06, H08, H10 não executados** (ver 4) | `tools/e2e_business_worker.py` |

## 2. O que mudou na arquitetura de execução
- A central **não** guarda nem conhece senha de banco: o job traz só `database_ref`; a string de conexão é resolvida no cliente (variável `NEXORA_BW_DB_<REF>` ou arquivo em diretório protegido).
- Cada job é **assinado por job** (Ed25519) sobre o JSON canônico e entregue como `{"payload": "<json>", "signature": "<base64>"}`. O Worker verifica a assinatura **sobre os bytes exatos** e só então interpreta o payload, sem reimplementar canonicalização. A chave pública é fixada na instalação (*pinning*).
- A consulta que roda é o texto **normalizado pela AST**, não o digitado.
- Falhas só entram como "tentar de novo" quando são transitórias; tudo é limitado (3 tentativas, 2 renovações de lease, circuit breaker de 5 falhas).

## 3. O que ainda **não** foi provado (leia antes de homologar)
1. **RabbitMQ real.** O Docker não funciona neste computador (WSL ausente). O cliente `pika` foi testado com objetos simulados e a lógica ao redor com um *double* que reproduz o contrato (fila por worker, confirm, DLQ, redelivery), mas topologia, `mandatory`, expiração por mensagem e DLX só se provam com um broker. **Rode `tools/rabbitmq_smoke.py` num ambiente com RabbitMQ antes de ligar `RABBITMQ_URL`.**
2. **SQL Server.** O executor usa o mesmo código para os dois bancos, mas só PostgreSQL foi exercitado. `ApplicationIntent=ReadOnly`, certificado e tipos `datetime` precisam de teste com um SQL Server real.
3. **Windows Service e systemd.** Os instaladores têm sintaxe validada (PowerShell e `bash -n`), mas não foram executados.
4. **Isolamento (H01).** Dois processos independentes, com limites próprios, mas não houve o teste crítico "matar o Worker durante uma consulta lenta com o agente de infraestrutura em operação". Exige o agente real.
5. **Carga e desempenho** (H08): nada foi medido; os números da spec seguem como valores iniciais sugeridos.
6. **Telas** (P1-01) e **DLQ com reprocessamento** (P1-02): **feitas** (seção 8); o que falta é validar as telas no navegador com uma sessão do Master e do administrador. O formulário antigo de coletores por HUB virou histórico somente leitura.

## 4. Como testar
```powershell
# Backend (unitários); com PostgreSQL descartável, também a integração:
$env:NEXORA_TEST_DATABASE_URL = "postgresql://central_app:pw@localhost:5432/central_monitor"   # papel SEM superusuário; extensão pgcrypto instalada
python -m unittest discover -s services/backend/tests

# Worker (.NET 8): 53 testes; 7 usam PostgreSQL real quando NEXORA_TEST_PG está definido
$env:NEXORA_TEST_PG = "Host=127.0.0.1;Port=5432;Database=erp_demo;Username=postgres"
dotnet test agent/Nexora.BusinessWorker.Tests

# Ponta a ponta (API + PostgreSQL + Worker real); precisa de: pip install pixeltable-pgserver
dotnet build agent/Nexora.BusinessWorker -c Release
python tools/e2e_business_worker.py --dotnet "C:\caminho\dotnet.exe"

# Se mudar a assinatura/canonicalização no servidor, regenere os vetores usados pelo C#:
python tools/gen_worker_vectors.py
```

## 5. Operação
**Master (por empresa):** habilitar o produto → criar worker (`POST /api/v1/master/business/workers`, o `worker_token` aparece **uma vez**) → fonte (`/data-sources` com `dialect` e `database_ref`) → monitor (`/monitors`; consulta validada e normalizada) → teste (`…/revisions/1/test`) → publicação (`…/publish`, exige teste bem-sucedido). Editar = nova revisão. `…/status` suspende, retoma ou desativa (cancela jobs ainda não iniciados). `GET …/broker/status` e `…/broker/dead-letters` mostram fila e mensagens mortas da empresa.

**Servidor:** `BUSINESS_SIGNING_KEY` (seed Ed25519 em base64, 32 bytes) é obrigatória para publicar; `RABBITMQ_URL` liga o broker (o `compose.yaml` já traz o serviço `rabbitmq` na rede privada; sem a variável o modo é `db`).

**Cliente (servidor monitorado):**
1. `agent\build-business-worker.ps1 -Runtime win-x64` (ou `linux-x64`) gera o executável autocontido e roda os testes.
2. Obtenha a chave pública (`GET /api/v1/agents/business/signing-key`, com os cabeçalhos do worker) e confirme-a com o Master.
3. Windows: `install-business-worker.ps1 -ServerUrl … -TenantId … -WorkerId … -Token (Read-Host -AsSecureString) -SigningPublicKey … -DatabaseRef erp_principal -ConnectionString "…"` · Linux: `install-business-worker.sh --server-url … --token-file … --signing-key … --database-ref … --connection-file …`.
4. Crie no banco do ERP um login **somente leitura** com `SELECT` apenas nas views/tabelas autorizadas. É a proteção principal; o resto é defesa em profundidade.
5. Teste TLS válido no banco. A opção `AllowUntrustedServerCertificate` existe só para homologação; `AllowPlaintextLoopbackDatabase` só para desenvolvimento no mesmo servidor.

## 6. Contrato do Worker (referência)
Cabeçalhos em todas as chamadas: `X-Tenant-ID`, `X-Worker-ID`, `X-Worker-Token`.

| Rota | Uso |
|---|---|
| `GET /api/v1/agents/business/signing-key` | Chave pública Ed25519 (para fixar na instalação) |
| `POST …/heartbeat` `{version}` | ≤ 60 s, independente do loop de jobs |
| `GET …/jobs/claim?limit=1` | `{jobs:[{payload, signature}]}`; vazio se o worker já tem job ativo ou não há trabalho; **503** se o broker estiver fora (o Worker apenas tenta de novo) |
| `POST …/jobs/{id}/started` · `/renew` | Início e até 2 renovações de 90 s |
| `POST …/jobs/{id}/result` | `{started_at, duration_ms, columns, rows, row_count, truncated}` → `accepted`, `duplicate` ou erro |
| `POST …/jobs/{id}/failed` | `{status: FAILED|TIMED_OUT|REJECTED, error_code, error_message, retryable, duration_ms}`; nunca texto de driver, SQL ou valores |

Resultado: `columns` idêntico a `column_schema`; cada linha é **um objeto** com todas as colunas (`null` quando ausente); `decimal` como texto; `date` ISO; `datetime` ISO **com fuso**; vazio = `rows: []`. Violação do contrato → a central rejeita e conta como falha. Reenvio do mesmo resultado → `duplicate`; resultado diferente para execução fechada → `409`.

Isolamento: o Worker nunca fala com RabbitMQ nem com o banco central; sem API, ele só para de rodar consultas e reconecta.

## 7. Atualização remota assinada do Worker, com anel canário
**Fluxo.** A operação gera o pacote (`agentuild-business-worker.ps1 -Runtime <win-x64|linux-x64> -Package` → `disteleases
exora-business-worker-<versão>-<plataforma>.zip|.tar.gz`, sem o `appsettings.json`) e o copia para a pasta `releases/` do servidor (montada somente leitura na API em `/data/releases`). O Master **registra** a versão (`POST /api/v1/master/business/releases` com `version`, `platform`, `file_name`): o servidor calcula SHA-256 e tamanho e a versão nasce no canal **canary**.

**Quem recebe o quê** (`business_releases.select_update`): cada worker tem um **anel** (`PUT /master/business/workers/{id}/update-ring`): `canary` recebe versões *canary* e *stable*; `stable` (padrão) só recebe *stable*; `hold` congela. Uma versão só vira *stable* com `POST …/releases/{id}/promote`, que **exige** ao menos um worker canário online que reportou `APPLIED` para ela. `POST …/releases/{id}/revoke` impede novas ofertas e faz os workers que a executam voltarem à última versão estável (`rollback=true`).

**Confiança.** O worker pergunta (`GET /agents/business/update?platform=`) e recebe um manifesto **assinado (Ed25519)** e ligado à Empresa, ao worker, à plataforma, ao SHA-256 e ao tamanho, válido por 1 h. Ele verifica com a chave fixada na instalação, baixa pela API (`/releases/{versão}/{plataforma}/download`, só o que lhe foi oferecido), confere tamanho e hash **durante e depois** do download e apenas **prepara** o pacote (`updates/ready/{pending.json,package.pkg}`). Nunca troca o próprio binário.

**Aplicação.** Preparado o pacote, o worker espera acabar a consulta em andamento e sai com código **75**: o gerenciador de serviço o reinicia. Antes de iniciar, o script do instalador (`apply-business-worker-update.sh` via `ExecStartPre=+` no systemd; `apply-business-worker-update.ps1` pela ação de falha do serviço no Windows) pede ao binário **instalado** que **reverifique** assinatura, vínculo, tamanho e hash (`Nexora.BusinessWorker --verify-staged`), recusa arquivos com link simbólico, caminho absoluto ou `..`, preserva o `appsettings.json` local, troca as pastas guardando a anterior e inicia uma **probation**: se a versão nova não confirmar saúde (primeiro heartbeat bem-sucedido → arquivo `confirmed`) em 3 inícios, a anterior é restaurada e o central recebe `ROLLED_BACK`. O script nunca impede o serviço de iniciar. O worker informa `DOWNLOADED`, `APPLIED`, `ROLLED_BACK` ou `FAILED` ao central (`POST /agents/business/update/status`). `UpdateCheckMinutes: 0` desliga o recurso.

**Prova e limites.** Manifestos gerados pelo código real do servidor são verificados pelo C# (assinatura, vínculo, expiração, rebaixamento, repetição); o E2E sobe API + worker real e prova oferta só ao anel canário, download por HTTP, hash idêntico, saída 75 e reverificação (e recusa de pacote trocado). Os scripts de aplicação foram testados em pastas temporárias com um binário falso (`agent/tests/test_apply_update.sh` e `Test-ApplyUpdate.ps1`). **Não foi executado:** o ciclo completo como serviço real (systemd/Windows), nem a troca de um binário publicado de verdade. Faça esse ensaio numa máquina de teste antes de promover uma versão para clientes.

## 8. Telas (Monitoramento de Negócios)
Páginas dedicadas e endereçáveis (sem modais), em `/negocios/…`: **Consultas** (lista, nova, revisão, testar, publicar, suspender/retomar/desativar, execuções e resultado), **Workers** (criar com token exibido uma única vez, anel de atualização, revogar), **Fontes**, **Filas** (mensagens mortas: reprocessar/descartar com motivo) e **Atualizações** (registrar, ativar/promover, revogar). O Master vê só governança (metadados, sem linhas de negócio); resultados e execuções exigem `business.view`. O **Primeiro acesso** mostra o que falta (produto, administrador, permissões do Master) e o administrador pode conceder o acesso padrão ao Master em um passo.

**Fila de mensagens mortas** (`POST /master/business/broker/dead-letters/{execution_id}/reprocess|discard`): reprocessar cria uma **nova** execução (a original fica como evidência), recusa quando o monitor não rodaria de qualquer forma (desativado, outra revisão, uma execução já em andamento) e remove a mensagem morta; descartar só remove.

