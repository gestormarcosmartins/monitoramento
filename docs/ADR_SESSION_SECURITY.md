# ADR — Segurança de sessão OIDC e MFA

Data: 11 de outubro de 2026  
Estado: adotado para o piloto local; evolução BFF planejada para implantação pública.

## Decisão

- O cliente web usa Authorization Code com PKCE S256, `state` e `nonce` aleatórios por tentativa.
- O `nonce` retornado no ID Token deve coincidir antes que qualquer token seja armazenado.
- O realm exige TOTP para todos os usuários. Isso é deliberadamente mais restritivo que exigir somente dos papéis privilegiados, pois o papel Nexora é concedido por Empresa depois da autenticação e não deve ser duplicado no Keycloak.
- Refresh tokens são rotacionados sem reutilização; access tokens duram cinco minutos; sessão ociosa dura trinta minutos e a sessão absoluta, oito horas.
- O realm exige TLS fora de localhost, bloqueia força bruta e limita tentativas.
- Mudança de papel, Empresa ou revogação continua sendo revalidada pelo backend e pelos workers em cada operação; a presença de um token não substitui autorização do objeto.

## Armazenamento no navegador

O piloto mantém tokens em `sessionStorage`, nunca em `localStorage`. O risco residual de leitura por script permanece e exige CSP, ausência de conteúdo administrativo executável e dependências controladas.

Antes da exposição pública, a sessão deve migrar para um BFF com cookie `Secure`, `HttpOnly`, `SameSite=Lax/Strict`, proteção CSRF e tokens mantidos apenas no servidor. Essa migração não pode alterar os contratos `/api/v1`, Empresa ou autorização por objeto.

## Rollout

1. Realm importado já nasce com TOTP e limites de sessão.
2. Realm existente recebe a mesma política pela API administrativa.
3. Sessões existentes são encerradas após a mudança para impedir continuidade sem o novo fluxo.
4. Usuários configuram TOTP no próximo login.
5. A implantação pública permanece bloqueada até TLS/DNS e BFF estarem aprovados.

## Evidência

- `infra/keycloak/central-monitor-realm.json` é a fonte versionada.
- O gate do frontend verifica PKCE, envio e validação de `nonce`.
- A política aplicada é lida de volta do Keycloak sem expor segredos administrativos.
