# Evidência de ensaio de carga — 11/10/2026

## Escopo

Ensaio local, limitado e somente leitura, alternando as rotas de Alertas, Incidentes e Topologia. Foram utilizadas a API e a base PostgreSQL reais do ambiente, em uma segunda instância efêmera sem porta publicada. A autenticação de desenvolvimento ficou restrita a esse processo; a autorização por concessão e a RLS permaneceram ativas.

Parâmetros: 600 requisições, concorrência 20, timeout individual de 15 segundos.

## Resultado válido

| Medida | Resultado |
|---|---:|
| HTTP 200 | 600 |
| Taxa de erro | 0% |
| p50 | 2.293,61 ms |
| p95 | 3.963,22 ms |
| p99 | 4.286,36 ms |
| Máxima | 4.653,08 ms |

O processo efêmero foi encerrado automaticamente ao final. A API principal permaneceu saudável.

## Achado corretivo

O ensaio revelou que, após a adoção de RLS forçada, duas rotinas de autorização consultavam concessões sem definir `app.tenant_id`. Usuários não-Master recebiam 403 mesmo com concessão ativa. As rotinas passaram a consultar `cm_access_grants` somente por `tenant_connection`, e testes regressivos foram adicionados.

Uma rodada negativa também confirmou que empresa não autorizada retorna 403 em todas as requisições. Ela não foi usada nas métricas acima.

## Leitura do resultado

O ensaio confirma integridade funcional sob a carga aplicada, mas não define SLA. As latências mostram oportunidade clara de otimizar abertura/reuso de conexões e autorização antes de estabelecer um objetivo de desempenho para produção.

## Reensaio após pool de conexões

A API passou a usar um pool PostgreSQL compartilhado por processo, configurável por `DATABASE_POOL_MIN_SIZE`, `DATABASE_POOL_MAX_SIZE` e `DATABASE_POOL_TIMEOUT_SECONDS`. A devolução da conexão mantém commit/rollback obrigatório e o contexto RLS continua transacional. O readiness passou a expor somente métricas operacionais do pool, sem credenciais.

O mesmo probe foi repetido em processo efêmero, com a mesma Empresa, rotas, quantidade, concorrência e timeout:

| Medida | Antes | Com pool | Variação |
|---|---:|---:|---:|
| HTTP 200 | 600 | 600 | estável |
| Taxa de erro | 0% | 0% | estável |
| p50 | 2.293,61 ms | 619,12 ms | -73,0% |
| p95 | 3.963,22 ms | 997,12 ms | -74,8% |
| p99 | 4.286,36 ms | 1.090,07 ms | -74,6% |
| Máxima | 4.653,08 ms | 1.186,82 ms | -74,5% |

Após o reensaio, o readiness informou pool sem espera (`requests_waiting=0`) e o teste adversarial alternando duas Empresas na mesma conexão foi aprovado novamente. O resultado continua sendo evidência local de engenharia, não um SLA de produção.
