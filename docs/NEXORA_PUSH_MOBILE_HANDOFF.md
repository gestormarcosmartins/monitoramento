# NexoraPush — o que o desenvolvedor do app precisa entregar

**Versão:** 1.0 · **Data:** 13/10/2026 · **Base:** `Nexora_Especificacao_Push_Notifications_v1.docx`
**Escopo deste documento:** `services/mobile-app/` (Expo SDK 57, `br.com.simmservices.nexora`). O backend já está pronto (seção 1). O app hoje **não tem nenhum código de push**: não há `expo-notifications`, registro de token, canais Android nem tratamento de toque na notificação.

> Antes de escrever código Expo, siga o `services/mobile-app/AGENTS.md`: leia a versão do `expo` no `package.json` e consulte a documentação **dessa versão** (`https://docs.expo.dev/versions/v57.0.0/` e `https://docs.expo.dev/push-notifications/overview/`). Instale pacotes sempre com `npx expo install`. Os nomes de API citados abaixo devem ser conferidos nessa documentação.

## 1. O que o backend já entrega (não refazer)

Rotas (todas exigem `Authorization: Bearer <token do Keycloak>`):

| Rota | Uso no app |
|---|---|
| `POST /api/v1/notificacoes/dispositivos-push` | Registrar/atualizar o token do aparelho (idempotente) |
| `GET /api/v1/notificacoes/dispositivos-push` | Tela "Meus dispositivos" |
| `DELETE /api/v1/notificacoes/dispositivos-push/{id}` | Logout / revogar aparelho (204) |
| `GET /api/v1/notificacoes?unread_only=&page=&page_size=` + header `X-Tenant-ID` | Central de notificações da empresa |
| `POST /api/v1/notificacoes/{id}/abrir` + `X-Tenant-ID` | Registrar abertura **e obter o destino autorizado** |
| `POST /api/v1/notificacoes/{id}/confirmar` + `X-Tenant-ID` | Confirmar ciência (só quando `requires_ack = true`) |

Registro — corpo (nomes em camelCase, exatamente assim):

```json
{
  "expoPushToken": "ExponentPushToken[xxxxxxxxxxxxxxxx]",
  "platform": "android",
  "installationId": "uuid-gerado-uma-vez-pelo-app",
  "expoProjectId": "uuid-do-projeto-eas",
  "appVersion": "1.0.13",
  "permissionStatus": "granted"
}
```

Respostas e erros que o app deve tratar: `200 {id, enabled}` · `422` (token inválido ou `expoProjectId` não permitido neste ambiente) · `503` (push não configurado no servidor) · `401/403` (sessão) · `404` em `/abrir` e `/confirmar` (**sem acesso, revogado, expirado ou inexistente: a mesma resposta nos quatro casos**) · `409` em `/confirmar` (notificação não exige ciência).

Conteúdo do push (campo `data`): `{ type, notificationId, tenantId, domain, actionRef }`. São **apenas identificadores**. O título e o corpo vêm genéricos e sem valores sensíveis. Canais Android usados: `alerts` (alertas altos/críticos) e `default`.

Domínios: `INFRA` (incidentes) e `BUSINESS` (ocorrências de negócio). Quem tem acesso a um não ganha o outro.

## 2. O que o desenvolvedor do app deve implementar

### 2.1 Projeto e build (P0)
1. Criar/ligar o projeto EAS (`eas init`) e gravar o `projectId` em `app.json` (`extra.eas.projectId`). **Um projeto EAS por ambiente (dev, hml, prod)**; o backend só aceita os `projectId` listados em `EXPO_PROJECT_IDS`.
2. `npx expo install expo-notifications expo-device expo-constants` e adicionar o plugin `expo-notifications` no `app.json` (ícone de notificação monocromático e cor da marca `#2563FF`).
3. Credenciais: **FCM (Android)** e **APNs (iOS)** enviadas ao EAS (`eas credentials`). Nada disso vai para o Git nem para o código do app.
4. Perfis `eas.json` por ambiente (`development`, `preview`, `production`) com o `projectId` correspondente. Push **não funciona no Expo Go em Android recente**: usar development build (`eas build --profile development`) — conferir na documentação da SDK 57.
5. Testar em **aparelhos físicos** Android e iOS. Emulador não valida entrega real.

### 2.2 Permissão e token (P0)
- Pedir a permissão do sistema **depois do login**, com uma tela explicando o motivo, e não na abertura fria do app. Se negada: o app continua funcionando, mostra o estado "Notificações desativadas" com atalho para os ajustes do sistema e **não insiste** a cada abertura.
- Criar os canais Android `alerts` (importância alta, vibração) e `default` **antes** de pedir a permissão.
- Obter o token com `getExpoPushTokenAsync({ projectId })`.
- `installationId`: UUID gerado **uma vez** e guardado no `expo-secure-store`. Não usar identificadores de hardware.

