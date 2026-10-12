# Cortes de desenvolvimento Nexora

Versão 1.0 - 11 de outubro de 2026

## 1. Objetivo

Este plano divide toda a especificação funcional e técnica do Nexora em cortes verticais homologáveis. Um corte entrega comportamento utilizável de ponta a ponta; não separa a tela do backend, da autorização, da persistência, dos testes e da operação.

Os cortes não recebem estimativas ou datas antes do inventário técnico do CD-00. A numeração representa dependência predominante, não obrigação de execução estritamente serial. Trabalho paralelo é permitido quando os contratos de entrada e saída estiverem definidos.

## 2. Regra de execução

Todo corte deve entregar, quando aplicável:

1. regra funcional e dono do dado;
2. migration incremental e plano de retorno;
3. autorização no backend e RLS;
4. API Nexora documentada;
5. integração real ou capacidade explicitamente indisponível;
6. interface no padrão oficial Nexora;
7. testes unitários, integração, segurança e responsividade;
8. auditoria, logs, métricas e alertas;
9. evidência de homologação;
10. documentação operacional.

Nenhum corte é concluído com mock, botão sem ação real, sucesso remoto não confirmado ou estado de erro convertido em zero.

## 3. Visão dos cortes

| Corte | Resultado de negócio | Dependência principal | Itens NX predominantes |
|---|---|---|---|
| CD-00 | Fundação comprovada e plano executável | Código e ambientes atuais | NX-001 a NX-008 |
| CD-01 | Shell e componentes oficiais reutilizáveis | CD-00 parcial | NX-006 |
| CD-02 | Workspace, empresas, identidade e autorização segura | CD-00 | NX-009 a NX-011 |
| CD-03 | Adaptador GLPI homologado | CD-00 e CD-02 | NX-002, NX-003, NX-005, NX-012 |
| CD-04 | Comandos, eventos e reconciliação recuperáveis | CD-03 | NX-013 a NX-018 |
| CD-05 | Central de chamados completa | CD-01, CD-02 e CD-04 | NX-019, NX-020 |
| CD-06 | Portal e catálogo de serviços | CD-05 | NX-022, NX-023, NX-025 |
| CD-07 | Filas, horas, SLA e notificações | CD-05 e CD-06 | NX-021, NX-024, NX-026, NX-028 |
| CD-08 | Contratos, consumo, qualidade e pré-faturamento | CD-07 | NX-027, NX-029 a NX-033 |
| CD-09 | Ativos, monitoramento e canais integrados | CD-04 e CD-05 | NX-034 a NX-036 |
| CD-10 | Segurança, carga, restore e piloto profissional | CD-02 a CD-09 | NX-037, NX-038 |
| CD-11 | Conhecimento, problemas, mudanças e automação avançada | CD-10 | NX-039 a NX-045 |
| CD-12 | IA assistiva e otimização | CD-11 | NX-046 a NX-048 |

## 4. CD-00 Fundação comprovada

### Resultado

Substituir suposições por um inventário versionado e definir exatamente o que será reutilizado, alterado ou criado.

### Entregas

- inventário de código, bancos, containers, redes, identidade, jobs e integrações;
- versão/build do GLPI, APIs disponíveis, plugins, entidades, perfis, calendários, automações e coletor de e-mail;
- provas de leitura e escrita para ticket, interação pública/privada, tarefa, solução, anexo, entidade, SLA e aprovação;
- `glpi-capabilities.json` sem segredos, com evidência e limitações;
- mapa entre tabelas `cm_*` existentes e o modelo lógico alvo;
- matriz de autoridade de dados: Nexora, GLPI, Zabbix/agente, Keycloak e ERP;
- matriz de perfis, empresas e objetos;
- auditoria de RLS, papéis do PostgreSQL, pooling, outbox, segredos e migrations;
- inventário do frontend e lista de telas que violam o padrão obrigatório;
- desenho DEV, HML e PROD, pipeline mínimo e primeiro procedimento de restore;
- ADRs para sessão BFF/cookie, autenticação GLPI, SLA, anexos e retenção.

