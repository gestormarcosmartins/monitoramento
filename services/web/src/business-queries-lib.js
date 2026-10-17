export const COLUMN_TYPES = ['string', 'integer', 'decimal', 'boolean', 'date', 'datetime'];

/** Parses "nome:tipo" lines into the column contract. Returns {error} when a line is malformed. */
export function parseColumns(text) {
  const columns = [];
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const [name, type] = line.split(':').map(x => x.trim());
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name || '') || !COLUMN_TYPES.includes(type)) return {error: `Linha inválida: "${line}". Use nome:tipo (${COLUMN_TYPES.join(', ')}).`};
    columns.push({name, type});
  }
  return columns.length ? {columns} : {error: 'Informe ao menos uma coluna.'};
}
export function parseParameters(text) {
  const raw = String(text || '').trim();
  if (!raw) return {parameters: {}};
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('not an object');
    return {parameters: value};
  } catch { return {error: 'Parâmetros devem ser um objeto JSON, por exemplo {"hoje": "$today"}.'}; }
}
export const columnsText = (schema) => (schema || []).map(c => `${c.name}:${c.type}`).join('\n');

export const ERROR_HELP = {
  database_ref_not_configured: 'O Worker não encontrou a conexão desta fonte. O nome lógico configurado no agente precisa ser idêntico ao da fonte na Central.',
  db_error: 'O banco recusou ou não conseguiu executar a consulta. Confira o usuário de leitura, a permissão na view e a conexão do agente.',
  query_timeout: 'A consulta passou do tempo máximo. Simplifique a consulta ou aumente o tempo máximo (até 60 s).',
  result_too_large: 'O resultado passou do limite de linhas ou bytes. A consulta deve devolver poucas linhas (ex.: uma linha agregada).',
  schema_mismatch: 'As colunas devolvidas não batem com as declaradas. Confira os nomes, a ordem e o tipo em "Colunas".',
  type_mismatch: 'Um valor devolvido não bate com o tipo declarado da coluna.',
  invalid_signature: 'O Worker recusou o job por assinatura inválida. Verifique a chave de assinatura instalada no agente.',
  job_expired: 'O job expirou antes de rodar. O Worker pode estar parado ou sem acesso à Central.',
  lease_expired: 'O Worker demorou demais e perdeu o job. Tente de novo.',
  worker_revoked: 'O worker foi revogado.',
  source_inactive: 'A fonte de dados está inativa.',
  unexpected_error: 'Erro inesperado no Worker. Veja o arquivo business.log do agente.',
};

/** Mensagem clara para o resultado de um teste: o que aconteceu e o que fazer. */
export function testMessage(test) {
  if (!test) return null;
  const rev = `revisão ${test.revision}`;
  if (['PENDING', 'LEASED', 'RUNNING'].includes(test.state)) {
    return {tone: 'info', text: test.slow
      ? `Teste da ${rev} enviado, mas o Worker ainda não respondeu. Atualize a página em instantes ou confira se o Worker está Online.`
      : `Teste da ${rev} enviado ao Worker. Aguardando a execução (costuma levar até 30 segundos)…`};
  }
  if (test.state === 'SUCCEEDED') {
    return {tone: 'success', text: `Teste da ${rev} executado com sucesso: ${test.row_count ?? 0} linha(s) em ${test.duration_ms ?? '—'} ms. Agora você pode publicar.`};
  }
  const help = errorHelp(test.error_code);
  return {tone: 'error', text: `Teste da ${rev} não passou (${test.error_code || test.state}). ${help}`};
}

// Códigos do SQL Server vêm como mssql_<número> (SqlException.Number ou HRESULT do Windows).
const MSSQL_HELP = {
  '-2146893019': 'O certificado do SQL Server não é confiável. No configurador do agente, marque "Confiar no certificado do servidor" (certificado autoassinado, banco na mesma máquina) e salve de novo.',
  '-2146893022': 'O certificado do SQL Server não confere com o nome do servidor. Use o nome exato do certificado ou marque a confiança no certificado no configurador.',
  '-2': 'A conexão com o SQL Server passou do tempo. Confira se o servidor está no ar e se a instância e a porta estão acessíveis.',
  '18456': 'O SQL Server recusou o login. Confira o usuário e a senha do usuário de leitura (nexora_ro) no configurador do agente.',
  '4060': 'O usuário não consegue abrir o banco configurado. Confira o nome do banco e se o usuário existe nele.',
  '229': 'O usuário de leitura não tem permissão de SELECT na view. Peça ao DBA para conceder.',
  '208': 'A tabela ou view da consulta não existe nesse banco (confira o schema, ex.: pub.nome_da_view).',
  '207': 'Um nome de coluna da consulta não existe na view. Confira os nomes.',
  '53': 'O servidor SQL não foi encontrado na rede. Confira o nome do servidor/instância no configurador.',
  '-1': 'Não foi possível alcançar o SQL Server. Confira servidor, instância, firewall e se o serviço SQL Server Browser está ativo (instâncias nomeadas).',
};

export function errorHelp(code) {
  if (!code) return 'Veja o código do erro e o arquivo business.log do agente.';
  if (ERROR_HELP[code]) return ERROR_HELP[code];
  const match = /^mssql_(-?\d+)$/.exec(code);
  if (match) return MSSQL_HELP[match[1]] || `O SQL Server devolveu o erro ${match[1]}. Veja o arquivo business.log do agente e a documentação do código.`;
  return 'Veja o código do erro e o arquivo business.log do agente.';
}

/** 300 -> "5 min (300 s)", 3600 -> "1 h (3600 s)": o valor em segundos fica sempre visível. */
export function intervalLabel(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n % 3600 === 0) return `${n / 3600} h (${n} s)`;
  if (n % 60 === 0) return `${n / 60} min (${n} s)`;
  return `${n} s`;
}
