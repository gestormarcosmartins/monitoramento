# Roteiro de teste — monitor de consulta (Business Monitor v1.0)

Objetivo: criar, testar e publicar um monitor de consulta e ver o indicador ser alimentado, usando a API (ainda não há telas para monitores, execuções e workers).
Os comandos são PowerShell (Windows). Use uma **empresa de teste** e um **banco de teste** com PostgreSQL; SQL Server nunca foi exercitado.

## 0. Preparação

**Variáveis** (a cada ~5 min o token expira; repita o item "token" quando aparecer 401):
```powershell
$BASE   = "https://nexora.simmservices.com.br"     # endereço público do Nexora
$TENANT = "COLE-AQUI-O-ID-DA-EMPRESA"              # DevTools > Rede > qualquer chamada /api > cabeçalho X-Tenant-ID
$TOKEN  = "COLE-AQUI-O-TOKEN"                      # DevTools > Console: sessionStorage.getItem("access_token")
$H = @{ Authorization = "Bearer $TOKEN"; "X-Tenant-ID" = $TENANT }
function Api($method, $path, $body = $null) {
  $req = @{ Method = $method; Uri = "$BASE$path"; Headers = $H; ContentType = "application/json" }
  if ($body) { $req.Body = ($body | ConvertTo-Json -Depth 8) }
  Invoke-RestMethod @req
}
```
Entre no Nexora **como Master técnico** (com TOTP) na empresa de teste antes de copiar o token.

**Banco de teste** (onde o Worker vai consultar), no PostgreSQL da máquina do Worker:
```sql
CREATE DATABASE erp_demo;
\c erp_demo
CREATE TABLE contas (documento text, cliente text, cpf text, valor_vencido numeric(12,2), vencimento date);
INSERT INTO contas VALUES ('D1','Cliente A','111.111.111-11',450.50,current_date-10),
                          ('D2','Cliente B','222.222.222-22',500.00,current_date-3);
CREATE ROLE nexora_ro LOGIN PASSWORD 'troque-esta-senha';
GRANT CONNECT ON DATABASE erp_demo TO nexora_ro;
GRANT USAGE ON SCHEMA public TO nexora_ro;
GRANT SELECT ON contas TO nexora_ro;      -- somente leitura, somente o necessário
```

## 1. Ativar o produto e criar o indicador
```powershell
Api PUT  "/api/v1/business/enable" @{ enabled = $true; reason = "teste de monitores de consulta" }
$ind = Api POST "/api/v1/business/indicators" @{ code="contas_vencidas"; name="Contas vencidas (R$)"; unit="R$"; direction="above"; warning_value=500; critical_value=2000 }
$IND = $ind.id
```
Esperado: `enabled = True` e um `id` de indicador. (Se o indicador exigir permissão, o Master precisa da capacidade `business.monitor.create`.)

## 2. Criar o Worker (o token aparece UMA vez)
```powershell
$w = Api POST "/api/v1/master/business/workers" @{ name = "worker-teste"; capacity = 1 }
$WORKER = $w.id
$w.worker_token | Set-Content -NoNewline worker.token     # guarde; não será mostrado de novo
```

## 3. Instalar o Worker na máquina que enxerga o banco
Gere o executável (`agent\build-business-worker.ps1 -Runtime win-x64` ou `linux-x64`) e obtenha a chave pública de assinatura:
```powershell
Invoke-RestMethod "$BASE/api/v1/agents/business/signing-key" -Headers @{ "X-Tenant-ID"=$TENANT; "X-Worker-ID"=$WORKER; "X-Worker-Token"=(Get-Content worker.token) }
```
Confirme a chave com quem administra o servidor, depois instale:
```powershell
# Windows (PowerShell como Administrador)
.\install-business-worker.ps1 -ServerUrl $BASE -TenantId $TENANT -WorkerId $WORKER `
  -Token (Read-Host -AsSecureString "worker token") -SigningPublicKey "<CHAVE-PUBLICA>" `
  -DatabaseRef erp_demo -ConnectionString "Host=127.0.0.1;Port=5432;Database=erp_demo;Username=nexora_ro;Password=troque-esta-senha"
```
```bash
# Linux (root)
./install-business-worker.sh --server-url "$BASE" --tenant-id "$TENANT" --worker-id "$WORKER" \
  --signing-key "<CHAVE-PUBLICA>" --token-file worker.token --database-ref erp_demo --connection-file conexao.txt
```
Confira que ele está vivo: `GET /api/v1/business/workers` deve mostrar `ONLINE` em até 1 minuto.
Para teste no mesmo servidor, o banco precisa de TLS válido ou da opção de desenvolvimento `AllowPlaintextLoopbackDatabase` (ver `docs/NEXORA_BUSINESS_QUERY_MONITORS.md`, seção 5).