### Testes e saída

- comprovar que uma entidade GLPI não vaza dados para outra;
- registrar recursos não suportados como indisponíveis;
- executar restore técnico em ambiente isolado ou registrar bloqueio formal com responsável;
- aprovar o mapa `reutilizar / alterar / criar` antes da primeira migration estrutural.

### Rastreabilidade

NX-001, NX-002, NX-003, NX-004, NX-005, NX-006, NX-007 e NX-008. Sustenta RN-001 a RN-009 e AT-01 a AT-04.

## 5. CD-01 Shell e componentes oficiais

### Resultado

Estabelecer uma fundação de interface única para que as novas telas não aumentem a dívida do `main.jsx`.

### Entregas

- roteamento endereçável e retorno à listagem com filtros, ordenação, página e posição;
- `AppShell`, `PageHeader`, `ListPage`, `MetricGroup`, `FilterBar`, `DataGrid`, `FormPage`, `FormSection` e `FormActions`;
- estados compartilhados `Loading`, `Empty`, `NoResults`, `Unavailable`, `Conflict`, `Forbidden` e `SessionExpired`;
- ícones SVG, tooltips, nomes acessíveis e alvos de toque;
- contexto Empresa com cancelamento de respostas antigas e escrita somente em empresa concreta;
- feature flags/capacidades controlando menu e ações;
- migração piloto de Equipes e de uma listagem operacional para rotas próprias;
- testes em 320, 360, 390, 768, 1024, 1366 e 1920 px.

### Saída

Novo, Editar, Detalhe e Voltar funcionam em URLs próprias; nenhuma tela piloto usa prompt, formulário lateral ou modal de cadastro.

### Rastreabilidade

NX-006, ADR-10, T12 e AT-26. Aplica o padrão visual a todos os cortes seguintes.

## 6. CD-02 Workspace, empresas, identidade e autorização

### Resultado

Representar corretamente operação interna e prestadora sem ampliar acesso por contrato, equipe ou seletor visual.

### Entregas

- `workspace`, empresa atendida, organização operadora e relação de atendimento;
- migração incremental preservando IDs de tenants, usuários, equipes e vínculos atuais;
- catálogo de capacidades e escopos: próprio, departamento, equipe/serviço e empresa;
- concessão explícita com concedente, motivo, validade, papel, escopo e versão;
- revogação efetiva na próxima operação e revalidação por workers;
- endpoints de contexto retornando somente workspaces, empresas e capacidades concedidas;
- RLS nas tabelas sensíveis e testes de conexão reutilizada;
- MFA para papéis privilegiados e decisão de rollout da sessão segura;
- telas de Empresas e acessos, usuários, equipes e relações em páginas próprias.

### Regras bloqueantes

- master técnico não equivale a leitura universal de conteúdo;
- equipe e contrato não criam acesso;
- “Todas as empresas” é somente o conjunto autorizado no workspace atual;
- alteração exige uma empresa concreta.

### Testes e saída

Executar AT-01 a AT-04, incluindo troca de IDs, revogação durante operação e exportação. Nenhum objeto ou metadado de outra empresa pode ser revelado.

### Rastreabilidade

NX-009, NX-010 e NX-011; RN-001 a RN-009; T12 e T33.

## 7. CD-03 Adaptador GLPI homologado

### Resultado

Trocar a integração baseada em premissas por um adaptador compatível com a instalação real.

### Entregas

- cliente GLPI normalizado, isolado das regras comerciais;
- autenticação homologada e credencial técnica de menor privilégio;
- sessão/entidade isolada por operação;
- capability flags lidas do manifest produzido no CD-00;
- mapeamento real de status, prioridades, tipos, perfis e transições;
- leitura de ticket, atores, interações, tarefas, soluções, anexos, SLA/OLA e aprovações disponíveis;
- erros remotos saneados, timeouts e correlação;
- testes de contrato fixados à versão GLPI homologada;
- bloqueio explícito de recurso ausente, sem escrita direta no banco GLPI.

