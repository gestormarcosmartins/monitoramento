// Filtros da aba Chamados (status e criticidade). Lógica pura, testável sem React Native.
export type TicketFilter = { status: string; priority: string };
export const NO_FILTER: TicketFilter = { status: '', priority: '' };

// '' = todos; 'open' = em aberto (não resolvidos/fechados), enviado como actionable=true ao servidor.
export const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Todos' }, { value: 'open', label: 'Em aberto' }, { value: 'new', label: 'Novo' }, { value: 'triaged', label: 'Triado' },
  { value: 'assigned', label: 'Atribuído' }, { value: 'acknowledged', label: 'Reconhecido' }, { value: 'in_progress', label: 'Em atendimento' },
  { value: 'waiting_customer', label: 'Aguardando cliente' }, { value: 'waiting_supplier', label: 'Aguardando fornecedor' },
  { value: 'resolved', label: 'Resolvido' }, { value: 'closed', label: 'Fechado' },
];
export const PRIORITY_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Todas' }, { value: 'critical', label: 'Crítica' }, { value: 'high', label: 'Alta' }, { value: 'medium', label: 'Média' }, { value: 'low', label: 'Baixa' },
];

export function isFiltered(filter: TicketFilter): boolean {
  return !!(filter.status || filter.priority);
}

/** Parâmetros de consulta (sem '?'), só com valores conhecidos, nunca texto livre. */
export function ticketFilterParams(filter: TicketFilter): string {
  const params: string[] = [];
  if (filter.status === 'open') params.push('actionable=true');
  else if (STATUS_OPTIONS.some((option) => option.value && option.value === filter.status)) params.push(`status=${filter.status}`);
  if (PRIORITY_OPTIONS.some((option) => option.value && option.value === filter.priority)) params.push(`priority=${filter.priority}`);
  return params.join('&');
}

export function withFilters(path: string, filter: TicketFilter): string {
  const extra = ticketFilterParams(filter);
  if (!extra) return path;
  return `${path}${path.includes('?') ? '&' : '?'}${extra}`;
}

export function filterSummary(filter: TicketFilter): string {
  const parts = [STATUS_OPTIONS.find((o) => o.value === filter.status && o.value)?.label, PRIORITY_OPTIONS.find((o) => o.value === filter.priority && o.value)?.label.replace(/^/, 'Criticidade ')];
  return parts.filter(Boolean).join(' · ');
}