## 4. Cadastrar a fonte e o monitor
`database_ref` é o nome lógico (mesmo usado na instalação). A consulta usa `@parametro`; `"$today"` vira a data do dia.
```powershell
$src = Api POST "/api/v1/master/business/data-sources" @{ name="erp-demo"; worker_id=$WORKER; dialect="postgres"; database_ref="erp_demo" }

$mon = Api POST "/api/v1/master/business/monitors" @{
  name = "contas-vencidas"; description = "Soma das contas vencidas"
  indicator_id = $IND; source_id = $src.id; interval_seconds = 60
  query = "SELECT documento, cliente, cpf, valor_vencido FROM contas WHERE vencimento < @hoje ORDER BY documento"
  parameters = @{ hoje = '$today' }
  columns = @(@{name="documento";type="string"}, @{name="cliente";type="string"}, @{name="cpf";type="string"}, @{name="valor_vencido";type="decimal"})
  metric = @{ kind = "sum"; column = "valor_vencido" }
}
$MON = $mon.id
```
Tipos de coluna: `string, integer, decimal, boolean, date, datetime`. Métricas: `row_count, sum, max, count_nonnull, first_value`.

## 5. Testar e publicar
```powershell
$t = Api POST "/api/v1/master/business/monitors/$MON/revisions/1/test"      # 202 + execution_id
Start-Sleep 15
Api GET "/api/v1/business/monitors/$MON/executions"                         # estado SUCCEEDED
Api GET "/api/v1/business/monitors/$MON/executions/$($t.execution_id)/result"
Api POST "/api/v1/master/business/monitors/$MON/revisions/1/publish"        # só aceita revisão testada
```
Esperado: o teste termina `SUCCEEDED` com 2 linhas; depois de publicado, uma nova execução roda a cada 60 s. Em `GET /api/v1/business/indicators` o indicador mostra `last_value = 950.50` e estado de atenção (acima de 500). Na tela **Business Monitor** ele aparece com a ocorrência/atualização, e com o GLPI ativo, o chamado na categoria "Negócio".

## 6. Verificações de segurança e falha (o que deve ser RECUSADO ou tratado)
| Teste | Como | Esperado |
|---|---|---|
| SQL de escrita | criar monitor com `DELETE FROM contas` | **422**, nada é criado |
| Vários comandos | `SELECT 1; DROP TABLE contas` | **422** |
| Publicar sem teste | `publish` numa revisão nova sem `test` | **409** |
| Não-Master | repetir um `POST /api/v1/master/...` com token de usuário comum | **403** |
| Worker parado | parar o serviço do Worker, esperar 5 min | worker `OFFLINE`; as execuções ficam pendentes e expiram |
| Banco fora | parar o PostgreSQL de teste | execução `FAILED` (erro sanitizado, sem texto do driver); após 5 falhas seguidas o monitor é **suspenso** |
| Token do Worker revogado | `POST /api/v1/master/business/workers/$WORKER/revoke` | o Worker passa a receber 401 e para; jobs pendentes são cancelados |
| Revisão nova | `POST .../monitors/$MON/revisions` e republicar | a anterior vira `SUPERSEDED`; execuções pendentes dela são canceladas |

## 7. Conferir o RabbitMQ (ligado em produção)
```powershell
Api GET "/api/v1/master/business/broker/status"         # fila do worker e profundidade da DLQ
Api GET "/api/v1/master/business/broker/dead-letters"   # mensagens mortas da empresa (deve estar vazio)
```
No servidor: `docker compose exec rabbitmq rabbitmqctl list_queues name messages` mostra `business.jobs.w.<id do worker>`.

## 8. Limpeza
```powershell
Api POST "/api/v1/master/business/monitors/$MON/status" @{ action="disable"; reason="fim do teste" }
Api POST "/api/v1/master/business/workers/$WORKER/revoke"
```
Remova o serviço do Worker e o banco `erp_demo` da máquina de teste.
