# Blueprint de desenvolvimento Nexora

Versão 1.0 - 11 de outubro de 2026

## 1. Autoridade e objetivo

Este arquivo é o contrato de desenvolvimento do projeto Nexora. Ele transforma a especificação funcional e técnica do modelo híbrido em decisões aplicáveis ao código existente.

O sequenciamento detalhado e a rastreabilidade integral de NX-001 a NX-048, AT-01 a AT-28 e T01 a T33 estão definidos em `docs/NEXORA_DEVELOPMENT_SLICES.md`.

Em caso de conflito, a ordem de autoridade é:

1. requisitos de segurança, isolamento e integridade deste blueprint;
2. `docs/NEXORA_FRONTEND_STANDARD.md` para interface e responsividade;
3. contratos OpenAPI, migrations e decisões arquiteturais versionadas;
4. mockups e referências visuais, que não autorizam dados, funções ou fluxos simulados.

O produto-alvo é uma plataforma multiempresa para operação interna e para prestadoras de serviço. O GLPI permanece como autoridade do atendimento ITSM. O Nexora concentra experiência, autorização, monitoramento, integrações recuperáveis e extensões comerciais.

## 2. Diagnóstico do projeto atual

### 2.1 O que deve ser preservado

| Capacidade atual | Avaliação | Direção |
|---|---|---|
| React, FastAPI e PostgreSQL | Aderente à arquitetura alvo | Evoluir o monólito para módulos internos, sem reescrever a aplicação. |
| Keycloak com OIDC e PKCE | Base válida de identidade | Completar nonce, MFA privilegiado, revogação efetiva e política de sessão. |
| Tenant, memberships e perfis | Base multiempresa existente | Preservar IDs e migrar para workspace, operadora, relação de atendimento e concessões explícitas. |
| RLS em tabelas operacionais | Defesa útil já iniciada | Cobrir todas as tabelas sensíveis, testar pooling e separar papéis de migration, API e workers. |
| Outbox PostgreSQL e worker | Direção arquitetural correta | Evoluir estados, lease, retentativa segura, incerteza, idempotência e reconciliação. |
| GLPI, Zabbix, agentes e notificações | Integrações reais existentes | Colocar adaptadores atrás de contratos e manifests de capacidade homologados. |
| Auditoria e trilha operacional | Base parcial | Tornar consistente, correlacionada, append-only na aplicação e protegida por retenção. |
| Padrão visual Nexora | Documento obrigatório existente | Migrar telas restantes para componentes compartilhados e rotas próprias. |
| Monitoramento de banco nativo | Recurso real do Nexora | Manter por empresa e banco, sem iframe, com fonte e atualização identificadas. |

### 2.2 Lacunas críticas encontradas

| Tema | Estado observado | Risco | Correção obrigatória |
|---|---|---|---|
| Modelo organizacional | `tenant_id` representa apenas a empresa; não há workspace, operadora ou engagement. | Acesso e contratos podem ser inferidos de forma errada. | Criar modelo incremental, preservando tenants e IDs atuais. |
| Autorização | Papéis amplos e master; ausência de catálogo completo de capacidades e escopo de objeto. | Ampliação de acesso por papel ou tela. | Centralizar autorização como capacidade + empresa + objeto + relação. |
| GLPI | Cliente legado simples, códigos de status fixos e escrita sem manifest homologado. | Incompatibilidade, duplicidade e falso sucesso. | Inventariar versão/API, criar capability manifest e adaptador normalizado. |
| Autoridade ITSM | Incidente local ainda funciona como registro principal em vários fluxos. | Dois motores concorrentes de chamado. | Tornar GLPI autoridade após confirmação e Nexora projeção/extensão. |
| Sincronização | Outbox simples, sem inbox, leases, `UNCERTAIN`, conflitos ou reconciliação ampla. | Repetição de POST e perda de efeitos remotos. | Implementar máquina de estados de comando, inbox e reconciliação. |
| SLA | Cálculo local trata espera de fornecedor como pausa e usa política local como oficial. | Indicador contratual incorreto. | GLPI governa TTO/TTR; primeira resposta real é obrigação separada no Nexora. |
| Exclusão | Há `DELETE` físico em cadastros e históricos operacionais. | Perda de rastreabilidade e referências. | Inativar cadastros usados; purga apenas por política controlada. |
| Frontend | Aplicação concentrada em `main.jsx`, navegação por estado, formulários inline e prompts. | URLs não endereçáveis, regressões e divergência visual. | Introduzir roteamento e componentes AppShell, ListPage, FormPage e DataGrid. |
| Contexto consolidado | Algumas telas já exibiram dados antigos com “Todas as empresas” e tentaram escrever sem empresa. | Operação no tenant errado ou erro 422. | Consolidado somente em leitura; toda escrita exige empresa concreta. |
| Segurança de sessão | Token no `sessionStorage`; falta de evidência de nonce, MFA e sessão BFF. | Exposição maior a script e revogação tardia. | Registrar ADR e migrar para cookie Secure/HttpOnly quando a camada BFF estiver pronta. |
| Anexos | Não há quarentena, varredura e autorização de download completas. | Vazamento e conteúdo malicioso. | Implementar uploads temporários e liberação após análise. |
| Qualidade | Testes cobrem módulos pontuais; não há matriz AT completa nem evidência de CI/restore. | Funcionalidade aparente sem garantia operacional. | Vincular testes ao requisito e exigir restore, segurança e responsividade. |

