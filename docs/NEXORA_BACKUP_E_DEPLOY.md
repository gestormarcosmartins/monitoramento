# Nexora — backup diário e roteiro de deploy (servidor Oracle)

Servidor de produção: `nexora-producao` (Oracle Linux 9), projeto em `/opt/nexora` via Docker Compose. `/opt/nexora` **não é um repositório git**: o código chega por cópia de arquivos (seção 3).

## 1. Backup full diário
**O que faz** (`scripts/backup-daily.sh`, agendado por `nexora-backup.timer` às 03:15 + atraso aleatório de até 5 min):
- `pg_dump -Fc` de `central_monitor`, `keycloak` e `zabbix`; `mariadb-dump` do GLPI (consistente, `--single-transaction`);
- arquivo com `.env`, `.secrets`, `compose.yaml`, `infra/`, `services/backend`, `services/web/src` e `scripts/`;
- **verificação antes de valer:** cada dump precisa ser legível pelo `pg_restore --list` com conteúdo, o GLPI precisa terminar com a marca de conclusão, e o manifesto SHA-256 é gerado. Se algo falhar, o backup é descartado, o serviço falha (visível em `systemctl status nexora-backup`) e **nenhum backup antigo é apagado**;
- envio para a máquina de backup por `rsync` sobre SSH, **conferido do outro lado** com os mesmos hashes, e só então a poda remota (padrão: 30 dias); local mantém 7 dias.

**Estado atual:** instalado e testado **somente em modo local** (12 s, 51 MB, 5/5 hashes ok). Falta ligar a máquina remota (seção 2).

**Importante:** o `.env` entra no backup porque contém `BUSINESS_SIGNING_KEY` (cujas chaves públicas ficam fixadas nos Business Workers) e `PUSH_TOKEN_KEY` (que decifra os tokens de push). Perder esse arquivo exige gerar chaves novas e refixar os workers. Guarde o destino remoto com acesso restrito, de preferência cifrado em repouso.

**Operar**
```bash
sudo systemctl list-timers nexora-backup.timer      # próxima execução
sudo systemctl start nexora-backup.service           # rodar agora
journalctl -u nexora-backup.service -n 50            # resultado da última
ls -lh /home/opc/nexora-backups/daily/               # backups locais
```
Configuração em `/etc/nexora-backup.conf` (modelo: `scripts/nexora-backup.conf.example`).

## 2. Ligar a máquina de backup (Oracle free)
1. No servidor de produção, gerar uma chave **só para backup**: `ssh-keygen -t ed25519 -N "" -f /home/opc/.ssh/nexora_backup_ed25519`.
2. Na máquina de backup, acrescentar `nexora_backup_ed25519.pub` ao `~/.ssh/authorized_keys` do usuário que receberá os arquivos (de preferência restrito a `rsync`/`sha256sum` e à pasta de backup).
3. Aceitar a chave do host uma vez (`ssh -i … usuario@maquina true`) para o `StrictHostKeyChecking=yes` do script funcionar.
4. Preencher `REMOTE_HOST`, `REMOTE_USER`, `REMOTE_DIR`, `REMOTE_KEY` em `/etc/nexora-backup.conf` e rodar `sudo systemctl start nexora-backup.service`.
5. **Teste de restauração** (faça ao menos uma vez e depois a cada trimestre): restaurar o dump mais recente num PostgreSQL descartável (`pg_restore --create -d postgres arquivo.dump`) e abrir o `glpi.sql` num MariaDB de teste. Um backup que nunca foi restaurado não é prova de nada.

## 3. Roteiro de deploy (usado em 14/10/2026 para a entrega de push, Business Monitor e Worker)
1. **Backup verificado antes de qualquer mudança** (`sudo systemctl start nexora-backup.service` ou o `pg_dump` manual).
2. Copiar só os arquivos alterados: `git archive HEAD <arquivos> | ssh opc@servidor 'cd /opt/nexora && tar x --no-same-owner -f -'`. **Nunca sobrescrever** `infra/caddy/Caddyfile` (configuração do domínio).
3. **Migrations novas** (`.sql`): aplicar como `platform_admin` em transação única e **antes** de reiniciar a API:
   `docker compose exec -T postgres psql -U platform_admin -d central_monitor -v ON_ERROR_STOP=1 --single-transaction < arquivo.sql`
   depois marcar (`INSERT INTO cm_schema_migrations(version) VALUES ('arquivo.sql') ON CONFLICT DO NOTHING`) e conceder `SELECT, INSERT, UPDATE, DELETE` ao `central_app` em cada tabela criada (o `platform_admin` vira dono delas).
4. `.env`: acrescentar variáveis novas **sem exibir valores**; guardar cópia (`cp -p .env <pasta do backup>/env.before`).
5. `docker compose build api worker web` e `docker compose up -d api worker web`. O reinício deixa `api`/`worker` fora por alguns segundos.
6. Conferir `docker compose logs --since 3m api worker web | grep -E 'Traceback|ERROR|psycopg.errors'` (deve ser vazio), `/api/v1/health/ready` e as rotas novas.

**Reverter (se algo quebrar):** (a) código: `tar xzf <backup>/arquivos-antes.tar.gz -C /opt/nexora` e `docker compose build api worker web && docker compose up -d api worker web`; (b) banco, **só se a migration causou dano**: as tabelas novas são aditivas e o código antigo as ignora, então normalmente basta reverter o código. Para restaurar de fato: `pg_restore --clean --if-exists -U platform_admin -d central_monitor <backup>/central_monitor.dump`.

## 4. Estado do ambiente após o deploy de 14/10/2026
- Migrations aplicadas até a **052**. API em modo `db` (sem RabbitMQ); o serviço `rabbitmq` existe no `compose.yaml`, mas **não foi iniciado** e `RABBITMQ_URL` está vazio.
- `PUSH_TOKEN_KEY` e `BUSINESS_SIGNING_KEY` geradas no servidor. `EXPO_PROJECT_IDS` vazio: **nenhum aparelho será aceito para push até existir o projeto EAS** (a rota responde 422/503, sem efeito colateral).
- Nenhum Business Worker, fonte ou monitor foi criado: nada novo roda até o Master configurar.
- Pendências de higiene: arquivos de `/opt/nexora` com permissão 666/777; disco em ~66% após limpar o cache de build do Docker.
