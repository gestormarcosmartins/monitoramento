// Logica pura do Monitoramento (sem React Native), para poder ser testada com `node --test`.

export const STATUS = { critical: '#EF4444', attention: '#F59E0B', healthy: '#22C55E', unknown: '#94A3B8', inactive: '#CBD5E1', info: '#3B82F6' };
export const POLL_MS = 60_000;
export const MAX_BACKOFF_MS = 300_000;

// Atualizacao periodica: 60 s; em falha dobra o intervalo ate 5 min; no primeiro sucesso volta a 60 s.
export function nextDelay(current: number, failed: boolean): number {
  return failed ? Math.min(current * 2, MAX_BACKOFF_MS) : POLL_MS;
}

export type BadgeLevel = 'none' | 'attention' | 'critical';

// Selo da aba: apenas alertas ABERTOS visiveis ao usuario (criticos + de atencao). Sem dados = sem selo,
// nunca "zero saudavel": a ausencia de dados nao e tratada como ausencia de problemas na tela, so some o selo.
export function badgeFrom(data: { totals: { critical_alerts: number; attention_alerts: number } } | null | undefined): { count: number; critical: number; level: BadgeLevel } {
  if (!data) return { count: 0, critical: 0, level: 'none' };
  const critical = Number(data.totals.critical_alerts || 0);
  const attention = Number(data.totals.attention_alerts || 0);
  return { count: critical + attention, critical, level: critical > 0 ? 'critical' : attention > 0 ? 'attention' : 'none' };
}

export function ago(iso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (Number.isNaN(minutes)) return '—';
  if (minutes < 1) return 'agora';
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  return `há ${Math.round(hours / 24)} d`;
}

// "Sem dados" (desconhecido) NUNCA vira saudavel; so last_status 'up' em servico ativo e saudavel.
export function serviceState(service: { active: boolean; last_status: string }): { label: string; color: string } {
  if (!service.active) return { label: 'Inativo', color: STATUS.inactive };
  if (service.last_status === 'up') return { label: 'Saudável', color: STATUS.healthy };
  if (service.last_status === 'down') return { label: 'Indisponível', color: STATUS.critical };
  return { label: 'Sem dados', color: STATUS.unknown };
}

// ---- Lista de alertas (GET /api/v1/alerts/consolidated)

export type AlertFilter = 'problems' | 'critical' | 'attention' | 'all';
// "Meus" nao existe: o alerta nao tem responsavel no backend. Ordem e rotulos seguem a especificacao.
export const ALERT_FILTERS: Array<{ key: AlertFilter; label: string }> = [
  { key: 'problems', label: 'Com problemas' },
  { key: 'critical', label: 'Críticos' },
  { key: 'attention', label: 'Atenção' },
  { key: 'all', label: 'Todos' },
];

// Com problemas = alertas ABERTOS (resolvidos e ignorados ficam de fora). Críticos = high+disaster; Atenção = warning+average
// (mesmo agrupamento do painel). Todos = qualquer estado.
export function alertParams({ filter, text, scope, cursor, pageSize = 25 }: { filter: AlertFilter; text?: string; scope?: string; cursor?: string | null; pageSize?: number }): string {
  const parts = [`page_size=${pageSize}`, `status=${filter === 'all' ? 'all' : 'open'}`];
  if (filter === 'critical') parts.push(`severity=${encodeURIComponent('high,disaster')}`);
  if (filter === 'attention') parts.push(`severity=${encodeURIComponent('warning,average')}`);
  const query = (text || '').trim();
  if (query) parts.push(`q=${encodeURIComponent(query)}`);
  if (scope && scope !== 'all') parts.push(`tenant_ids=${encodeURIComponent(scope)}`);
  if (cursor) parts.push(`cursor=${encodeURIComponent(cursor)}`);
  return parts.join('&');
}

// Estado e severidade sempre tem rotulo em texto: a cor nunca e o unico sinal.
export function severityLabel(group: string): { label: string; color: string } {
  if (group === 'critical') return { label: 'Crítico', color: STATUS.critical };
  if (group === 'attention') return { label: 'Atenção', color: STATUS.attention };
  return { label: 'Informativo', color: STATUS.info };
}

export function alertStatusLabel(status: string): string {
  return ({ open: 'Aberto', resolved: 'Resolvido', ignored: 'Ignorado' } as Record<string, string>)[status] || status;
}

// ---- Ambientes e Servicos (GET /api/v1/assets/consolidated e /api/v1/monitors/consolidated)

export type InventoryKind = 'environments' | 'services';
export type InventoryFilter = 'problems' | 'unavailable' | 'critical' | 'attention' | 'unknown' | 'healthy' | 'inactive' | 'all';
type FilterOption = { key: InventoryFilter; label: string; values: string | null };

// "Com problemas" nunca inclui desconhecido: "Sem dados" e um filtro proprio (estados desconhecidos aparecem separados).
export const ENVIRONMENT_FILTERS: FilterOption[] = [
  { key: 'problems', label: 'Com problemas', values: 'unavailable,critical,attention' },
  { key: 'unavailable', label: 'Indisponíveis', values: 'unavailable' },
  { key: 'critical', label: 'Críticos', values: 'critical' },
  { key: 'attention', label: 'Atenção', values: 'attention' },
  { key: 'unknown', label: 'Sem dados', values: 'unknown' },
  { key: 'healthy', label: 'Saudáveis', values: 'healthy' },
  { key: 'all', label: 'Todos', values: null },
];
export const SERVICE_FILTERS: FilterOption[] = [
  { key: 'problems', label: 'Com problemas', values: 'unavailable' },
  { key: 'unknown', label: 'Sem dados', values: 'unknown' },
  { key: 'healthy', label: 'Saudáveis', values: 'healthy' },
  { key: 'inactive', label: 'Inativos', values: 'inactive' },
  { key: 'all', label: 'Todos', values: null },
];

