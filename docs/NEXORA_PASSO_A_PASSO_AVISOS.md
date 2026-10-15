# Nexora: colocar os avisos de e-mail e WhatsApp para funcionar

Estado medido na produção em 15/10/2026: configuração global de e-mail (Gmail) e de WhatsApp (Evolution) **existem**, mas 0 regras de notificação, 76 de 76 monitores sem responsável e nenhuma homologação de WhatsApp. Por isso nunca houve envio.

Como os avisos funcionam (código atual):
- Dois caminhos independentes, os dois só para **chamado criado** (`incident.created`, texto FALHA) e **chamado resolvido** (`incident.resolved`, texto NORMALIZADO):
  1. **Regras** (Configurações → Notificações): valem para a empresa inteira; uma regra = um canal + um destino.
  2. **Responsável do monitor**: o aviso vai para a equipe/usuário ligado ao monitor.
- Se os dois estiverem ativos para o mesmo destino, a pessoa recebe duas mensagens. **Comece só com as regras** (mais simples: não precisa mexer em 76 monitores).
- WhatsApp só envia depois da homologação da empresa (provedor + 4 declarações). O botão "Testar WhatsApp" **não exige** homologação, então ele passa mesmo com o canal bloqueado para avisos reais.

## Passo 1: conferir o Evolution (WhatsApp)
Configurações → WhatsApp (admin ou gestor): o estado da instância tem que estar **conectado** (`open`). Se pedir QR Code, leia com o celular do número que enviará os avisos.
- A instância se chama `chaveou`, a mesma do outro produto. Se o número é compartilhado, as mensagens de alerta sairão do mesmo WhatsApp.
- O Evolution usa WhatsApp Web (não é a API oficial da Meta). Prefira um número dedicado a alertas, para não arriscar o número principal.

## Passo 2: testar o e-mail
Configurações → E-mail (SMTP) → enviar teste para você.
- Gmail exige **senha de app** (conta com verificação em duas etapas); a senha normal da conta é recusada.
- Se falhar, a tela mostra o erro do servidor de e-mail.

## Passo 3: completar os contatos das equipes
Administração → Equipes. Hoje: **Morada/SIMM** (1 e-mail, 1 número), **jri/JRI** (2 grupos de WhatsApp, **sem e-mail**), **Chaveou** (sem equipe).
- Número de WhatsApp: só dígitos com país e DDD, ex.: `5511999999999`. Grupo: o identificador terminado em `@g.us`.

## Passo 4: criar as regras (uma por canal, por empresa)
Configurações → Notificações → Nova regra. Repita para Morada, jri e Chaveou.
| Campo | Valor sugerido |
|---|---|
| Canal | e-mail (e outra regra para WhatsApp) |
| Destino | e-mail ou número/grupo |
| Prioridade mínima | **alta** (incidentes de severidade alta/desastre viram prioridade crítica) |
| Eventos | `incident.created` **e** `incident.resolved` (o padrão da API traz só `incident.created`) |

## Passo 5: homologar o WhatsApp por empresa (sem tela; só admin)
O código exige, por empresa: provedor, identidade verificada, capacidade verificada, consentimento e template aprovado. São **declarações suas**: só marque `true` se for verdade (os destinatários consentiram em receber). Não há tela; use o SQL abaixo no servidor (como `platform_admin`) ou o `PUT /api/v1/channel-capabilities/whatsapp` (admin).

```sql
INSERT INTO cm_channel_capabilities(tenant_id,channel,provider,identity_verified,consent_verified,template_approved,capability_verified,metadata)
SELECT id,'whatsapp','Evolution API',true,true,true,true,'{}'::jsonb FROM cm_tenants WHERE active AND name IN ('Morada','jri')
ON CONFLICT (tenant_id,channel) DO UPDATE SET provider=excluded.provider,identity_verified=excluded.identity_verified,
  consent_verified=excluded.consent_verified,template_approved=excluded.template_approved,capability_verified=excluded.capability_verified;
```
Inclua `'Chaveou'` na lista quando ela tiver destinatário.

## Passo 6 (opcional, depois): ligar equipes aos monitores
Só se quiser rotear por equipe em vez de regra. Pelo painel (Monitoramento → Serviços → editar, campo equipe responsável) ou em lote, por empresa:
```sql
UPDATE cm_monitors m SET responsible_team_id = t.id
FROM cm_teams t JOIN cm_tenants e ON e.id=t.tenant_id
WHERE t.tenant_id=m.tenant_id AND e.name='Morada' AND t.name='SIMM' AND m.responsible_team_id IS NULL AND m.responsible_user_id IS NULL;
```
Não deixe a mesma pessoa na regra **e** na equipe, ou ela recebe em dobro.

## Passo 7: conferir que chegou
- Chamados **novos** disparam o aviso; os 9 chamados antigos já abertos não reenviam (só avisam quando forem resolvidos).
- Consulta somente leitura no servidor:
```sql
SELECT 'regra' AS origem, channel, status, count(*) FROM cm_notification_deliveries GROUP BY 2,3
UNION ALL SELECT 'responsavel', channel, status, count(*) FROM cm_routing_deliveries GROUP BY 2,3;
SELECT created_at::timestamp(0), channel, left(last_error,120) FROM cm_notification_deliveries WHERE status='failed' ORDER BY created_at DESC LIMIT 5;
```
- Sem destinatário, o chamado passa a mostrar `notification.skipped` na linha do tempo.
- WhatsApp bloqueado aparece como entrega `failed` com a mensagem "WhatsApp bloqueado: homologue provedor, identidade, consentimento, template e capacidade".

## Antes de ligar tudo
- A carência de 10 min (PR #18) já está na produção, então o volume de chamados caiu de ~450/dia para os chamados reais.
- Ainda existem chamados reais abertos por configuração: o monitor "Serviços essenciais" acusa `MSSQLSERVER`/`SQLSERVERAGENT` parados em servidores que provavelmente não têm SQL Server. Revise antes de ligar, senão a recuperação desses chamados vai disparar mensagens antigas.
