# API de incidentes para aplicativos

Contrato inicial do corte CD-05 (NX-019 e NX-020) para aplicativos que operam incidentes da Nexora.

## Autenticação

Um administrador cria uma credencial em `POST /api/v1/api-clients`, autenticado normalmente na Nexora e informando `X-Tenant-ID`.

```json
{
  "name": "Aplicativo de atendimento",
  "scopes": [
    "incidents:read",
    "incidents:create",
    "incidents:attend",
    "incidents:finish"
  ],
  "valid_days": 365
}
```

O campo `token` da resposta é mostrado uma única vez. O aplicativo envia em todas as chamadas:

```http
Authorization: Bearer nxapp_<id>.<segredo>
X-Tenant-ID: <uuid-da-empresa>
```

Credenciais podem ser listadas por `GET /api/v1/api-clients` e revogadas por `DELETE /api/v1/api-clients/{id}`. Tokens são armazenados somente como hash.

## Operações

### Listar incidentes abertos

```http
GET /api/v1/app/incidents?status=open&page=1&page_size=25
```

Também é possível informar um estado específico ou omitir `status` para listar todos. A resposta inclui paginação, filtros aplicados, `data_as_of` e `sync_state`.

### Consultar um incidente

```http
GET /api/v1/app/incidents/{incident_id}
```

Retorna o incidente e suas interações.

### Criar um incidente

```http
POST /api/v1/app/incidents
Idempotency-Key: app-ocorrencia-123456
Content-Type: application/json

{
  "title": "Banco de dados indisponível",
  "description": "A aplicação não consegue estabelecer conexão.",
  "priority": "high",
  "category": "database",
  "source_reference": "APP-123456"
}
```

### Atender um incidente aberto

Assume o incidente com o usuário técnico associado à credencial, muda o estado para `in_progress` e pode registrar uma interação.

```http
POST /api/v1/app/incidents/{incident_id}/attend
Idempotency-Key: atendimento-123456
Content-Type: application/json

{
  "note": "Análise iniciada pelo aplicativo.",
  "visibility": "public"
}
```

### Finalizar um incidente

Registra a solução e leva o incidente para `resolved`. Tarefas pendentes bloqueiam a operação.

```http
POST /api/v1/app/incidents/{incident_id}/finish
Idempotency-Key: solucao-123456
Content-Type: application/json

{
  "solution_type": "permanent",
  "description": "Conexão restaurada após correção da configuração.",
  "evidence": "Teste de conexão concluído com sucesso."
}
```

## Confirmação e idempotência

As três escritas retornam HTTP `202`, `operation_status: "pending"` e `remote_confirmed: false`. Isso significa que a intenção foi persistida na Nexora e aguarda confirmação do GLPI. O aplicativo não deve apresentar confirmação remota antes de `sync_status` indicar sucesso na projeção reconciliada.

`Idempotency-Key` é obrigatório em toda escrita. Repetir a mesma chave e o mesmo conteúdo devolve o resultado original; reutilizar a chave com conteúdo diferente retorna HTTP `409`.

## Escopos

| Escopo | Permissão |
|---|---|
| `incidents:read` | Listar e consultar incidentes e interações |
| `incidents:create` | Criar incidentes |
| `incidents:attend` | Assumir, iniciar atendimento e registrar interação |
| `incidents:finish` | Registrar solução e resolver incidente |

## Referência de campos e valores

### Credenciais (`POST /api/v1/api-clients`)

| Campo | Regra |
|---|---|
| `name` | 3 a 160 caracteres |
| `scopes` | Ao menos um dos escopos acima; valor inválido retorna `422` |
| `valid_days` | 1 a 730 (padrão 365) |
| `actor_user_id` | Opcional; padrão é o administrador que cria a credencial. Deve ser um usuário ativo e autorizado a operar a empresa, senão `422` |

O `actor_user_id` é o usuário técnico que aparece como autor, solicitante e responsável nas operações do aplicativo.

Papéis exigidos: criar exige `admin` ou `manager`; listar exige `admin`, `manager` ou `auditor`; revogar exige `admin`. A revogação é imediata, retorna `204` e fica no registro de auditoria. Revogar uma credencial já revogada ou inexistente retorna `404`.

### Criar incidente

| Campo | Regra |
|---|---|
| `title` | Obrigatório, 3 a 240 caracteres |
| `description` | Obrigatório, 3 a 20000 caracteres |
| `priority` | `low`, `medium` (padrão), `high`, `critical` |
| `category` | Até 100 caracteres (padrão `general`) |
| `service_code` | Opcional, até 100 caracteres |
| `source_reference` | Opcional, até 300 caracteres. Referência do aplicativo para correlação |
| `assignee_team_id` | Opcional, UUID de equipe ativa |