### Correções imediatas

- remover mapa numérico de status presumido;
- impedir que `waiting_supplier` pause prazo por padrão;
- separar login OIDC Nexora da credencial de integração GLPI;
- definir como preservar `received_at`, `accepted_at` e `remote_created_at`.

### Saída

Todas as capacidades que os próximos cortes usarão possuem prova, contrato e comportamento de indisponibilidade.

### Rastreabilidade

NX-002, NX-003, NX-005 e NX-012; RN-011 a RN-023, RN-053 a RN-056; AT-07 a AT-11.

## 8. CD-04 Comandos, eventos e reconciliação

### Resultado

Garantir que uma falha ou timeout não duplique chamados, interações, notificações ou operações financeiras.

### Entregas

- outbox com estados `PENDING`, `RUNNING`, `SUCCEEDED`, `REJECTED`, `RETRY_WAIT`, `UNCERTAIN`, `CONFLICT` e `CANCELLED`;
- lease, backoff, limite de tentativas e reserva segura por worker;
- `Idempotency-Key` com escopo, hash canônico e conflito de payload;
- inbox durável para webhooks/eventos autenticados;
- cursores incrementais, polling alternativo e reconciliação de pais e filhos;
- referência externa e tratamento de resposta perdida;
- auditoria correlacionada entre ator, comando, tentativa e efeito remoto;
- métricas de fila, idade, falhas, conflitos, incertezas e atraso de projeção;
- tela T31 de operações pendentes com inspeção e reprocessamento autorizado;
- tela T30 de integrações com capacidade, versão, último sucesso e atraso.

### Testes e saída

Executar AT-13 a AT-17: GLPI indisponível, resposta perdida, evento duplicado/atrasado, filho perdido e edição concorrente. Nenhuma repetição cega é permitida.

### Rastreabilidade

NX-013, NX-014, NX-015, NX-016, NX-017 e NX-018; RN-057 a RN-064; T30, T31 e T32.

## 9. CD-05 Central de chamados

### Resultado

Executar o ciclo essencial de incidente e solicitação no Nexora, com confirmação do GLPI e histórico preservado.

### Entregas funcionais

- T02 listagem de chamados com cards, filtros, grid, paginação e exportação conciliada;
- T03 novo chamado com empresa, relação, serviço, solicitante, tipo, descrição e referências autorizadas;
- T04 detalhe com Atendimento, Tarefas, Aprovações, Ativos, Contrato/Consumo e Histórico, expondo somente abas autorizadas;
- T05 edição com versão/If-Match e impacto antes de salvar;
- transições de triagem, atribuição, atendimento, espera, solução, aceite, rejeição, cancelamento e duplicidade;
- distinção visível entre intenção local, operação pendente e chamado GLPI confirmado;
- interações públicas e notas privadas separadas;
- solução com descrição, tipo, evidência e validação de pendências;
- links de duplicidade sem fusão destrutiva;
- inativação/retention no lugar de exclusão física de histórico.

### Dados e API

- projeções de chamados, atores, timeline, tarefas, anexos e extensões Nexora;
- `GET /tickets`, `GET /tickets/summary`, `POST /tickets`, detalhe, patch, entries, tasks e transitions;
- respostas com paginação, filtros, `data_as_of` e `sync_state`;
- DTO separado por perfil, especialmente para conteúdo público e privado.

### Testes e saída

Fluxo interno e prestadora passam por criação, atribuição, resposta, tarefa, solução, validação e fechamento sem recorrer à interface GLPI na rotina homologada.

### Rastreabilidade

NX-019 e NX-020; RN-010 a RN-015; T02 a T05; AT-05, AT-06, AT-12, AT-13, AT-14, AT-17 e AT-24.

## 10. CD-06 Portal e catálogo de serviços

### Resultado

