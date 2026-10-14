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
