# Business Monitor — Rastreabilidade da fatia de código v0.1

Status: **Implementado em pacote para revisão; ainda não integrado, compilado com .NET nem homologado no repositório.** Não marcar BM-01..BM-05 como concluídos.

| Requisito | Arquivos | Situação |
|---|---|---|
| Autorização Business independente | `business.py`, migration 045, React guard | Implementado no código, teste multiempresa integrado pendente |
| Catálogo e thresholds numéricos | `business.py`, `business_rules.py`, tabelas `cm_business_*` | Código + testes unitários de regras |
| Ingestão por agente existente | `business.py`, `CentralClient.Business.cs`, `BusinessCollector.cs` | Código; não homologado com agente real |
| Fila local / operação offline | `BusinessQueue.cs`, cache no `AgentWorker.cs` pelo patch | Primeiro spool; faltam SQLite/quotas de bytes e upgrade seguro |
| Dashboard e grids | `business-monitor.jsx`, `business-monitor.css`, main.jsx pelo patch | Código, build e E2E pendentes |
| Regras de processo / IA / correlação financeira | — | Não implementado |

Ver detalhes e aceites em `README_INSTALACAO.md`. Seguir `docs/NEXORA_DEVELOPMENT_BLUEPRINT.md` e `docs/NEXORA_FRONTEND_STANDARD.md` do projeto original.