Permitir que o solicitante encontre um serviço elegível, abra e acompanhe solicitações sem acessar conteúdo interno.

### Entregas

- T07 portal com DTO próprio, serviços disponíveis e chamados recentes;
- T08 minhas solicitações, resposta pública e aceite/rejeição de solução;
- T09 solicitação por serviço versionado;
- T10 catálogo com publicado, revisão e cobertura ausente;
- T11 criação/edição de serviço com elegibilidade, schema de formulário, fila, SLA, aprovação e cobertura;
- formulários versionados com texto, texto longo, número, data, seleção, múltipla seleção e anexo;
- condições declarativas, sem JavaScript administrativo;
- versões publicadas imutáveis e snapshot da submissão;
- aprovações simples, snapshot/hash, aprovador elegível e proibição de autoaprovação;
- upload temporário, quarentena, tipo real, hash, varredura e download autorizado.

### Testes e saída

Solicitante não recebe nota privada, custo interno, margem, arquivo não liberado ou serviço não elegível. Alterar catálogo não muda solicitações anteriores.

### Rastreabilidade

NX-022, NX-023 e NX-025; RN-024 a RN-028, RN-070 a RN-072; T07 a T11; AT-12 e AT-23.

## 11. CD-07 Filas, horas, SLA e notificações

### Resultado

Operar atendimento com distribuição, prazos confiáveis, primeira resposta real e comunicação sem duplicidade.

### Entregas

- T06 Minha fila;
- T13 operação com carga, tarefas e agenda;
- T14 apontamentos e cronômetro como rascunho até tarefa GLPI confirmada;
- T15 obrigações separando atribuição/TTO, primeira resposta, solução/TTR e OLA;
- T16 políticas versionadas, calendário IANA, feriados, vigência, pausas e reabertura;
- regra de técnico elegível: equipe + workspace + concessão + relação/serviço;
- escalonamentos por risco e vencimento sem reiniciar prazo;
- primeira resposta concluída somente por interação pública útil elegível;
- notificações internas e e-mail com uma autoridade por evento;
- preferências, deduplicação e estados comprováveis de entrega;
- T01 Visão Geral com backlog, críticos, vencidos, sem responsável e atualização por fonte.

### Testes e saída

Executar AT-07 a AT-11: assumir chamado não conclui primeira resposta; espera sem pausa autorizada continua consumindo prazo; calendário, reabertura e prioridades coincidem com o motor homologado.

### Rastreabilidade

NX-021, NX-024, NX-026 e NX-028; RN-016 a RN-023, RN-029 a RN-033 e RN-050 a RN-052; T01, T06, T13 a T16.

## 12. CD-08 Contratos, consumo, qualidade e pré-faturamento

### Resultado

Atender o modelo interno e o modelo prestadora sem confundir esforço, consumo, pré-fatura, faturamento ou recebimento.

### Entregas

- T17 e T18 contratos, versões, partes, cobertura, calendário, metas, franquias, tarifas e excedente;
- relações INTERNAL e MANAGED_SERVICE explícitas;
- revisão comercial de horas sem alterar a tarefa técnica original;
- ledger imutável, alocação, competência, arredondamento e estorno referenciado;
- T19 consumo com incluído, excedente, revisão e aprovado;
- T20 pré-faturamento com rascunho, revisão, aprovação e exportação;
- snapshot/hash da aprovação e chave externa estável;
- T21 qualidade com SLA, primeira resposta, solução, reabertura e CSAT;
- T22 rentabilidade com acesso específico e ausência de margem quando custo/receita não forem confiáveis;
- indicadores com fonte, definição, corte, amostra e `data_as_of`;
- separação entre estimado, aprovado, exportado, confirmado no ERP e recebido.
- confirmação de retorno ERP em página endereçável, com Empresa e snapshot da revisão somente leitura, referência obrigatória e proteção contra envio duplicado.

### Testes e saída

Executar AT-18 a AT-20 e AT-28. Reprocessar competência não duplica linha; alteração após aprovação gera nova versão ou ajuste; ausência de base nunca vira zero fictício.

