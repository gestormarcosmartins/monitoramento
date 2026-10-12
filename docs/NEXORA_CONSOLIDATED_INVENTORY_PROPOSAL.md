# Proposta: endpoints consolidados de ambientes e serviços

Status: **proposta + implementação no backend** em 12/10/2026 (`consolidated_inventory.py` e
`tests/test_http_consolidated_inventory.py`), com a adoção no app (abas Ambientes e Serviços).

## Rastreabilidade

- Corte: CD-09 Ativos, monitoramento e canais integrados (herda CD-04 e CD-05).
- Referência funcional: *Especificação funcional e técnica — Central de Monitoramento*, v1.0 de 12/10/2026
  (seções 3, 4, 6, 7, 9, 10 e 13; CA01 a CA05 e CA07). Ambientes = `cm_assets`; serviços = `cm_monitors`.
- Regra do blueprint: consolidado somente leitura; toda escrita exige empresa concreta.

## Problema

- `GET /api/v1/assets` e `GET /api/v1/monitors` exigem uma empresa e devolvem **todas as colunas**. Em `cm_monitors`,
  `configuration` é um JSON livre (URL de checagem, host, porta, parâmetros) e em `cm_assets` há `address` e `metadata`.
  Nenhum cliente móvel precisa disso, e hoje qualquer papel com acesso à empresa o recebe.
- Não há estado calculado do ambiente (saudável, atenção, crítico, indisponível, desconhecido) nem contagem de alertas por ambiente/serviço.
- Não há "Todas as empresas": o app teria de consultar empresa por empresa.

## Princípios

1. **Projeção mínima:** os endpoints novos nunca devolvem `configuration`, `metadata` nem `address`.
2. **Mesmo modelo dos consolidados anteriores:** RLS por conexão (consolidação na aplicação), autorização com o grant de cada empresa,
   fan-out com concorrência 4, limite de 20 empresas, timeout por empresa, falha parcial visível, ids sem acesso ignorados em silêncio,
   papel `client` fora, e **nenhuma rota de escrita**.
3. **Estado calculado no servidor** por funções puras e testáveis; o cliente não recalcula.
4. **Desconhecido nunca vira saudável**, e falha de coleta ou telemetria velha nunca vira "normal" (seções 7 e 10 da especificação).

## Contrato

Ambos usam Bearer, **sem** `X-Tenant-ID`, e `tenant_ids` opcional (interseção com as empresas autorizadas).
Como os inventários são pequenos, paginam por **página** (`page`, `page_size` 1 a 100, padrão 25), com `total` exato.
Limite de segurança: 1000 linhas por empresa; acima disso a empresa entra em `tenants_truncated`.

### `GET /api/v1/assets/consolidated` (ambientes)

Filtros: `status` (lista entre `healthy`, `attention`, `critical`, `unavailable`, `unknown`, `inactive`), `kind`, `q` (nome), `sort` (`status` padrão ou `name`).

Item: `id`, `tenant_id`, `tenant_name`, `name`, `kind`, `criticality`, `active`, `status`,
`services` (`total`, `up`, `down`, `unknown`, `inactive`) e `alerts` (`critical`, `attention`, `info`; só abertos).

### `GET /api/v1/monitors/consolidated` (serviços)

Filtros: `state` (lista entre `healthy`, `unavailable`, `unknown`, `inactive`), `asset_id`, `monitor_type`, `q` (nome), `sort` (`state` padrão ou `name`).

Item: `id`, `tenant_id`, `tenant_name`, `name`, `monitor_type`, `active`, `severity`, `state`, `last_status`, `stale`,
`last_checked_at`, `last_message` (até 200 caracteres), `interval_seconds`, `asset_id`, `asset_name`, `responsible_team_name`,
`open_alerts` (`critical`, `attention`, `info`).

Resposta comum: `items`, `page`, `page_size`, `pages`, `total`, `counts` (por estado, **sobre o conjunto filtrado**, para KPI e grid coincidirem),
`totals_by_tenant`, `tenants_failed`, `tenants_truncated`, `partial`, `read_only`, `data_as_of`, `sync_state`, `filters_applied`.
Falha parcial: `200` com `partial: true`; todas falhando: `503`.

## Política de estado (provisória)

A especificação deixa a política de agregação em aberto (seção 16). Adoto esta regra, que é conservadora e fica em funções puras,
fáceis de trocar quando o produto decidir:

**Serviço** (`state`):
1. `inactive`: serviço desativado.
2. `unknown`: coleta **desatualizada** (`stale`: nunca coletou ou `last_checked_at` mais antigo que `max(3 × interval_seconds, 180 s)`)
   ou `last_status = unknown`. Telemetria velha nunca vale como saudável.
3. `unavailable`: `last_status = down`.
4. `healthy`: `last_status = up` e coleta atual.

**Ambiente** (`status`), calculado a partir dos **estados dos serviços ativos** (acima) e dos alertas abertos, em ordem de precedência:
1. `inactive`: ambiente desativado.
2. `unavailable`: algum serviço ativo em estado `unavailable`.
3. `critical`: algum alerta **aberto** crítico (`high`, `disaster`).
4. `attention`: algum alerta aberto de atenção (`warning`, `average`).
5. `unknown`: nenhum serviço ativo, ou algum serviço ativo em estado `unknown` (inclui coleta desatualizada).
6. `healthy`: todos os serviços ativos `healthy` e nenhum alerta aberto.

Em `services` do ambiente, `up` = serviços `healthy`, `down` = `unavailable` e `unknown` = `unknown` (todos entre os ativos), e `inactive` = desativados.

A janela de desatualização (3 × intervalo, mínimo 180 s) e a precedência são **decisões de produto a confirmar**.

## Segurança e decisões de acesso

- Papel `client` fica fora (a especificação não o lista), igual aos alertas.
- `last_message` pode citar host ou URL da checagem; é truncado e só é entregue a quem já enxerga a empresa (o `GET /monitors` atual já o devolve).
- Alertas e serviços não têm `row_scope`: quem tem acesso à empresa vê todos.

## Fora do escopo

Criar, editar ou desativar ambiente e serviço; detalhe com histórico de coletas; `last_sync` da integração Zabbix; ações técnicas.

## Testes e critérios de aceite

Funções de estado (todas as precedências e a janela de desatualização, inclusive "desconhecido nunca vira saudável"); isolamento por empresa e
papel `client`; filtros chegam ao SQL; paginação por página com `total` exato; `counts` coerentes com o filtro; falha parcial e `503`;
ausência de `configuration`, `metadata` e `address` na resposta e no SQL; ausência de rotas de escrita.
