# Nexora Mobile — implementação Android e iOS

## Rastreabilidade

- corte: CD-05;
- histórias: NX-019 e NX-020;
- domínio: operação de chamados projetados e reconciliados com o GLPI;
- cliente: `services/mobile-app` (React Native/Expo);
- API de usuário: `/api/v1/auth/me` e `/api/v1/tickets`.

## Decisões

O aplicativo usa autenticação OIDC Authorization Code com PKCE e armazena os tokens no cofre seguro do sistema operacional. Nenhum segredo de cliente técnico é distribuído no APK ou IPA.

Uma operação de chamado mostra `sync_status=pending` como pendente de confirmação. O aplicativo não comunica sucesso remoto do GLPI antes da projeção/reconciliação.

A URL do servidor é configurável. HTTP é aceito somente para desenvolvimento local; a implantação Oracle deve usar HTTPS válido.

## Plataformas

- Android: pacote `br.com.simmservices.nexora`; perfil EAS `preview` produz APK.
- iOS: bundle `br.com.simmservices.nexora`; assinatura e IPA exigem Apple Developer e macOS/Xcode ou EAS Build.
- redirect OIDC nativo: `nexora://auth`.

## Gate antes da distribuição

1. Definir a URL HTTPS definitiva da Oracle.
2. Aplicar a atualização do cliente `central-monitor` no Keycloak da Oracle.
3. Executar login real, troca de Empresa e todos os fluxos de chamado em Android e iOS.
4. Validar tarefas pendentes, conflitos, perda de rede, token expirado e reconciliação do GLPI.
5. Configurar credenciais de assinatura Android e Apple Developer.
6. Publicar por canal interno antes das lojas.
