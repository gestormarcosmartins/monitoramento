// Estado de leitura das notificações, sem React Native (testável). A leitura só muda aqui DEPOIS que o servidor confirmou.
export type ReadItem = { id: string; tenant_id: string; opened_at: string | null };

export const itemKey = (item: { id: string; tenant_id: string }) => `${item.tenant_id}:${item.id}`;

export function unreadCount(items: ReadItem[]): number {
  return items.filter((item) => !item.opened_at).length;
}

/** Marca como lidas as notificações indicadas (por chave empresa:id). Idempotente: o que já estava lido não muda. */
export function applyRead<T extends ReadItem>(items: T[], keys: string[], at: string = new Date().toISOString()): T[] {
  const wanted = new Set(keys);
  return items.map((item) => (wanted.has(itemKey(item)) && !item.opened_at ? { ...item, opened_at: at } : item));
}

/** Leitura em lote: só nas empresas em que o servidor confirmou. As demais continuam pendentes. */
export function markTenantsRead<T extends ReadItem>(items: T[], tenantIds: string[], at: string = new Date().toISOString()): T[] {
  const confirmed = new Set(tenantIds);
  return items.map((item) => (confirmed.has(item.tenant_id) && !item.opened_at ? { ...item, opened_at: at } : item));
}

export function visibleItems<T extends ReadItem>(items: T[], segment: 'all' | 'unread'): T[] {
  return segment === 'unread' ? items.filter((item) => !item.opened_at) : items;
}