## 3. Crítica e ajustes à especificação de origem

A especificação de origem tem boa direção de produto, segurança e integração, mas deve ser usada com estes ajustes:

1. **Não é retrato do código atual.** O texto declara que não houve acesso ao repositório. Este blueprint passa a ser a ponte oficial entre alvo e implementação observada.
2. **O escopo é maior que uma primeira entrega.** E0, E1, E2, E3 e E4 não devem ser apresentados como um único MVP. A liberação ocorre por capacidade vertical comprovada.
3. **As 48 entidades lógicas não são ordem de criação de tabelas.** Primeiro se mapeiam e reutilizam `cm_*`; novas estruturas entram apenas na fase que as utiliza.
4. **Monólito modular é o alvo, não um arquivo monolítico.** FastAPI continua como implantação principal, mas regras de domínio, adaptadores e contratos não permanecem concentrados em `main.py` e `operations.py`.
5. **GLPI é autoridade somente para dados ITSM confirmados.** Intenções, comandos, contratos, primeira resposta, autorização e auditoria Nexora continuam no PostgreSQL Nexora.
6. **Métricas propostas não são SLA contratado.** p95, RPO, RTO e 99,9% só viram compromisso após medição, capacidade e aceite formal.
7. **Segurança de sessão exige migração planejada.** A troca imediata do fluxo SPA atual por BFF/cookie não deve quebrar login; deve existir ADR, teste e rollout progressivo.
8. **A interface não pode antecipar módulos inexistentes.** Menus são liberados por capacidade real e feature flag, nunca como telas vazias de demonstração.
9. **Monitoramento existente não deve esperar E2 para ser preservado.** Ele já é parte real da operação; deve ser integrado ao modelo alvo sem ser refeito.

## 4. Decisões arquiteturais obrigatórias

### ADR 01 Monólito modular com processos separados

Manter FastAPI e PostgreSQL como núcleo. Separar internamente `identity`, `tenancy`, `service_desk`, `catalog`, `contracts`, `billing`, `monitoring`, `automation`, `notifications`, `integrations`, `audit` e `reporting`. API, worker e orquestrador podem ser processos distintos do mesmo código-base.

### ADR 02 Uma autoridade por dado

- GLPI: chamado confirmado, atores, interações, tarefas, solução, SLA e OLA nativos.
- Nexora: identidade aplicacional, autorização, catálogo próprio, contratos, primeira resposta real, consumo, comandos, auditoria e projeções.
- Zabbix/agentes: eventos e métricas de infraestrutura.
- ERP conectado: emissão, título, vencimento, baixa e recebimento.

Nenhum dado pode ter dois escritores sem uma política de precedência e reconciliação registrada.

### ADR 03 Contexto organizacional explícito

`tenant_id` continua sendo a empresa atendida. `workspace_id`, `operator_company_id` e `engagement_id` são dimensões diferentes. Equipe, contrato e relação comercial não concedem acesso por consequência.

### ADR 04 Escrita assíncrona confirmada

