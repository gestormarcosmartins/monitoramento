# Atualização automática do agente de monitoramento

Decisão de produto (17/10/2026): o agente Windows passa a se atualizar sozinho, com versões assinadas, anéis e volta automática.
A chave de assinatura é a do servidor da Central (`BUSINESS_SIGNING_KEY`, a mesma do Business Worker). Isso é mais simples e **menos seguro**
do que assinar offline: quem invadir a Central pode publicar uma versão maliciosa para todos os agentes. Mitigações: o anel padrão é
`hold`, versões nascem `canary`, a promoção exige evidência de um agente canário saudável, e há revogação com volta automática.

## Fluxo
1. Você gera o pacote (`Nexora.Agent.exe` + `Microsoft.Data.SqlClient.SNI.dll` num zip) e copia para `/opt/nexora/releases`.
2. `agent_release_cli register` calcula o SHA-256 e cadastra a versão no canal **canary**.
3. Cada agente verifica a Central a cada 60 minutos. A Central só oferece a versão a agentes cujo **anel** permite (`canary` recebe canary e stable; `stable` só stable; `hold` nada).
4. O agente recebe um **manifesto assinado (Ed25519)** preso à empresa, ao agente, à plataforma, ao hash, ao tamanho e a uma validade de 1 hora, confere tudo,
   baixa o pacote, confere tamanho e SHA-256 e apenas **prepara** o pacote.
5. Ele aciona a tarefa agendada `NexoraAgentUpdate` (SYSTEM) → `apply-update.ps1`: copia o pacote para uma área só de administradores, o executável instalado
   **reverifica** (`--verify-staged`), descompacta, para o agente, troca os arquivos (**nunca** `agentsettings.json`), religa e espera um heartbeat aceito.
6. Sem confirmação em 7 minutos, os arquivos anteriores são restaurados e o agente é religado. O resultado (`APPLIED`/`ROLLED_BACK`/`FAILED`) é reportado à Central.
7. Com um agente canário `APPLIED` e online, você promove a versão para **stable** e ela chega aos agentes do anel `stable`.

## Comandos (no servidor, na pasta `/opt/nexora`)
```bash
sudo docker compose exec -T api python -m central_monitor.agent_release_cli list
sudo docker compose exec -T api python -m central_monitor.agent_release_cli register --version 2.2.1 --platform win-x64 --file Nexora-Agent-2.2.1-win-x64.zip --notes "descrição"
sudo docker compose exec -T api python -m central_monitor.agent_release_cli ring --agent "JRI - SRVJRIBD" --ring canary
sudo docker compose exec -T api python -m central_monitor.agent_release_cli promote --version 2.2.1 --platform win-x64
sudo docker compose exec -T api python -m central_monitor.agent_release_cli revoke --version 2.2.1 --platform win-x64 --reason "motivo"
```
As mesmas operações existem na API do Master (`/api/v1/master/agent-releases`, `/api/v1/master/agents/{id}/update-ring`). Ainda não há tela no painel.

## Limites
- **Instalação inicial manual:** o primeiro executável com o atualizador (2.2.0) precisa ser instalado à mão em cada servidor (o configurador cria a tarefa `NexoraAgentUpdate`). Depois disso as versões chegam sozinhas.
- O **configurador** (`Nexora.Agent.Config.exe`) e o script `apply-update.ps1` não se atualizam sozinhos.
- Somente Windows x64 nesta etapa. O servidor é reiniciado do ponto de vista do agente por alguns segundos durante a troca.
- Reinicialização do Windows durante o período de teste não dispara a volta automática (a versão nova segue rodando; a anterior fica em `<Instalação>.previous`).

## Segurança (resumo)
O pacote só é aceito se: assinatura válida; empresa, agente e plataforma batem; versão maior (ou volta explícita de uma versão revogada); validade em dia;
tamanho e SHA-256 batem; o zip não tem caminhos absolutos, `..`, links simbólicos e contém `Nexora.Agent.exe`. A verificação é refeita por um binário já instalado,
sobre uma cópia numa pasta que usuários comuns não escrevem.
