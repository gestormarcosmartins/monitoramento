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
  const help = ERROR_HELP[test.error_code] || 'Veja o código do erro e o arquivo business.log do agente.';
  return {tone: 'error', text: `Teste da ${rev} não passou (${test.error_code || test.state}). ${help}`};
}