### Rastreabilidade

NX-027, NX-029, NX-030, NX-031, NX-032 e NX-033; RN-029 a RN-036, RN-065 a RN-069; T17 a T22.

## 13. CD-09 Ativos, monitoramento e canais

### Resultado

Unificar ativos, saúde, eventos e chamados sem confundir inventário, gravidade técnica e prioridade comercial.

### Entregas

- T23 projeção de ativos GLPI e dependências identificadas por fonte;
- preservação do monitoramento Nexora já existente, inclusive bancos por empresa e banco monitorado;
- T24 eventos Zabbix/agentes, manutenção, quarentena e vínculo com incidente;
- mapeamento explícito GLPI/Zabbix/agente, sem usar nome ou IP como chave suficiente;
- correlação e deduplicação por conexão, empresa, serviço, gatilho e episódio;
- recuperação que atualiza saúde sem fechar automaticamente trabalho humano;
- T25 incidente maior com mestre restrito à operadora e comunicação saneada por empresa;
- WhatsApp somente após provedor, identidade, consentimento, template e capacidade homologados;
- ERP somente após contrato de integração e retorno confiável dos estados financeiros;
- alerta externo independente para indisponibilidade do próprio Nexora/GLPI.

### Testes e saída

Executar AT-21 e cenários de evento duplicado, ativo não mapeado, manutenção, recuperação parcial e comunicação multiempresa. Evento não mapeado entra em quarentena, nunca na empresa raiz.

### Rastreabilidade

NX-034, NX-035 e NX-036; RN-037 e RN-040 a RN-043; T23, T24 e T25.

## 14. CD-10 Segurança, recuperação e piloto profissional

### Resultado

Comprovar que o conjunto E0 a E2/P0 opera com segurança, capacidade, restauração e suporte.

### Entregas

- testes adversariais completos AT-01 a AT-26 aplicáveis;
- revisão de sessão, MFA, segredos, SSRF, anexos, exportações e logs;
- carga com volume realista e metas medidas, sem transformar referência em contrato;
- readiness separada de liveness;
- backup e restore de PostgreSQL, GLPI, documentos, Keycloak/configurações e chaves necessárias;
- restauração em rede isolada, workers pausados e reconciliação antes da retomada;
- dashboards de integração, fila, atraso, falhas e capacidade;
- manual do operador, contingência, suporte, rollback e changelog;
- modo sombra, escrita piloto e liberação gradual por empresa/feature flag;
- decisão formal de aceite por produto, técnica, operação e segurança.

### Bloqueadores de produção

Vazamento de dados, duplicidade de cobrança/chamado, fechamento indevido, perda de auditoria, ausência de restore ensaiado, dependência P0 pendente ou desvio visual não aprovado.

### Rastreabilidade

NX-037 e NX-038; RN-070 a RN-072; AT-01 a AT-26; metas e operação das seções 37 a 41 da especificação.

## 15. CD-11 Conhecimento, problemas, mudanças e automação avançada

### Resultado

Evoluir a operação depois que o núcleo profissional estiver comprovado.

### Entregas

- T28 base de conhecimento GLPI projetada com ACL antes da busca;
- T26 problemas, hipótese, causa confirmada, erro conhecido e solução de contorno;
- T27 mudanças padrão, normal e emergencial, com risco, aprovação, janela, execução e retorno;
- incidente maior avançado e comunicação multiempresa;
- runbooks versionados com risco, parâmetros tipados, alvos, pré/pós-condições, locks e aprovação;
- escalas, substituições, recorrência e calendário avançado;
- catálogo avançado e aprovações declarativas;
- proibição de shell arbitrário, credencial no frontend e execução crítica autônoma.

### Testes e saída

Executar AT-22 e AT-27 onde houver busca/automação. Timeout de ação pode resultar em `UNCERTAIN`; código de saída zero não prova recuperação.

### Rastreabilidade

