# Proposta: endpoint consolidado de chamados

Status: **aprovada e implementada no backend** em 12/10/2026 (`consolidated_tickets.py`, migration 039 e
`tests/test_http_consolidated_tickets.py`). Falta a adoção pelo app mobile e pela web. Ainda não validada contra o
PostgreSQL real (os testes emulam o banco de cada empresa).

## Rastreabilidade

- Corte: CD-05 Central de chamados completa (herda CD-01, CD-02 e CD-04).
- Histórias: NX-019 e NX-020; regras RN-010 a RN-015; telas T02 a T05; aceites AT-05, AT-06, AT-12 a AT-14, AT-17 e AT-24 do CD-05.
- Regra do blueprint: *"Consolidado somente em leitura; toda escrita exige empresa concreta."*

## Problema

O usuário `master` (ou com grants em várias empresas) precisa **ver e tratar os chamados de todas as empresas**.
Hoje `GET /api/v1/tickets` exige `X-Tenant-ID` com uma empresa só. Por isso o app mobile faz uma requisição por empresa
e junta tudo no aparelho, com estes limites:

- no máximo 100 chamados por empresa, sem paginação;
- N requisições por atualização, cada uma passando pela autorização completa;
- métricas do Início calculadas sobre esses 100, o que é incorreto quando há mais;
- falha de uma empresa não é descrita de forma estruturada;
- a web não tem "Todas as empresas" na lista de chamados.

## Restrições que o desenho respeita

1. **Isolamento por empresa é por conexão.** `tenant_connection()` faz `set_config('app.tenant_id', …)` e o RLS filtra por isso.
   Uma única consulta SQL não enxerga duas empresas. Portanto a consolidação é feita **na aplicação**, empresa por empresa,
   **nunca** com `UNION`, role com `BYPASSRLS` ou desligando o RLS. O painel (`/api/v1/dashboard`) já usa esse modelo.
2. **Autorização por empresa:** papel e `row_scope` (`own`, `department`, `team_service`, `company`) vêm do grant de cada empresa
   e são aplicados com `incident_scope()` em cada consulta. A consolidação não pode ampliar o que o usuário vê em nenhuma empresa.
3. **Somente leitura.** Detalhe e escritas continuam em `/api/v1/tickets/{id}/…` com `X-Tenant-ID` = `tenant_id` do item.
4. O pool é compartilhado com worker e orquestrador (`DATABASE_POOL_MAX_SIZE`, padrão 12): o fan-out precisa ter concorrência limitada.

## Contrato proposto

### `GET /api/v1/tickets/consolidated`

Autenticação por Bearer. **Não usa** `X-Tenant-ID` (se vier, é ignorado).

| Parâmetro | Descrição |
|---|---|
| `tenant_ids` | Opcional, lista separada por vírgula. Interseção com as empresas autorizadas; ids sem acesso são **ignorados em silêncio** (sem revelar existência). Padrão: todas as autorizadas. Máximo de 20 empresas por chamada |
| `q`, `status`, `priority`, `actionable`, `mine`, `team_id`, `assignee_user_id` | Mesmos filtros e mesma semântica de `GET /api/v1/tickets` |
| `page_size` | 1 a 100 (padrão 25) |
| `cursor` | Opaco, devolvido em `next_cursor`. Ausente na primeira página |

Resposta `200`:

```json
{
  "items": [
    { "id": "…", "tenant_id": "…", "tenant_name": "Chaveou", "title": "…", "status": "in_progress",
      "priority": "high", "sync_status": "pending", "updated_at": "2026-10-12T13:05:00Z", "version": 4 }
  ],
  "page_size": 25,
  "next_cursor": "eyJ1IjoiMjAyNi0xMC0xMlQxMzowNTowMFoiLCJpIjoiLi4uIn0",
  "total": 137,
  "totals_by_tenant": [ { "tenant_id": "…", "tenant_name": "Chaveou", "total": 52 } ],
  "tenants_failed": [ { "tenant_id": "…", "tenant_name": "Morada", "code": "timeout", "retryable": true } ],
  "partial": true,
  "read_only": true,
  "data_as_of": "2026-10-12T13:06:10Z",
  "sync_state": "projected",
  "filters_applied": { "q": null, "status": null, "priority": null, "actionable": true }
}
```

- `partial: true` quando alguma empresa falhou. A resposta continua `200` com as demais, e a empresa faltante vem em `tenants_failed`
  (não é "tudo ou nada"). Se **todas** falharem, retorna `503` no formato de erro padrão (`correlation_id`, `retryable`).
- Sem nenhuma empresa autorizada: `200` com lista vazia e `total: 0`.
- Cada item mantém os campos do `GET /tickets` e acrescenta `tenant_id` e `tenant_name`.

### `GET /api/v1/tickets/consolidated/summary`

Mesmos filtros de empresa. Devolve os totais **corretos no servidor** para as métricas do Início/T02, sem depender da página carregada:

```json
{ "totals": { "total": 137, "open": 88, "critical": 6, "overdue": 9, "unassigned": 14, "pending_sync": 3 },
  "by_tenant": [ { "tenant_id": "…", "tenant_name": "Chaveou", "open": 40, "critical": 2 } ],
  "tenants_failed": [], "partial": false, "data_as_of": "…" }
```

