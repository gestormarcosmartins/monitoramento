# Business Monitor — monitores de consulta (lado central) e contrato do Business Worker

**Base:** `Nexora_Business_Monitor_Especificacao_Oficial_v1_0.docx` (14/10/2026) · **Código:** `business_monitors.py`, `business_sql.py`, `business_results.py`, migration `051_business_query_monitors.sql`.
**Situação:** o lado central está implementado e testado contra PostgreSQL real (migrations 001–051, RLS forçada). O **Business Worker** (processo C# separado) e o **RabbitMQ** ainda **não** existem; este documento é o contrato para quem desenvolver o Worker.

## 1. O que já existe no backend

| Spec | Entrega | Evidência |
|---|---|---|
| P0-05 SQL read-only | Validador por AST (T-SQL e PostgreSQL, `sqlglot`): um único `SELECT`; bloqueia DML/DDL, `SELECT INTO`, `FOR UPDATE`, `EXEC`, CTE com DML, nomes de quatro partes (linked server), tabelas temporárias, `@@variáveis`, funções com efeito colateral/acesso externo. O texto que roda é o **normalizado pela AST** (sem divergência entre parser e banco) | `test_business_sql.py` |
| P0-06 JSON tabular | Colunas sempre presentes, uma linha = um objeto com todas as chaves, `null` preservado, vazio = sucesso com `rows: []`, decimal exato (texto), data/hora com fuso obrigatório, `truncated` só se o teto foi atingido | `test_business_results.py` |
| P0-07 Master + auditoria | Só o Master técnico cria/edita/testa/publica/suspende; cada ação grava `cm_audit_log` (hash da consulta, revisão). O TOTP já é obrigatório no realm Keycloak | integração |
| P0-04 (parte central) | Scheduler no worker central, execuções com lease, idempotência por `execution_id`, retentativas com backoff, expiração sem “tempestade”, circuit breaker (5 falhas) | integração |
| P0-08 | Resultado SUCCEEDED alimenta o indicador pelo mesmo pipeline do Business Monitor (série, estado, ocorrência, GLPI, push) | integração |
| Revisões assinadas | Ed25519 por job (liga a consulta a `execution_id` + worker; não pode ser reaproveitada) | `test_business_results.py` |
| Identidade do Worker | Credencial própria (`X-Worker-ID`/`X-Worker-Token`), um token por instalação, revogável; vinculada ao tenant | integração |

## 2. O que **não** está feito (e por quê)

1. **Business Worker em C#** (P0-01, P0-02, P0-03 do lado do cliente, P0-09): precisa do SDK .NET 8 e de homologação em Windows/Linux. Este documento define o contrato (seção 3).
2. **RabbitMQ + outbox publisher** (P0-04): não há broker provisionado no `compose.yaml`. Hoje a fila lógica é a tabela `cm_business_executions` (claim com `FOR UPDATE SKIP LOCKED` + lease). O contrato com o Worker **não muda** quando o broker entrar: o scheduler passará a publicar mensagens e o gateway as consumirá com ACK manual. **Desvio assumido:** a spec pede que o banco não substitua o broker; isto é uma etapa intermediária, a ser decidida e aprovada.
3. **Telas** do Nexora para monitores, execuções e workers (P1-01): só API.
4. **DLQ e reprocessamento** (P1-02), retenção/expurgo (P1-04; a coluna `retain_until` já existe, falta o job de expurgo), alerta ao responsável por perda de heartbeat (hoje o worker é marcado `OFFLINE` e vai para o log/auditoria).
5. Parâmetros dinâmicos: só `$today` (data no fuso da empresa) e `$now` (UTC); demais valores são fixos.

## 3. Contrato para o desenvolvedor do Business Worker (.NET 8)

Autenticação em **todas** as chamadas: `X-Tenant-ID`, `X-Worker-ID`, `X-Worker-Token` (o token é mostrado **uma vez** ao criar o worker em `POST /api/v1/master/business/workers`). HTTPS obrigatório. O Worker **nunca** acessa RabbitMQ nem o banco central.

| Rota | Uso |
|---|---|
| `GET /api/v1/agents/business/signing-key` | Chave pública Ed25519; **fixar na instalação** (pinning) e comparar |
| `POST /api/v1/agents/business/heartbeat` `{version}` | A cada ≤ 60 s, independente do agente de infraestrutura |
| `GET /api/v1/agents/business/jobs/claim?limit=1` | Devolve `{jobs:[…]}`; vazio se o worker já tem job ativo ou não há trabalho |
| `POST …/jobs/{id}/started` | Ao iniciar (exige lease válido) |
| `POST …/jobs/{id}/renew` | Até 2 renovações de 90 s |
| `POST …/jobs/{id}/result` | Resultado tabular (abaixo); responde `accepted`, `duplicate` ou erro |
| `POST …/jobs/{id}/failed` | `{status: FAILED|TIMED_OUT|REJECTED, error_code, error_message(≤500, sanitizada), retryable, duration_ms}` |

**Job recebido** (campos assinados): `execution_id, monitor_id, revision, tenant_id, worker_id, expires_at, lease_expires_at, database_ref, dialect, query, parameters, constraints{timeout_seconds,max_rows,max_bytes}, column_schema[], test, correlation_id` + `signature`.

**O Worker deve, antes de executar:** (1) verificar a assinatura Ed25519 sobre o JSON canônico do manifesto **sem** o campo `signature` (chaves ordenadas, separadores `,` e `:`, UTF-8, sem espaços); (2) conferir `tenant_id`/`worker_id` próprios e `expires_at` futuro; se falhar, responder `failed` com `status=REJECTED`; (3) resolver `database_ref` na **configuração local** (string de conexão e usuário/senha só-leitura nunca vêm da central).

**Execução:** parâmetros por *bind* (`@nome`), nunca concatenados; timeout e cancelamento do comando (`timeout_seconds`); ler no máximo `max_rows` + 1 linha para saber se houve truncamento; abortar acima de `max_bytes`; concorrência 1; limites de CPU/memória do processo; nenhum log de dados, SQL com parâmetros ou credenciais.

**Resultado:** `{started_at, duration_ms, columns, rows, row_count, truncated}` onde `columns` é **idêntico** a `column_schema` (mesma ordem), cada linha é **um objeto** com todas as colunas (`null` quando ausente), `decimal` como **texto** (`"1200.50"`), `date` ISO (`2026-10-14`), `datetime` ISO **com fuso**, `boolean` real. Resultado vazio: `rows: []`, `row_count: 0`. Violação do contrato → a central rejeita e conta como falha.

**Falhas e repetição:** se o Worker cair depois de executar e antes de enviar, o job é reentregue (lease expirado); como é leitura, repetir é seguro. A central consolida **um** resultado por `execution_id`; reenviar o mesmo resultado devolve `duplicate`; resultado diferente para execução fechada devolve `409`.

**Isolamento (critério de aceite crítico):** processo/serviço **separado** do agente de infraestrutura (duas unidades Windows Service/systemd), sem memória, fila, thread pool ou executor compartilhados. Matar o Worker no meio de uma consulta lenta não pode afetar heartbeat nem coleta da infraestrutura. O código atual de negócio dentro de `AgentWorker.cs`/`BusinessCollector.cs` deve ser extraído (P0-01), mantendo o agente de infraestrutura sem dependência dele.

## 4. Operação (Master)

1. `PUT /api/v1/business/enable` na empresa (já existente).
2. `POST /api/v1/master/business/workers` → guarda o `worker_token` (aparece uma vez).
3. `POST /api/v1/master/business/data-sources` `{name, worker_id, dialect, database_ref}`.
4. `POST /api/v1/master/business/monitors` (cria revisão 1 em rascunho; consulta validada e normalizada) → `…/revisions/1/test` → aguardar o Worker → `…/revisions/1/publish` (exige teste bem-sucedido). Editar = nova revisão (`POST …/revisions`), que precisa ser testada e publicada.
5. `POST …/monitors/{id}/status` `{action: suspend|resume|disable, reason}`; suspender/desativar cancela jobs ainda não iniciados.

Configuração do servidor: `BUSINESS_SIGNING_KEY` (seed Ed25519 em base64, 32 bytes; comando em `.env.example`). Sem ela, `publish` e `signing-key` respondem 503.

## 5. Como rodar os testes de integração
Os testes `test_integration_postgres.py` só rodam com um PostgreSQL **descartável** cujo dono seja um papel **sem** superusuário (superusuário ignora RLS):

```powershell
$env:NEXORA_TEST_DATABASE_URL = "postgresql://central_app:pw@localhost:5432/central_monitor"
python -m unittest -v services/backend/tests/test_integration_postgres.py
```
O banco precisa ter a extensão `pgcrypto` (usada pela migration 024).