export function inventoryParams({ kind, filter, text, scope, page = 1, pageSize = 25, assetId }: { kind: InventoryKind; filter: InventoryFilter; text?: string; scope?: string; page?: number; pageSize?: number; assetId?: string | null }): string {
  const option = (kind === 'environments' ? ENVIRONMENT_FILTERS : SERVICE_FILTERS).find((item) => item.key === filter);
  const parts = [`page=${page}`, `page_size=${pageSize}`];
  if (option?.values) parts.push(`${kind === 'environments' ? 'status' : 'state'}=${encodeURIComponent(option.values)}`);
  if (kind === 'services' && assetId) parts.push(`asset_id=${encodeURIComponent(assetId)}`);
  const query = (text || '').trim();
  if (query) parts.push(`q=${encodeURIComponent(query)}`);
  if (scope && scope !== 'all') parts.push(`tenant_ids=${encodeURIComponent(scope)}`);
  return parts.join('&');
}

// Estado de ambiente e de servico: sempre rotulo em texto + cor. "Indisponivel" (servico fora) e "Critico" (alerta critico aberto) sao distintos.
export function inventoryStatusLabel(status: string | undefined | null): { label: string; color: string } {
  switch (status) {
    case 'healthy': return { label: 'Saudável', color: STATUS.healthy };
    case 'attention': return { label: 'Atenção', color: STATUS.attention };
    case 'critical': return { label: 'Crítico', color: STATUS.critical };
    case 'unavailable': return { label: 'Indisponível', color: '#B91C1C' };
    case 'inactive': return { label: 'Inativo', color: STATUS.inactive };
    default: return { label: 'Sem dados', color: STATUS.unknown };
  }
}

const KINDS: Record<string, string> = { linux: 'Linux', windows: 'Windows', database: 'Banco de dados', network: 'Rede', website: 'Site', api: 'API', service: 'Serviço', other: 'Outro' };
const MONITOR_TYPES: Record<string, string> = { http: 'HTTP', tcp: 'TCP', zabbix: 'Zabbix', event_deadman: 'Sinal de vida', push: 'Push' };
export const kindLabel = (kind: string): string => KINDS[kind] || kind;
export const monitorTypeLabel = (type: string): string => MONITOR_TYPES[type] || type;

export function checkedText(iso: string | null | undefined, now: number = Date.now()): string {
  return iso ? `Última coleta ${ago(iso, now)}` : 'Nunca coletado';
}

export function serviceBreakdown(services: { total: number; up: number; down: number; unknown: number; inactive: number }): string {
  if (!services.total) return 'Sem serviços';
  const parts = [`${services.total} serviço(s)`];
  if (services.up) parts.push(`${services.up} saudável(is)`);
  if (services.down) parts.push(`${services.down} indisponível(is)`);
  if (services.unknown) parts.push(`${services.unknown} sem dados`);
  if (services.inactive) parts.push(`${services.inactive} inativo(s)`);
  return parts.join(' · ');
}

export function alertBreakdown(alerts: { critical: number; attention: number; info: number }): string {
  const parts: string[] = [];
  if (alerts.critical) parts.push(`${alerts.critical} crítico(s)`);
  if (alerts.attention) parts.push(`${alerts.attention} de atenção`);
  if (alerts.info) parts.push(`${alerts.info} informativo(s)`);
  return parts.length ? parts.join(' · ') : 'Sem alertas abertos';
}

// ---- NOC (GET /api/v1/dashboard/noc): um card por ambiente, mesmo conceito do portal ----
export type NocStatus = 'critical' | 'attention' | 'operational' | 'no_communication';
export type NocFilter = 'all' | 'problems' | 'critical' | 'no_communication';
export const NOC_FILTERS: Array<{ key: NocFilter; label: string }> = [
  { key: 'all', label: 'Todos' },
  { key: 'problems', label: 'Com problemas' },
  { key: 'critical', label: 'Críticos' },
  { key: 'no_communication', label: 'Sem comunicação' },
];

export function nocPath(scope?: string): string {
  return scope && scope !== 'all' ? `/api/v1/dashboard/noc?tenant_ids=${encodeURIComponent(scope)}` : '/api/v1/dashboard/noc';
}

export function nocStatusLabel(status: string | undefined | null): { label: string; color: string } {
  switch (status) {
    case 'critical': return { label: 'Crítico', color: STATUS.critical };
    case 'attention': return { label: 'Atenção', color: STATUS.attention };
    case 'operational': return { label: 'Operacional', color: STATUS.healthy };
    default: return { label: 'Sem comunicação', color: '#64748B' };
  }
}

export function nocServiceLabel(state: string | undefined | null): { label: string; color: string } {
  switch (state) {
    case 'healthy': return { label: 'OK', color: STATUS.healthy };
    case 'failed': return { label: 'Falha', color: STATUS.critical };
    case 'inactive': return { label: 'Inativo', color: STATUS.inactive };
    default: return { label: 'Sem dados', color: '#64748B' };
  }
}

const norm = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

export function filterNoc<T extends { status: string; environment: string; company: string }>(items: T[], filter: NocFilter, text = ''): T[] {
  const query = norm(text.trim());
  return items.filter((item) => {
    if (filter === 'problems' && item.status === 'operational') return false;
    if (filter === 'critical' && item.status !== 'critical') return false;
    if (filter === 'no_communication' && item.status !== 'no_communication') return false;
    return !query || norm(item.environment).includes(query) || norm(item.company).includes(query);
  });
}
