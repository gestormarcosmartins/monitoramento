# Central Monitor

Fundacao do MVP multiempresa de monitoramento baseado em Zabbix.

## Arquitetura deste primeiro incremento

- Nginx como unico ponto de entrada HTTP local
- API FastAPI para saude e contratos da Central
- Worker consolidado para sincronizacao, regras, incidentes e notificacoes
- Orquestrador de acoes separado e restrito a catalogo homologado
- Zabbix Server e Zabbix Web
- Keycloak para identidade
- Redis para fila e cache
- Uma instancia PostgreSQL com tres bases isoladas: `central_monitor`, `zabbix` e `keycloak`

O Compose usa configuracoes de desenvolvimento. TLS, segredos externos, backups, limites de recursos e acesso administrativo privado serao adicionados antes da implantacao na Oracle.

## Preparacao

1. Copie `.env.example` para `.env`.
2. Substitua todas as senhas e a chave secreta.
3. Inicie os servicos:

```powershell
docker compose up --build -d
```

4. Consulte:

- Central API: `http://localhost/api/v1/health/live`
- Zabbix: `http://localhost/zabbix/`
- Keycloak: `http://localhost/auth/`

## Decisoes de seguranca

- A API nunca recebe `tenant_id` confiando apenas no corpo da requisicao.
- O orquestrador nao aceita shell, PowerShell ou comandos arbitrarios.
- Acoes precisam existir no catalogo, estar habilitadas para o ativo e respeitar idempotencia e limites.
- Tokens do Zabbix permanecem apenas no backend.
- Series temporais permanecem no Zabbix; a Central armazena estados, alertas, incidentes e auditoria.

## Proximos incrementos

1. Migrations da Central, RLS e modelo de tenants.
2. Integracao OIDC com Keycloak.
3. Adaptador JSON-RPC do Zabbix e outbox transacional.
4. Cadastro de clientes, ambientes e ativos.
5. Catalogo persistente de acoes e trilha de auditoria.