O incidente nasce com `origin: "api"` e estado `new`.

### Atender incidente

| Campo | Regra |
|---|---|
| `note` | Opcional, 1 a 10000 caracteres |
| `visibility` | `public` (padrão) ou `private` (interação interna) |

### Finalizar incidente

| Campo | Regra |
|---|---|
| `solution_type` | `permanent`, `workaround`, `not_reproduced` ou `cancelled` |
| `description` | Obrigatório, 3 a 20000 caracteres |
| `evidence` | Opcional, até 20000 caracteres |

### Listagem

- `page` começa em 1; `page_size` vai de 1 a 100 (padrão 25).
- `status` aceita `open` (padrão), um estado específico ou vazio para todos. Itens inativos nunca são retornados.
- `open` agrupa: `new`, `triaged`, `assigned`, `acknowledged`, `in_progress`, `waiting_customer`, `waiting_supplier`.
- Ordenação por `updated_at` decrescente. A resposta traz `items`, `page`, `page_size`, `total`, `pages`, `filters_applied`, `data_as_of` e `sync_state`.
- `sync_state` da listagem é sempre `projected`: os dados vêm da projeção local da Nexora, não de uma leitura direta no GLPI.

### Estado de sincronização (`sync_status` do incidente)

| Valor | Significado |
|---|---|
| `pending` | Alteração registrada na Nexora e ainda não confirmada pelo GLPI |
| `synced` | Confirmada pelo GLPI e reconciliada |
| `failed` | O worker esgotou a tentativa de sincronização; requer intervenção operacional |

O aplicativo deve tratar somente `synced` como confirmação remota. Para acompanhar o resultado, consulte `GET /api/v1/app/incidents/{incident_id}` até que `sync_status` deixe de ser `pending`.

### Respostas das escritas

Todas incluem `id`, `status`, `sync_status`, `version`, `operation_status: "pending"` e `remote_confirmed: false`. A criação devolve também `title`, `priority`, `category` e `created_at`; o atendimento devolve `assignee_user_id`; a finalização devolve `resolved_at` e `solution_id`.

## Erros

O corpo de erro é `{"detail": "<mensagem>"}`.

| HTTP | Situação |
|---|---|
| `401` | Token ausente, malformado, desconhecido, revogado, expirado ou de outra empresa |
| `403` | Escopo ausente; ou o usuário técnico não pertence à equipe responsável pelo incidente |
| `404` | Incidente inexistente ou inativo na empresa informada |
| `409` | `Idempotency-Key` reutilizada com conteúdo ou operação diferente; requisição concorrente com a mesma chave; incidente fora de estado aberto; incidente atribuído a outro técnico; tarefas pendentes ao finalizar |
| `422` | Falha de validação de campos, de cabeçalho `Idempotency-Key` ou de equipe inválida |

Retentar é seguro com a mesma `Idempotency-Key` e o mesmo corpo. Um `409` por requisição concorrente pode ser repetido após alguns segundos. Os demais `409` e os `4xx` não devem ser repetidos sem corrigir a causa.

## Idempotência

- A chave tem de 8 a 200 caracteres e vale por credencial e por operação. No atendimento e na finalização, a operação inclui o `incident_id`.
- Os resultados ficam guardados por 7 dias. Depois disso a mesma chave é tratada como nova.
- Use uma chave nova para cada intenção distinta, por exemplo um UUID gerado pelo aplicativo e persistido antes do envio.

## Regras de operação

- Atender e finalizar só são aceitos em incidentes abertos. Um incidente já atribuído a outro técnico não pode ser atendido nem finalizado por esta credencial.
- Se o incidente tem equipe responsável, o usuário técnico precisa ser membro dela.
- Um incidente finalizado fica `resolved`, e o fechamento definitivo é feito fora desta API.
- Todas as chamadas são isoladas pela empresa informada em `X-Tenant-ID`. Uma credencial só opera na empresa em que foi criada.
- Cada uso atualiza `last_used_at`, visível na listagem de credenciais.
- Para trocar um token (rotação), crie uma nova credencial, migre o aplicativo e revogue a anterior. Não há renovação in-place.
- Não há limite de taxa (rate limit) implementado para esta API no momento.

O contrato interativo está disponível em `/api/docs` e o OpenAPI em `/api/openapi.json`.