### 2.3 Registro e ciclo de vida (P0)
- Registrar (`POST /dispositivos-push`) após login, ao voltar para primeiro plano se passou de 24 h e quando o token mudar (listener de mudança de token). Reenviar é seguro (idempotente).
- Guardar o `id` devolvido para o logout.
- **Logout:** chamar `DELETE /dispositivos-push/{id}` **antes** de apagar a sessão. Se estiver offline, enfileirar a revogação e repetir; mesmo assim o backend deixa de enviar para quem perdeu o acesso.
- Trocar de usuário no mesmo aparelho: o token passa para o novo usuário (o backend desativa o vínculo anterior).
- Nunca registrar em log, analytics ou relatório de erro o token, o `Authorization` ou o conteúdo das notificações.

### 2.4 Receber e abrir (P0)
- Handler em primeiro plano: mostrar banner para `alerts`; tocar som só em `alerts`.
- Tratar o toque na notificação em três situações: app em primeiro plano, em segundo plano e **fechado** (resposta inicial ao abrir).
- Fluxo seguro de deep link — **o payload nunca autoriza nada**:
  1. Ler `tenantId` e `notificationId` do `data`.
  2. Confirmar que `tenantId` está na lista de empresas do usuário (`/api/v1/auth/me`). Se não estiver, mostrar "Sem acesso a esta empresa".
  3. Chamar `POST /api/v1/notificacoes/{notificationId}/abrir` com `X-Tenant-ID: tenantId`.
  4. Só então navegar usando o `action_type`/`action_ref` **da resposta**: `incident` → detalhe do chamado/incidente; `business_occurrence` → ocorrência de negócio (somente se o usuário tiver acesso a Negócio).
  5. Em `404`: mostrar "Notificação indisponível, expirada ou sem acesso". Não revelar qual dos casos ocorreu.
- Se a notificação exigir ciência (`requires_ack`), a tela de destino mostra o botão **Confirmar ciência** (`POST .../confirmar`).

### 2.5 Telas novas (P1, seguir o padrão visual Nexora)
- **Central de notificações:** lista paginada da empresa selecionada, filtro "não lidas", selo de contagem na barra inferior, estados de carregando/vazio/erro, puxar para atualizar. Itens de Negócio só aparecem para quem tem acesso.
- **Meus dispositivos:** plataforma, versão do app, última atividade, estado; ação "Revogar este aparelho".
- **Configurações de notificação:** estado da permissão do sistema e atalho para ajustes. (Preferências por categoria e horário de silêncio ficam para uma etapa posterior, e dependem do backend.)
- Acessibilidade: alvos de toque de 44 pt, rótulos para leitor de tela, contraste das cores oficiais (Midnight `#0B1F3B`, Electric Blue `#2563FF`, Cyan `#06B6D4`).

### 2.6 Testes que o app deve ter
Automatizados (`node --test`, como já existe em `tests/`): leitura segura do `data` (campos ausentes ou extras), regra "empresa não pertence ao usuário", montagem do corpo de registro, fila de revogação offline.

Em aparelho real (evidência: vídeo ou captura sem dados de clientes):

| Caso | Resultado esperado |
|---|---|
| Permissão negada | App funciona; estado "desativado" + atalho; sem insistência |
| Android e iOS reais | Notificação chega via Expo; toque abre a tela certa |
| App aberto, em segundo plano e fechado | Os três tratam o toque corretamente |
| Logout | Dispositivo revogado; nenhum push depois |
| Troca de token | Registro novo; apenas um aparelho ativo por instalação |
| Usuário sem acesso a Negócio | Não recebe nem vê notificação de Negócio |
| Empresa diferente | Toque em notificação de empresa sem acesso mostra "Sem acesso" |
| Acesso revogado depois do envio | `/abrir` devolve 404 e o app mostra a mensagem neutra |
| Rede indisponível no registro e no logout | Repetição automática, sem travar o app |

## 3. Critérios de aceite do app
1. Push real chegando em Android e iOS físicos, nos ambientes de homologação.
2. Permissão, token, logout e troca de usuário testados.
3. Nenhum token ou conteúdo sensível em logs; sem credenciais no repositório.
4. Toque na notificação sempre passa por `/abrir`; nada é aberto só pelo `data`.
5. Telas conforme o padrão visual e acessíveis; testes automatizados passando; `npx tsc --noEmit` e `npx expo lint` sem erros.

## 4. O que o time de infraestrutura precisa configurar (fora do app)
- No `.env` do servidor: `PUSH_TOKEN_KEY` (chave Fernet), `EXPO_PROJECT_IDS` (um `projectId` por ambiente, separados por vírgula) e, se o Expo exigir, `EXPO_ACCESS_TOKEN`. Sem `PUSH_TOKEN_KEY` o registro responde 503, e sem `EXPO_PROJECT_IDS` nenhum aparelho é aceito.
- Chave Fernet: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Guardar em cofre; perder a chave invalida os tokens já registrados (os aparelhos se registram de novo no próximo login).
- Credenciais FCM/APNs ficam no EAS, nunca no servidor Nexora.

## 5. Semântica de entrega (não prometer ao usuário mais do que existe)
`SENT_TO_EXPO` e `PROVIDER_ACCEPTED` significam que o Expo e o FCM/APNs aceitaram a mensagem. **Não significam que o aparelho exibiu nem que a pessoa leu.** Abertura e ciência são eventos do usuário, registrados por `/abrir` e `/confirmar`. O modo "Não perturbe" do sistema não é contornado.
