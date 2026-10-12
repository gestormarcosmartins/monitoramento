# Manual de operação, contingência e piloto

## Liberação gradual

Cada capacidade deve começar em `off`, seguir para `shadow`, depois `write_pilot` em uma empresa autorizada e somente então `enabled`. A alteração exige motivo e usuário aprovador no endpoint `/api/v1/feature-flags/{feature}`. Não é permitido usar uma empresa raiz como destino implícito.

## Saúde e observabilidade

- Liveness: `/api/v1/health/live` prova apenas que o processo responde.
- Readiness: `/api/v1/health/ready` verifica PostgreSQL e Redis antes de admitir tráfego.
- Operar pelas filas/outbox, quarentena de eventos, integrações e auditoria. Atraso, dead-letter, evento não mapeado e dependência externa degradada exigem tratamento explícito.
- A indisponibilidade simultânea da Nexora e do GLPI deve ser vigiada por monitor externo, fora desta pilha.

## Backup e restauração

1. Execute `scripts/backup.ps1` para produzir PostgreSQL, GLPI, configuração Keycloak e material criptográfico necessário, com manifesto SHA-256.
2. Copie o pacote para cofre externo cifrado e aplique retenção.
3. Ensaie restore apenas em rede isolada e destino descartável.
4. Pause `worker` e `action-orchestrator`; restaure com `scripts/restore-isolated.ps1 -ConfirmIsolatedRestore`.
5. Valide migrações, RLS, contagens, anexos/documentos, ledger, outbox e referências GLPI.
6. Reprocesse/reconcilie pendências antes de retomar workers. Nunca presuma sucesso financeiro ou fechamento de chamado.

## Contingência e rollback

- Retorne a feature flag a `off` ou `shadow`; preserve registros e auditoria.
- Pause consumidores quando houver risco de escrita duplicada.
- Não reverta migrations destrutivamente. Publique migração corretiva.
- Para timeout de ação, use estado `uncertain` até verificação pós-condição; código de saída zero não comprova recuperação.

## Gate do piloto profissional

Produto, técnica, operação e segurança devem registrar aceite. Bloqueiam produção: vazamento entre empresas, duplicidade de cobrança/chamado, fechamento indevido, perda de auditoria, restore não ensaiado, dependência P0 pendente ou desvio visual não aprovado.

Evidências mínimas: AT-01 a AT-26 aplicáveis, relatório de carga com percentis e volume, restore datado, varredura de segredos/anexos/SSRF, matriz de acesso, changelog e plano de suporte.