Os critérios de cada contador são os mesmos de `GET /api/v1/tickets/summary`.

## Algoritmo

1. Resolver o usuário **uma vez** e carregar os grants das empresas pedidas, com `role` e `row_scope`.
   Hoje `authorize()` faz um upsert do usuário e toma `pg_advisory_xact_lock` a cada chamada; repetir isso N vezes serializa.
   Criar um helper `authorize_many(identity, tenant_ids)` que faz isso uma vez e consulta o grant de cada empresa.
2. Para cada empresa permitida, em paralelo com **concorrência máxima 4** (`ThreadPoolExecutor`) e timeout por empresa (3 s):
   `tenant_connection(id)` + a mesma consulta de `list_tickets`, acrescida de
   `AND (i.updated_at, i.id) < (%s, %s)` quando há cursor, `ORDER BY i.updated_at DESC, i.id DESC LIMIT page_size + 1`.
3. Mesclar as listas por `(updated_at, id)` decrescente e cortar em `page_size`. O cursor é `(updated_at, id)` do último item devolvido.
4. `total` e `totals_by_tenant`: `count(*)` por empresa, na primeira página (sem cursor); nas seguintes são omitidos
   ou repetidos a partir do que o cliente já tem.

**Por que cursor (keyset) e não `page`/`offset`:** com offset, a página 5 exigiria buscar `5 × page_size` linhas de cada empresa e descartar a maior parte.
Com cursor, cada página custa no máximo `N × (page_size + 1)` linhas, qualquer que seja a profundidade, e não "pula" nem repete itens quando o chamado é atualizado
entre uma página e outra (o desempate por `id` evita ambiguidade quando dois chamados têm o mesmo `updated_at`).

## Desempenho e índice

O índice atual `ix_cm_tickets_queue (tenant_id, status, priority, updated_at DESC)` não atende a ordenação sem filtro de `status`.
Nova migration (**039**, após a 038 já existente):

```sql
CREATE INDEX IF NOT EXISTS ix_cm_tickets_cursor
  ON cm_incidents (tenant_id, updated_at DESC, id DESC) WHERE inactive_at IS NULL;
```

Limites: 20 empresas por chamada, `page_size` ≤ 100, concorrência 4, timeout por empresa. Com 3 a 5 empresas é uma ida ao banco por empresa.

## Segurança e auditoria

- Empresa fora dos grants nunca é consultada; `tenant_ids` é só um filtro dentro do conjunto autorizado.
- O `cursor` não carrega permissões: é só posição. Cada página reautoriza do zero (grant revogado vale na página seguinte).
- Endpoint registrado como `read_only` no OpenAPI. Nenhum `POST`/`PATCH` consolidado é criado.
- Observabilidade: log com `correlation_id`, empresas consultadas, empresas com falha e duração por empresa; métrica de latência por empresa.

## Impacto nos clientes

- **App mobile:** "Todas as empresas" passa a usar este endpoint (uma chamada, `next_cursor` para "carregar mais"), mostra `partial`/`tenants_failed`
  em vez de alertas soltos e usa `/summary` nas métricas do Início. Abrir e operar um chamado continua usando o `tenant_id` do item.
- **Web:** pode oferecer "Todas as empresas" na lista T02 reaproveitando o mesmo contrato, respeitando o padrão de lista
  (identificação, métricas, filtros, grid de largura total, paginação).
- Compatível com o app atual: `GET /api/v1/tickets` não muda.

## Testes e critérios de aceite

1. Usuário com grants em A e B **não** recebe chamados de C, mesmo passando `tenant_ids` com C.
2. `row_scope=own` em A e `company` em B: A devolve só os chamados dele, B devolve todos, na mesma resposta.
3. Ordenação global por `updated_at DESC, id DESC` com duas empresas intercaladas; **desempate por `id`** com `updated_at` igual.
4. Cursor: percorrer todas as páginas sem duplicar nem perder item, inclusive com uma atualização entre duas páginas.
5. Falha em uma empresa (consulta lenta/erro simulado): `200`, `partial: true`, empresa em `tenants_failed`, demais itens presentes. Todas falhando: `503`.
6. `X-Tenant-ID` ausente funciona; presente é ignorado.
7. `page_size` e número de empresas acima do limite: `422`.
8. Teste de contrato OpenAPI e teste de isolamento HTTP no padrão de `test_http_tenant_isolation.py` e `test_http_ticket_visibility.py`.
9. Explicitamente: não existe rota de escrita consolidada.

## Decisões em aberto

1. **`total` exato ou estimado?** Proposta: exato (um `count(*)` por empresa, só na primeira página).
2. **Limite de 20 empresas** e **concorrência 4** são adequados ao tamanho da VM Oracle (2 OCPUs, pool de 12)? Proposta: sim.
3. **`tenants_failed` com `200`** em vez de `207` ou erro? Proposta: `200` com `partial`, pela simplicidade para o cliente.
4. O resumo consolidado deve ser **endpoint próprio** (proposto) ou parte de `/dashboard`?
5. O app deve pedir `actionable=true` por padrão, como a web?
