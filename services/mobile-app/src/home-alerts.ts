// Tela de Início: só alertas ABERTOS, com 4 cards de severidade que funcionam como filtro. Lógica pura, testável sem React Native.
export type SeverityKey = 'critical' | 'high' | 'medium' | 'low';
type Card = { key: SeverityKey; label: string; severities: string[]; color: string };

// Severidades do servidor (estilo Zabbix): disaster, high, average, warning, info. Baixo agrupa warning + info para que nenhum alerta aberto fique de fora.
export const SEVERITY_CARDS: Card[] = [
  { key: 'critical', label: 'Críticos', severities: ['disaster'], color: '#F87171' },
  { key: 'high', label: 'Alto', severities: ['high'], color: '#FB923C' },
  { key: 'medium', label: 'Médio', severities: ['average'], color: '#FBBF24' },
  { key: 'low', label: 'Baixo', severities: ['warning', 'info'], color: '#60A5FA' },
];

export const cardOf = (severity: string): Card => SEVERITY_CARDS.find((card) => card.severities.includes(severity)) || SEVERITY_CARDS[3];

/** Clicar num card seleciona; clicar de novo desmarca. Nenhum selecionado = todos (padrão ao abrir). */
export function toggleCard(selected: SeverityKey[], key: SeverityKey): SeverityKey[] {
  return selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key];
}

/** Severidades enviadas ao servidor: união dos cards selecionados, sem repetir. Nenhum card = sem filtro. */
export function severityParam(selected: SeverityKey[]): string | null {
  const chosen = SEVERITY_CARDS.filter((card) => selected.includes(card.key)).flatMap((card) => card.severities);
  return chosen.length ? chosen.join(',') : null;
}

type Params = { selected?: SeverityKey[]; scope?: string; cursor?: string | null; pageSize?: number };

/** Lista da Início: sempre status=open, mais grave primeiro. */
export function homeListParams({ selected = [], scope, cursor, pageSize = 25 }: Params): string {
  const parts = [`page_size=${pageSize}`, 'status=open', 'sort=severity'];
  const severity = severityParam(selected);
  if (severity) parts.push(`severity=${encodeURIComponent(severity)}`);
  if (scope && scope !== 'all') parts.push(`tenant_ids=${encodeURIComponent(scope)}`);
  if (cursor) parts.push(`cursor=${encodeURIComponent(cursor)}`);
  return parts.join('&');
}

/** Contagem de um card (independente da seleção: o número do card não muda ao filtrar). */
export function homeCountParams(card: SeverityKey, scope?: string): string {
  const found = SEVERITY_CARDS.find((item) => item.key === card)!;
  const parts = ['page_size=1', 'status=open', `severity=${encodeURIComponent(found.severities.join(','))}`];
  if (scope && scope !== 'all') parts.push(`tenant_ids=${encodeURIComponent(scope)}`);
  return parts.join('&');
}
