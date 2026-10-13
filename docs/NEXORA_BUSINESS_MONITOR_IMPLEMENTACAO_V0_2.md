# NEXORA BUSINESS MONITOR — Registro da entrega V0.2

**Versão:** V0.2-dev / 13/10/2026  
**Escopo:** código P0/P1 sobre o Nexora existente.  
**Status:** entregue como arquivos de código para integração; não homologado, não implantado, não publicado no Git.

## Funcionalidades codificadas

- Banco incremental: migrations `045_business_monitor_mvp.sql`, `046_business_workflows_p1.sql`, `047_business_hardening_v02.sql`, tabelas `cm_business_*`, RLS.
- Backend: `business.py` (base/ingestão/regras simples), `business_p1.py` (unidades, processos, etapas, regras avançadas, fontes, Studio, relatórios, grants, subscrição) e `business_evaluation.py` (avaliação temporal, estado stale, incidentes e notificações internas).
- Agente .NET: `BusinessReader.cs` (SQL Server/PostgreSQL/REST/CSV/XLSX), `BusinessCollector.cs`, `BusinessQueue.cs` (SQLite WAL), `BusinessSample.cs` e extensões de envio; cache remoto integrado no `AgentWorker` pelo script V0.1.
- UI: `business-monitor.jsx` para dashboard/indicadores/ocorrências e `business-p1.jsx` para processos, regras, unidades, conectores, relatórios, notificações e concessões.
- Ferramentas: integradores idempotentes/guardados da V0.1 e V0.2, validação de integridade e testes puros de regras e contratos.

## Evidências locais

- Análise sintática Python dos módulos passou.
- Seis testes existentes do motor de regras foram executados com sucesso.
- Seis novos testes P1 foram preparados, mas não executados neste ambiente por falta de dependências do backend; executar no checkout com requirements instalado.
- JSX de `business-monitor.jsx` e `business-p1.jsx` analisado com compilador TypeScript/transpileModule, sem diagnósticos sintáticos.
- SDK .NET 8, serviços ERP reais, ambiente PostgreSQL do Nexora e dependências npm deste checkout não estão disponíveis aqui; portanto não houve build C#, build do Vite, migração em banco real, E2E de navegador ou teste de tráfego HUB->API.

## Conhecidas limitações e bloqueios

1. O fluxo visual `POST /collectors/{id}/test` **valida metadados**: ainda não despacha teste remoto pelo HUB. Publicar exige segundo aprovador e teste real em HML.
2. O hub não possui agente updater assinado/autônomo ou mTLS em todas as plataformas; TLS é obrigatório e existe verificação de certificados SQL/PG.
3. O sistema de notificações Business é interno; integração de ocorrências a GLPI e envio WhatsApp/SMTP estão pendentes de confirmação das APIs e regra comercial.
4. Conectores suportam leituras numéricas singulares; transformação low-code avançada, planilhas complexas e multi-origens por um mesmo conector não integram esta primeira implementação.
5. Os testes integrados, de desempenho, RLS contra banco real, perda de conexão, Windows/Linux e rollout/rollback precisam ser realizados pelo desenvolvedor em HML.
6. Nenhum acesso Business é criado por migration; Master técnico não recebe automaticamente acesso ao conteúdo.

## Próxima ação técnica

Aplicar overlay em branch de homologação, executar ambos os integradores, revisar diffs, testar migrations com papel de DDL, compilar API/React/.NET, provisionar views read-only reais e provar o caminho ponta a ponta com dois tenants/usuários. Usar a matriz V02-AT no README como condição de liberação.