NX-039 a NX-045; RN-038, RN-039 e RN-044 a RN-049; T26 a T29.

## 16. CD-12 IA assistiva e otimização

### Resultado

Adicionar assistência sem conceder à IA autoridade sobre acesso, finanças ou execução crítica.

### Entregas

- resumo de histórico, classificação sugerida, busca de solução e rascunho de resposta;
- recuperação filtrada por workspace, empresa, serviço e visibilidade antes do modelo;
- fontes identificadas, incerteza e revisão humana;
- registro de provedor, modelo, política, fontes, custo e decisão humana;
- conjunto de avaliação separado por empresa e medição de acerto/rejeição;
- sugestões de capacidade e automação sem execução autônoma;
- conteúdo de chamado/anexo tratado como dado não confiável, nunca instrução de ferramenta.

### Testes e saída

Executar AT-27 e AT-28. Conteúdo de outra empresa não pode influenciar nem aparecer na resposta. Ganho só é declarado quando medido.

### Rastreabilidade

NX-046, NX-047 e NX-048; RN-047 a RN-049.

## 17. Matriz de telas por corte

| Corte | Telas alvo |
|---|---|
| CD-01 | Base compartilhada e T12 piloto |
| CD-02 | T12 Equipes e T33 Empresas e acessos |
| CD-04 | T30 Integrações, T31 Operações pendentes e T32 Auditoria |
| CD-05 | T02 Chamados, T03 Novo, T04 Detalhe e T05 Editar |
| CD-06 | T07 Portal, T08 Minhas solicitações, T09 Solicitar serviço, T10 Catálogo e T11 Serviço |
| CD-07 | T01 Visão Geral, T06 Minha fila e T13 a T16 Operação, Apontamentos e SLA |
| CD-08 | T17 a T22 Contratos, Consumo, Pré-faturamento, Qualidade e Rentabilidade |
| CD-09 | T23 Ativos, T24 Monitoramento e T25 Incidente maior |
| CD-11 | T26 Problemas, T27 Mudanças, T28 Conhecimento e T29 Automações |

## 18. Matriz de aceite por corte

| Casos | Corte responsável |
|---|---|
| AT-01 a AT-04 | CD-02, reexecutados no CD-10 |
| AT-05 e AT-06 | CD-05, ampliados por CD-07 e CD-08 |
| AT-07 a AT-11 | CD-03 e CD-07 |
| AT-12 | CD-05 e CD-06 |
| AT-13 a AT-17 | CD-04 e CD-05 |
| AT-18 a AT-20 | CD-08 |
| AT-21 | CD-09 |
| AT-22 | CD-11 |
| AT-23 | CD-06 |
| AT-24 | Todos os cortes com indicadores |
| AT-25 | CD-10 |
| AT-26 | CD-01 e todos os cortes de interface |
| AT-27 | CD-11 e CD-12 |
| AT-28 | CD-08 e CD-12 |

## 19. Portas de liberação

### Porta A Fundação

CD-00 a CD-04 concluídos. Autoriza desenvolver fluxos ITSM produtivos.

### Porta B Atendimento

CD-05 a CD-07 concluídos. Autoriza piloto operacional interno e prestadora sem cobrança.

### Porta C Operação profissional

CD-08 a CD-10 concluídos. Autoriza piloto profissional conforme escopo, empresas e integrações aprovadas.

### Porta D Evolução

CD-11 concluído. Autoriza conhecimento, mudanças e automação controlada. CD-12 permanece opcional e depende de política de IA.

## 20. Controle de cobertura

Uma alteração na especificação deve atualizar este arquivo no mesmo pull request. Todo item NX-001 a NX-048 está associado a pelo menos um corte. Todo caso AT-01 a AT-28 possui corte responsável. Toda tela T01 a T33 possui corte de implementação.

Se um item for adiado, o corte registra motivo, impacto, comportamento seguro temporário e responsável pela decisão. Adiar não autoriza mock funcional nem flexibilização de isolamento.