Toda mutação remota relevante cria comando durável e auditoria na mesma transação. Estados mínimos: `PENDING`, `RUNNING`, `SUCCEEDED`, `REJECTED`, `RETRY_WAIT`, `UNCERTAIN`, `CONFLICT` e `CANCELLED`. A interface não apresenta sucesso remoto antes da confirmação.

### ADR 05 Consolidação somente para leitura

“Todas as empresas” usa apenas empresas autorizadas e somente endpoints definidos de leitura. Criar, editar, inativar, excluir, aprovar, executar ou exportar conteúdo sensível exige empresa concreta e autorização novamente validada.

### ADR 06 Histórico não é CRUD descartável

Chamados, consumo, auditoria, comandos, integrações confirmadas e dados financeiros não são fisicamente excluídos por ações comuns. Cadastros referenciados são inativados. Purga exige política, retenção, evidência e execução administrativa separada.

## 5. Contrato para todo novo desenvolvimento

### 5.1 Antes de desenvolver

Toda história deve declarar:

- requisito funcional e caso de aceite relacionado;
- proprietário de cada dado;
- empresa, workspace e escopo do objeto;
- capacidades exigidas;
- contrato de API e estados de erro;
- comportamento de carga, vazio, indisponibilidade, conflito e permissão;
- impacto em GLPI, SLA, notificação, auditoria e retenção;
- comportamento desktop e mobile;
- estratégia de migration, retorno e observabilidade.

Se uma capacidade externa ainda não foi comprovada, o recurso fica desabilitado e a pendência é registrada. Não se simula sucesso.

### 5.2 Backend e API

- API sob `/api/v1`, com UUID público quando aplicável e datas ISO 8601.
- Listagens retornam paginação, filtros aplicados, `data_as_of` e estado de sincronização.
- Erros usam código estável, mensagem, campos inválidos, correlação e indicação de retentativa.
- Identificador recebido nunca substitui autorização do objeto.
- Mutação repetível recebe `Idempotency-Key`; payload divergente com mesma chave gera conflito.
- Timeout de escrita remota gera estado incerto até reconciliação, não repetição cega.
- Toda regra de empresa é aplicada no serviço e reforçada por FKs/RLS quando possível.

### 5.3 Banco e migrations

- Reutilizar tabelas e IDs atuais antes de criar equivalentes.
- Migrations são incrementais: expandir, preencher, validar e só então restringir.
- JSONB é reservado a snapshots e schemas validados; relações centrais usam FKs.
- Aplicação, migration e integração usam papéis diferentes; aplicação não tem `BYPASSRLS` nem é proprietária das tabelas protegidas.
- Toda nova tabela operacional recebe política de isolamento, índices do caso de uso e testes de conexão reutilizada.
- Exclusão em cascata só é aceita quando o filho é realmente descartável e não histórico.

### 5.4 Frontend

- Cumprir integralmente `docs/NEXORA_FRONTEND_STANDARD.md`.
- Usar rotas endereçáveis; listagem, novo, detalhe e edição são páginas próprias.
- Listagens seguem identificação, métricas reais, filtros, grid completa e paginação.
- Alterar Empresa cancela respostas antigas, limpa seleções incompatíveis e recarrega o escopo.
- Falha não vira zero; sem fonte usa `—` ou estado indisponível com recuperação.
- Ações usam SVG compartilhado, nome acessível, tooltip, foco visível e alvo de toque mínimo.
- Menus e botões são derivados das capacidades efetivas, sem depender apenas de ocultação visual.

### 5.5 Integrações e workers

- Cada integração possui `capability manifest` versionado com versão, autenticação, operações testadas e limitações.
- Adaptadores não contêm regra comercial e não vazam modelos remotos ao frontend.
- Inbox persiste evento autenticado antes de confirmar recebimento.
- Eventos são sinais; o worker busca o estado atual antes de atualizar a projeção.
- Workers usam lease, correlação, limite de tentativa, backoff e fila de tratamento manual.
- Credenciais vêm de segredo externo e nunca aparecem em tela, log ou payload de auditoria.

### 5.6 Testes e evidências

