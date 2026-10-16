# Business Worker dentro do agente de monitoramento

Decisão de produto (16/10/2026): o Worker de consultas de negócio roda **dentro do agente de monitoramento** em vez de ser um serviço separado. O modo independente (com token) continua existindo no código e no painel, mas o caminho recomendado é este.

## O que muda para quem instala
- **Sem token de worker.** O worker usa a identidade do agente (a mesma do cadastro por chave de instalação).
- **Uma tela só**: o aplicativo `Nexora.Agent.Config.exe` ganhou a aba **Consultas de negócio** (fonte, servidor, banco, usuário, senha, certificado e **Testar conexão**).
- A senha do banco fica cifrada pelo Windows (DPAPI, máquina) em `C:\ProgramData\Nexora\Agent\business\<nome>.dat` e nunca vai à Central.
- **Desligado por padrão.** Agente sem a opção marcada se comporta exatamente como antes.

## Salvaguardas
- O teste de conexão e o "Salvar e instalar" **recusam usuário sysadmin** (como o `sa`) e conexão inválida antes de gravar qualquer coisa.
- Conexão sempre criptografada; só confia em certificado autoassinado se marcado.
- Valores com `;` ou quebra de linha nos campos da conexão são recusados (sem injeção de parâmetros).
- O código do Worker é o mesmo do serviço independente: assinatura Ed25519 do job verificada, consulta somente leitura validada na Central, limites de tempo/linhas/bytes, 1 consulta por vez.
- Se o Worker falhar, o agente continua: a falha é registrada e a tentativa se repete.

## Custo (isolamento)
No modo independente, o Worker era outro processo. Embutido, uma consulta ruim divide o processo com o monitoramento. Os limites acima reduzem o risco, mas não o eliminam. Se o monitoramento e as consultas precisarem ficar isolados de novo, use o modo independente.

## Passo a passo (exemplo JRI)
1. **Banco:** criar `nexora_ro` na base e dar `SELECT` só nas views necessárias.
2. **Central (Master, empresa jri):** *Negócios → Workers → Novo worker → Dentro de um agente já instalado* e escolher o agente. Nenhum token é gerado.
3. **Central:** *Negócios → Fontes → Nova fonte* (SQL Server, nome lógico igual ao do configurador, worker do agente).
4. **Máquina do agente:** abrir `Nexora.Agent.Config.exe` → aba **Consultas de negócio** → marcar, preencher, **Testar conexão** → **Salvar e instalar**.
5. **Central:** o worker fica **Online** no primeiro sinal; criar a consulta, testar e publicar em *Negócios → Consultas*.

## Backend
- Migração `056`: `cm_business_workers.agent_id` (worker de agente tem `token_hash` nulo; worker independente, `agent_id` nulo).
- Gateway `/api/v1/agents/business/*`: aceita `X-Worker-ID/Token` **ou** `X-Agent-ID/Token`, nunca os dois.
- `GET /api/v1/master/business/agents`, `POST /api/v1/master/business/workers/from-agent`, `GET /api/v1/agents/business/identity`.
