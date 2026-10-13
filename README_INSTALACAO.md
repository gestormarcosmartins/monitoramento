# Nexora Business Monitor — Pacote de código P0/P1 · V0.2

**Data:** 13/10/2026  
**Base de compatibilidade:** [gestormarcosmartins/monitoramento](https://github.com/gestormarcosmartins/monitoramento), branch `main` inspecionada com último migration `043_backfill_global_notification_settings_v2.sql`.  
**Status:** CÓDIGO PARA INTEGRAÇÃO/HOMOLOGAÇÃO. **NÃO IMPLANTADO NEM HOMOLOGADO EM PRODUÇÃO.**  
**Intervenção no repositório:** nenhuma; este ZIP é um overlay do checkout.

## 1. O que foi incluído

1. **BM-00/BM-03:** banco e permissões separadas para o produto Business, sem conceder acesso a administradores técnicos por implicação, grants por Empresa, próprio usuário, unidade, departamento ou processo.
2. **BM-01:** evolução do agente .NET existente (não criar outro agente): cache da última configuração, fila SQLite WAL de eventos Business, ACK individual, limite de armazenamento e identificação por coletor.
3. **BM-02:** motor de conectores Business com SQL Server (view de leitura), PostgreSQL (view), REST HTTPS/JSON, CSV de linha única e XLSX de célula definida; limites e saneamento, configurações sem senhas no servidor.
4. **BM-04:** valores históricos/idempotência, regra acima/abaixo, stale, regra versionada, janela de horário, histerese/cooldown, ocorrências próprias, avaliações periódicas pelo worker.
5. **BM-05:** interfaces de indicador, visão geral, processos/etapas, regras, unidades, ocorrências, Hub Studio/conectores, alertas internos, relatórios e concessões, com APIs/menus independentes de Ambientes.
6. **BM-06 (primeiro corte):** formulário visual de conector, validação estática segura, publicação por segundo usuário com revisão e rollback. **Teste remoto assíncrono, auditoria visual da execução e editor low-code avançado ainda exigem homologação/evolução.**
7. **HML/V0.2:** scripts guardados de integração, matriz de aceite, instruções, testes unitários e verificações estáticas. Nenhum dado financeiro sintético foi criado.

## 2. Pré-requisitos

- Checkout integral do repositório Nexora na branch de trabalho a partir da `main` atual; não usar esta entrega como repositório completo.
- Backup/restauração testada do PostgreSQL e base de homologação isolada.
- Python/venv com `services/backend/requirements.txt`; PostgreSQL sob papel de migration com DDL autorizado, usuário de API sem BYPASSRLS.
- Node.js/npm de acordo com o frontend do projeto; .NET SDK 8 para compilação do HUB; acesso a fontes reais somente leitura em HML.
- Confirmar se as migrations **045/046/047 ainda estão livres**. O pacote foi baseado em `main` cujo último migration era 043. Se o número estiver ocupado, **parar e reconciliar**; não sobrescrever migrations aplicadas.
- Verificar privilégios `central_app` vs dono de schema; o projeto tem histórico de DDL falhando por permissão. Usar fluxo de migração do repositório com papel próprio.

## 3. Como colocar no GitHub sem substituir a aplicação

```bash
# executar em uma cópia local do checkout oficial
git checkout -b feat/business-monitor-v02
# extrair o ZIP NA RAIZ do checkout (mesmas pastas agent/services/tools/docs)
python tools/apply_business_monitor.py
python tools/apply_business_monitor_v02.py
python tools/verify_business_monitor_package.py

git diff --check
git status --short
# revisar todos os arquivos alterados; fazer commit e push só após aprovação da equipe
```

Os dois `apply_*` exigem âncoras exatas e **não** criam commits, conexões remotas ou deploy. O primeiro aplica a integração V0.1; o segundo adiciona os componentes V0.2. Se a V0.1 já foi aplicada, o primeiro script é idempotente para as âncoras conhecidas. Se aparecer **anchor mismatch**, **interromper**, confrontar com a `main` atual e adaptar conscientemente; nunca forçar com substituição global.

Arquivos originais modificados pelos integradores: `services/backend/central_monitor/main.py`, `worker.py`, `services/web/src/main.jsx`, `services/web/src/business-monitor.jsx`, `agent/Nexora.Agent/AgentWorker.cs`, `Program.cs`, `SqlServerCollector.cs`, `CentralClient.cs`, `Nexora.Agent.csproj`.

### Build e testes

```bash
# do root do checkout Nexora
export PYTHONPATH="$PWD/services/backend"
python -m unittest discover -s services/backend/tests -p 'test_business*.py' -v
python -m compileall -q services/backend/central_monitor

cd services/web
npm ci
npm test
npm run build
npm run test:responsive
cd ../..

# no host com .NET SDK 8
dotnet restore agent/Nexora.Agent/Nexora.Agent.csproj
dotnet build agent/Nexora.Agent/Nexora.Agent.csproj -c Release
```

A migration será executada no processo do projeto; **não execute a API/worker em PROD para descobrir se a migration funciona**. Validar as migrations em banco clonado com snapshot e simulação de RLS/rollback primeiro.

## 4. Ativação explícita por empresa

O produto permanece **desabilitado** sem `PUT /api/v1/business/enable` por Master técnico autenticado e motivo. A permissão de ler indicadores **não é concedida automaticamente ao Master**. Um gestor autorizado concede permissões específicas a usuário que já tenha vínculo com a empresa:

```http
PUT /api/v1/business/enable
X-Tenant-ID: <UUID_EMPRESA>
Authorization: Bearer <TOKEN_OIDC_MASTER>
Content-Type: application/json

{"enabled":true,"reason":"Piloto autorizado para esta empresa"}
```

```http
PUT /api/v1/business/grants-scoped
X-Tenant-ID: <UUID_EMPRESA>
Authorization: Bearer <TOKEN_OIDC_GESTOR_DE_ACESSO>
Content-Type: application/json

{"user_id":"<UUID_USUARIO>","capability":"business.view","scope_type":"company","scope_id":null,"active":true,"reason":"Gestão autorizada de indicadores"}
```

Para administrador de Negócios, conceder também capabilities pertinentes: `business.monitor.create`, `business.monitor.update`, `business.process.manage`, `business.rule.manage`, `business.source.manage`, `business.collector.test`, `business.collector.publish`, `business.occurrence.manage`, `business.export` e `business.grants.manage` **somente conforme necessidade**. Nunca conceder todos por padrão. Autoconcessão é bloqueada; edição de grant não concede acesso de outra empresa.

## 5. Implantação dos conectores no HUB

- **SQL Server:** instalar view que retorna **uma linha com um único número**, nome `vw_nexora_*`; conceder somente SELECT ao login do agente. Configurar certificado SQL válido e `SqlTrustServerCertificate=false` para coleta Business.
- **PostgreSQL:** credencial read-only local por coletor: variável de ambiente `NEXORA_BUSINESS_POSTGRES_DSN_<UUID_EM_MAIÚSCULO_COM_UNDERSCORES>`; TLS VerifyFull e SELECT apenas em views homologadas.
- **REST:** URL HTTPS sem redirecionamento; o domínio deve constar na lista local `NEXORA_BUSINESS_REST_ALLOWED_HOSTS` (separada por vírgulas); token opcional exclusivo por coletor em `NEXORA_BUSINESS_REST_TOKEN_<UUID_EM_MAIÚSCULO_COM_UNDERSCORES>`. Bloquear egress a redes de metadata, loopback e redes não autorizadas também no firewall.
- **CSV/XLSX:** diretório de leitura exclusivo do agente `NEXORA_BUSINESS_DATA_DIRECTORY`; arquivo relativo, limite de 1 MB, formatos restritos; CSV simples com cabeçalho e **uma linha**; XLSX usa aba+célula (número). Acesso a planilhas completas e layouts complexos exige adaptador homologado.
- **Fila local:** `business-events.sqlite3` sob o diretório de dados do agente, com WAL. Não apagar arquivos `.sqlite3`, `-wal`, `-shm` manualmente. Monitorar tamanho e backlog e usar backup compatível com WAL.

Nunca colocar DSNs, chaves, senhas, tokens ou planilhas com dados reais no Git. Provisionar localmente usando mecanismo seguro e permissões do serviço. Configurações do Studio contêm somente metadados e referências não secretas.

## 6. Aceite obrigatório V0.2 (bloqueadores de produção)

| Caso | Como validar | Status desta entrega |
|---|---|---|
| V02-AT01 | Usuário com só Infra acessando Business: menu oculto e API 403 | CÓDIGO; teste E2E externo pendente |
| V02-AT02 | Usuário com só Business não pode ler infraestrutura sensível | CÓDIGO; E2E externo pendente |
| V02-AT03 | RLS e permissões com duas Empresas e troca de contexto | CÓDIGO; integração DB pendente |
| V02-AT04 | Criar/editar indicador e limites + visualização real | CÓDIGO; E2E pendente |
| V02-AT05 | Enviar amostras duplicadas, divergentes e fora de ordem | CÓDIGO; teste HTTP/DB pendente |
| V02-AT06 | Desconectar HUB, coletar offline, reiniciar, reenviar sem perda | CÓDIGO; teste em Windows/Linux pendente |
| V02-AT07 | Coletar SQL Server/PostgreSQL/REST/CSV/XLSX autorizados | CÓDIGO; testes de fonte real pendentes |
| V02-AT08 | Publicar coletor em segundo usuário, fazer rollback, revogar acesso | CÓDIGO; teste integrado pendente |
| V02-AT09 | Processo com dependências de etapas e prazos | CÓDIGO; E2E pendente |
| V02-AT10 | Regra com cooldown/calendário/stale periódico | CÓDIGO; teste integrado pendente |
| V02-AT11 | Interface 320 a 1920 px + leitor de tela | JSX verificável; Playwright HML pendente |
| V02-AT12 | Testes de regressão de Ambientes, Zabbix/GLPI e restore | NÃO EXECUTADOS em ambiente real |
| V02-AT13 | GLPI opcional para ocorrências Business e canais externos | NÃO IMPLEMENTADO — integração controlada posterior |
| V02-AT14 | Teste remoto da fonte via Hub Studio (rodar query em HUB) | NÃO IMPLEMENTADO — Studio valida contrato local |

**Regra de qualidade:** não informar que V0.2 está homologada enquanto a matriz não estiver verde com evidência DEV/HML. Os componentes P0/P1 principais foram codificados, mas persistem itens que dependem de ambientes reais e adaptações de integração.

## 7. Próximas entregas e evolução

A V0.2 tem arquitetura e código extensível; antes da liberação comercial ainda serão necessários: homologação externa e correção de incompatibilidades, teste remoto do Hub Studio, gestão robusta de segredos por cliente/integração, controle de acesso executado sob perfis reais, integração opcional com GLPI/WhatsApp, prevenção de DNS rebind por controle de rede, atualização automática assinada do agente, teste de restauração, SLOs e observabilidade avançada. **IA permanece fora do escopo**, conforme decisão anterior.

Não anexar dados demonstrativos, credenciais ou capturas com informações de clientes. O código deve permanecer em branch e ser aprovado em PR antes do merge na `main`.