- Unidade: regra de domínio, transição, calendário, idempotência e autorização.
- Integração: banco real, RLS, migrations, GLPI/Zabbix simulados por contrato e falhas parciais.
- Segurança: troca de IDs entre empresas, revogação, anexo, exportação e acesso direto à API.
- Interface: 320, 360, 390, 768, 1024, 1366 e 1920 px; teclado, zoom e foco.
- Operação: queda e retorno do GLPI, comando incerto, evento duplicado, restore e reconciliação.
- Evidência registra versão, usuário, empresa, pré-condição, resultado e correlação. Captura de tela não substitui teste da API.

## 6. Roadmap ajustado ao estado real

### E0 Fundação comprovada

1. Inventariar versão/build GLPI, API, plugins, entidades, jobs, perfis e autenticação.
2. Criar `glpi-capabilities.json` com provas de leitura e escrita.
3. Mapear `cm_*` para o modelo lógico e registrar reutilizar, alterar ou criar.
4. Auditar RLS, papéis do banco, segredos, outbox, restore e ambientes.
5. Consolidar componentes de frontend e introduzir roteamento sem regressão.
6. Publicar matriz de propriedade dos dados e decisões de SLA/primeira resposta.

**Saída:** inventário versionado, capability manifest, mapa de dados, matriz de autorização, ADRs e plano de migration.

### E1 Núcleo confiável de integração

1. Workspace, operadora, relações e concessões incrementais.
2. Catálogo de capacidades e autorização central por objeto.
3. Adaptador GLPI normalizado, comandos duráveis, inbox e reconciliação.
4. Operações pendentes e conflitos visíveis.
5. Auditoria correlacionada, métricas técnicas e alarmes de integração.

**Saída:** isolamento comprovado e mutação GLPI sem duplicidade ou falso sucesso.

### E2 Atendimento completo

1. Chamados: listagem, criação, detalhe, edição e transições.
2. Interações públicas/privadas, tarefas, solução, anexos e participantes.
3. Portal do solicitante, catálogo inicial, filas e apontamentos.
4. TTO/TTR projetados do GLPI e primeira resposta separada.
5. Notificações sem duplicidade de autoridade.

**Saída:** fluxos interno e prestadora executados integralmente no Nexora com confirmação do GLPI.

### E3 Operação híbrida profissional

1. Contratos, cobertura e política versionada.
2. Revisão de horas, ledger, franquia e excedente.
3. Pré-faturamento auditado e exportação, sem inventar estados de ERP.
4. Qualidade, indicadores reconciliáveis, carga, segurança e restore completo.
5. Liberação gradual por empresa e recurso.

**Saída:** piloto profissional com operação, produto e segurança aprovados.

### E4 Evolução controlada

Problemas, mudanças, conhecimento avançado, incidente maior, runbooks e IA entram somente após E1-E3 e seus controles. IA permanece assistiva; não decide autorização, finanças ou execução crítica.

## 7. Próximo corte de desenvolvimento

O próximo ciclo deve priorizar, nesta ordem:

1. inventário e capability manifest do GLPI atual;
2. refatoração do cliente GLPI e remoção de códigos de status presumidos;
3. máquina de estados da outbox, idempotência e reconciliação;
4. correção do SLA atual para não pausar fornecedor automaticamente;
5. política de inativação no lugar de exclusão física de objetos usados;
6. roteamento real do frontend e páginas próprias para Novo, Editar e Detalhe;
7. cobertura RLS e testes multiempresa adversariais;
8. tela de operações pendentes e saúde das integrações.

Novos módulos comerciais não devem começar antes dos itens 1 a 4, pois dependeriam de uma integração ainda não comprovada.

Esse trabalho corresponde ao início do CD-00 e prepara as portas de liberação definidas em `docs/NEXORA_DEVELOPMENT_SLICES.md`.

## 8. Definição de concluído

Uma entrega só está concluída quando possui:

- código revisado e sem segredo;
- migration incremental quando necessária;
- contrato OpenAPI atualizado;
- autorização no backend e RLS aplicável;
- testes automáticos e casos de aceite relacionados;
- estados de interface reais e responsivos;
- auditoria, logs, métricas e procedimento de falha;
- documentação de implantação e retorno;
- evidência de homologação no ambiente e versão suportados.

Tela demonstrativa, botão sem comportamento real, integração simulada ou sucesso não confirmado não encerram uma história.

