# Push mobile (Expo) — situação

O push mobile oficial está na `main` (PRs #12 e #13): backend (`services/backend/central_monitor/expo_push.py`, migration `050_expo_push_notifications.sql`, rotas `/api/v1/notificacoes/*`), app (`services/mobile-app/src/push.ts`) e central de notificações na web.

Este documento descrevia um backend alternativo que existia na branch `feat/expo-push-backend` e **foi retirado** para evitar duas implementações. Não use os contratos antigos (tabelas `cm_push_*`, `PUSH_TOKEN_KEY`).

## Como o Business Monitor usa o push
O Business Monitor chama `enqueue_notification` da `main` ao abrir uma ocorrência (`business_evaluation._notify`): domínio `BUSINESS`, destinatários com inscrição no indicador **e** `business.view`; texto genérico, sem valores. Quem tem acesso à infraestrutura não recebe push de negócio e vice-versa. Teste: `test_business_notification_reaches_only_users_with_business_access`.

## Pendências de ambiente
`EXPO_PROJECT_IDS` (um `projectId` EAS por ambiente) e, se exigido, `EXPO_ACCESS_TOKEN` no `.env` do servidor; credenciais FCM/APNs no EAS.
