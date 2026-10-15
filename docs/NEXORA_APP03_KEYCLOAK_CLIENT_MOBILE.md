# APP-03 — Login por usuário e senha no app (client `nexora-mobile`)

O app passa a ter tela própria de usuário, senha e código OTP. O Keycloak valida as credenciais num client **separado** (`nexora-mobile`); o painel web (`central-monitor`) não muda. O botão "Entrar pelo navegador seguro" continua como alternativa.

## 1. Criar o client no Keycloak (você aplica)

Console do Keycloak → realm **central-monitor** → **Clients → Create client**:

1. General: Client type `OpenID Connect`, Client ID `nexora-mobile`, Name `Nexora Mobile`.
2. Capability config: **Client authentication = Off** (público), **Standard flow = Off**, **Direct access grants = On**. Salvar.
3. **Client scopes → nexora-mobile-dedicated → Add mapper → By configuration → Audience**:
   - Name `audience-central-monitor`
   - Included Client Audience `central-monitor`
   - Add to access token = On, Add to ID token = Off. Salvar.
4. Confirme que `central-monitor` continua com Direct access grants **Off**.

(O arquivo `infra/keycloak/central-monitor-realm.json` já traz esse client para novos ambientes.)

## 2. Servidor (API)

A API aceita tokens cujo `azp` seja `central-monitor` ou um client de `OIDC_EXTRA_CLIENTS` (padrão `nexora-mobile`). Não é preciso definir a variável; sem o deploy desta versão, o mapper de audience já basta. Sem migração.

## 3. Comportamento no app

- Usuário e senha; campo de código OTP aparece quando o servidor o exige (ou pelo link "Tenho código…").
- Erros amigáveis: credenciais inválidas, conta bloqueada (proteção do Keycloak: 5 falhas, espera até 15 min), ação pendente da conta (entrar uma vez pelo navegador), sem conexão, serviço indisponível. A senha nunca é guardada nem registrada, e é limpa após falha.
- Renovação e logout usam o client que emitiu a sessão; sessões antigas (navegador) continuam funcionando.
- Após o login, o app registra o aparelho para push como antes.

## 4. Teste manual (critérios do APP-03)

1. Login correto (com OTP, se o usuário tiver) entra sem abrir o navegador.
2. Senha errada mostra mensagem clara e não derruba o app.
3. Fechar e reabrir o app mantém a sessão; passada a expiração do access token, ele renova sozinho.
4. Sair e entrar com outro usuário mostra só os dados dele.
5. Usuário com conta pendente: mensagem orienta a usar o navegador seguro.
