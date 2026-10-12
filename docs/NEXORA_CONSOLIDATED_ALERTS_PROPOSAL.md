# Proposta: endpoint consolidado de alertas

Status: **proposta + implementação no backend** em 12/10/2026 (`consolidated_alerts.py`, migration 040 e
`tests/test_http_consolidated_alerts.py`). Falta a adoção pelo app (aba Alertas) e pela web.

## Rastreabilidade

- Corte: CD-09 Ativos, monitoramento e canais integrados (herda CD-04 e CD-05).
- Referência funcional: *Especificação funcional e técnica — Central de Monitoramento*, versão 1.0 de 12/10/2026
  (seções 4 a 7, 9, 11 e 13; critérios CA01 a CA05). Os contratos `/api/v1/monitor/*` da especificação são propostos e
  "a validar com o backend"; este documento reaproveita o modelo que já existe (`cm_alerts`, `cm_monitors`, `cm_assets`).
- Regra do blueprint: consolidado somente leitura; toda escrita exige empresa concreta.

## Problema

A Visão Geral do app já sai de `GET /api/v1/dashboard`, mas **não há como listar alertas**:

- `GET /api/v1/alerts` exige uma empresa (`X-Tenant-ID`) e devolve o **histórico inteiro** (resolvidos incluídos), sem filtro,
  sem limite e sem paginação. No celular isso baixa milhares de linhas.
- O alerta não traz o nome do serviço nem do ambiente; o cliente teria de buscar `/monitors` e `/assets` de cada empresa.
- Não há ordenação por severidade, que é a ordenação padrão da especificação (criticidade e depois recência).
- Sem listagem, a aba Alertas, os filtros rápidos (Com problemas, Críticos, Atenção) e a coerência entre KPI e grid (CA03) ficam impossíveis.

## Restrições que o desenho respeita

Mesmas do endpoint consolidado de chamados: RLS por conexão (consolidação na aplicação, empresa por empresa, nunca `UNION`),
autorização por empresa com o grant de cada uma, fan-out com concorrência 4, limite de 20 empresas, timeout por empresa,
falha parcial sem esconder dados, e **nenhuma rota de escrita**. Os helpers (`accessible_tenants`, `fan-out`, `parse_tenant_ids`)
são reutilizados de `consolidated_tickets.py`.

## Contrato proposto

### `GET /api/v1/alerts/consolidated`

Bearer; **sem** `X-Tenant-ID` (se vier, é ignorado).

| Parâmetro | Descrição |
|---|---|
| `tenant_ids` | Opcional; interseção com as empresas autorizadas (ids sem acesso são ignorados em silêncio). Máx. 20 |
| `status` | `open` (**padrão**), `resolved`, `ignored` ou `all` |
| `severity` | Lista separada por vírgula entre `info`, `warning`, `average`, `high`, `disaster`. Críticos = `high,disaster`; Atenção = `warning,average` |
| `q` | Busca em título e mensagem |
| `asset_id` | Filtra por ambiente |
| `from`, `to` | Janela de abertura (`opened_at`), ISO 8601 |
| `sort` | `severity` (**padrão**: severidade desc, abertura desc) ou `recent` (abertura desc) |
| `page_size`, `cursor` | 1 a 100 (padrão 25) e cursor opaco |

Resposta `200`:

```json
{
  "items": [{
    "id": "…", "tenant_id": "…", "tenant_name": "Chaveou",
    "title": "API indisponível", "message": "HTTP 503", "severity": "high", "severity_group": "critical",
    "status": "open", "opened_at": "2026-10-12T12:00:00Z", "resolved_at": null,
    "monitor_id": "…", "monitor_name": "API pública", "asset_id": "…", "asset_name": "Produção",
    "incident_id": "…", "incident_status": "in_progress"
  }],
  "page_size": 25, "next_cursor": "…",
  "total": 18,
  "counts": { "critical": 4, "attention": 11, "info": 3 },
  "totals_by_tenant": [{ "tenant_id": "…", "tenant_name": "Chaveou", "total": 12, "critical": 3, "attention": 8, "info": 1 }],
  "tenants_failed": [], "partial": false, "read_only": true,
  "data_as_of": "…", "sync_state": "projected", "filters_applied": { "status": "open", "sort": "severity" }
}
```

- `severity_group` (`critical`, `attention`, `info`) usa o mesmo agrupamento do painel (`/dashboard`): crítico = `high` + `disaster`,
  atenção = `warning` + `average`.
- `counts` e `total` são calculados **com os mesmos filtros** da lista, só na primeira página: os KPIs e o grid concordam (CA03).
  Nas páginas seguintes são omitidos.
- `incident_id` e `incident_status` ligam o alerta ao incidente mais recente (semânticas distintas, vínculo rastreável).
- Falha parcial: `200` com `partial: true` e `tenants_failed`; todas falhando: `503`. A mensagem do banco não vai ao cliente.

## Algoritmo

Igual ao dos chamados, com chave de ordenação composta:

- `sort=severity`: `(rank, opened_at, id)` decrescente, onde `rank` = `disaster 5, high 4, average 3, warning 2, info 1`.
- `sort=recent`: `(opened_at, id)` decrescente.
- Cada empresa devolve `page_size + 1` linhas após o cursor; a mescla corta em `page_size`; `next_cursor` guarda `(rank, opened_at, id)`.
- O cursor também carrega um resumo (hash) dos filtros, da ordenação e das empresas; usá-lo com outros filtros dá `422`.

## Segurança e decisões de acesso

- **Papel `client` fica fora.** Quem só tem papel de cliente (portal de solicitante) não recebe alertas de infraestrutura nesta rota.
  Hoje `GET /api/v1/alerts` e o painel aceitam `client`; a especificação (seção 9) não lista esse papel. Reverter é remover uma linha.
- Alertas não têm `row_scope` (diferente de chamados): dentro da empresa, quem tem acesso vê todos os alertas.
- O backend filtra por empresa em toda consulta; ids de empresa sem permissão nunca são consultados.

## Desempenho

Migration **040**: `ix_cm_alerts_cursor (tenant_id, status, opened_at DESC, id DESC) WHERE inactive_at IS NULL`.
Padrão `status=open` mantém o conjunto pequeno; `all` e `resolved` paginam por cursor, sem baixar o histórico.

## Fora do escopo (e por quê)

- **Reconhecer, atribuir, comentar:** o backend não tem o estado "reconhecido" (só `open`, `resolved`, `ignored`) nem responsável no alerta.
- **Filtro "Meus":** depende de responsável no alerta.
- **Detalhe com linha do tempo de eventos:** não há endpoint de eventos por alerta.
- **"Última sincronização" da integração:** o painel não expõe esse dado.
- **Janela de desatualização e política de agregação de status do ambiente:** são decisões de produto em aberto (seção 16 da especificação).

## Observação sobre o painel

`GET /api/v1/dashboard` conta alertas abertos **sem** excluir `inactive_at`, enquanto esta lista exclui. Se houver alertas abertos e
inativados pela política de retenção, KPI e lista divergem. Recomendo alinhar o painel (fora desta entrega).

## Testes e critérios de aceite

Ordenação por severidade e por recência com desempate; cursor sem duplicar nem perder item nos dois modos; `counts` e `total` só na primeira página;
filtros de status, severidade, busca, ambiente e janela chegam ao SQL; empresa sem grant nunca consultada; papel `client` excluído; falha parcial e `503`;
cursor de outro filtro, ordenação ou conjunto de empresas rejeitado; ausência de rotas de escrita; `page_size` e `tenant_ids` fora do limite.
