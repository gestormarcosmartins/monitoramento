# Evidência de ensaio de restauração — 11/10/2026

## Resultado

Ensaio concluído com sucesso em 11 de outubro de 2026, entre 14:02:14Z e 14:02:52Z.

O bundle foi validado pelo manifesto SHA-256 antes da restauração. PostgreSQL e MariaDB foram restaurados em containers, volumes e rede Docker temporários, sem portas publicadas e sem interromper ou alterar os bancos ativos.

## Validações do destino restaurado

| Validação | Resultado |
|---|---:|
| Migrations do PostgreSQL | 29 |
| Empresas restauradas | 3 |
| Eventos de outbox preservados | 89 |
| Lançamentos no ledger de consumo | 0 |
| Tabelas `cm_*` com RLS habilitada | 69 |
| Tabelas do GLPI restauradas | 442 |

O script retornou o estado `passed`. Após a validação, todos os containers, volumes e a rede temporários foram removidos. Uma inspeção por prefixo `nexora-restore-` não encontrou recursos residuais.

## Continuidade do ambiente ativo

Depois do ensaio, PostgreSQL, MariaDB/GLPI, Redis, Keycloak, Zabbix Web, API e orquestrador permaneceram ativos; os serviços com healthcheck permaneceram saudáveis. Gateway, frontend e worker também permaneceram em execução.

## Procedimento reproduzível

1. Executar `scripts/backup.ps1` em um destino protegido.
2. Preservar o bundle e seu `manifest.sha256.json` sem modificações.
3. Executar `scripts/restore-isolated.ps1 -Bundle <bundle> -ConfirmIsolatedRestore`.
4. Anexar a saída JSON do ensaio à evidência operacional.

O script de restauração cria somente destinos descartáveis com nomes únicos, exige confirmação explícita, não publica portas e sempre remove seus recursos no bloco de finalização.
