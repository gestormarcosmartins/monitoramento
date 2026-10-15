import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';

type Requester = (path: string, options?: RequestInit, tenantHeader?: string, retry?: boolean) => Promise<any>;
type Company = { id: string; name: string };

export type NotificationItem = {
  id: string; domain: string; severity: 'P0' | 'P1' | 'P2' | 'P3'; category: string; title: string; body_safe: string;
  action_type: string; action_ref: string | null; require_ack: boolean; created_at: string;
  opened_at: string | null; acknowledged_at: string | null; tenant_id: string; tenant_name: string;
};

const SEVERITY: Record<string, { label: string; color: string }> = {
  P0: { label: 'Crítica', color: C.danger }, P1: { label: 'Alta', color: C.warning }, P2: { label: 'Média', color: C.blue }, P3: { label: 'Baixa', color: C.muted },
};
const POLL_MS = 60000;
const fmt = (value: string) => new Date(value).toLocaleString('pt-BR');

/** Contador de não lidas do sino, somado nas empresas do contexto. Empresa sem acesso conta zero. */
export function useNotificationCount({ enabled, scope, request }: { enabled: boolean; scope: Company[]; request: Requester }) {
  const [count, setCount] = useState(0);
  const requestRef = useRef(request); requestRef.current = request;
  const sequence = useRef(0);
  const key = scope.map((item) => item.id).join(',');
  const refresh = useCallback(async () => {
    if (!enabled || !key) { setCount(0); return; }
    const mine = ++sequence.current;
    const results = await Promise.allSettled(key.split(',').map((id) => requestRef.current('/api/v1/notificacoes?only_unread=true&limit=1', {}, id)));
    if (mine !== sequence.current) return; // resposta de um contexto anterior
    setCount(results.reduce((sum, result) => sum + (result.status === 'fulfilled' ? Number(result.value?.totals?.unread || 0) : 0), 0));
  }, [enabled, key]);
  useEffect(() => { setCount(0); refresh(); const timer = setInterval(refresh, POLL_MS); const sub = AppState.addEventListener('change', (state) => { if (state === 'active') refresh(); }); return () => { sequence.current++; clearInterval(timer); sub.remove(); }; }, [refresh]);
  return { count, refresh };
}

/** Lista da Central, mesclada por empresa e ordenada pela mais recente. */
export function useNotifications({ enabled, scope, request }: { enabled: boolean; scope: Company[]; request: Requester }) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(false); const [error, setError] = useState(''); const [failed, setFailed] = useState<string[]>([]);
  const requestRef = useRef(request); requestRef.current = request;
  const sequence = useRef(0);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const key = scope.map((item) => item.id).join(',');
  const reload = useCallback(async () => {
    const companies = scopeRef.current;
    if (!enabled || !companies.length) return;
    const mine = ++sequence.current;
    setLoading(true); setError('');
    const results = await Promise.allSettled(companies.map((company) => requestRef.current('/api/v1/notificacoes?limit=50', {}, company.id)
      .then((data) => (data.items || []).map((item: any) => ({ ...item, tenant_id: company.id, tenant_name: company.name })) as NotificationItem[])));
    if (mine !== sequence.current) return;
    const merged: NotificationItem[] = []; const failedNames: string[] = [];
    results.forEach((result, index) => { if (result.status === 'fulfilled') merged.push(...result.value); else failedNames.push(companies[index].name); });
    merged.sort((a, b) => b.created_at.localeCompare(a.created_at));
    setItems(merged.slice(0, 100)); setFailed(failedNames);
    if (failedNames.length === companies.length) setError('Não foi possível carregar as notificações. Verifique a conexão e tente novamente.');
    setLoading(false);
  }, [enabled, key]);
  useEffect(() => { setItems([]); setFailed([]); setError(''); if (enabled) reload(); return () => { sequence.current++; }; }, [reload]);
  return { items, loading, error, failed, reload };
}

export function NotificationsScreen({ state, consolidated, onOpen }: { state: ReturnType<typeof useNotifications>; consolidated: boolean; onOpen: (item: NotificationItem) => void }) {
  const [segment, setSegment] = useState<'all' | 'unread'>('all');
  const shown = useMemo(() => (segment === 'unread' ? state.items.filter((item) => !item.opened_at) : state.items), [state.items, segment]);
  return (
    <FlatList
      data={shown} keyExtractor={(item) => `${item.tenant_id}:${item.id}`} contentContainerStyle={s.page}
      refreshControl={<RefreshControl refreshing={state.loading} onRefresh={state.reload} />}
      ListHeaderComponent={<View>
        <View style={s.segments} accessibilityRole="tablist">
          {([['all', 'Todas'], ['unread', 'Não lidas']] as const).map(([key, label]) => (
            <Pressable key={key} accessibilityRole="tab" accessibilityState={{ selected: segment === key }} onPress={() => setSegment(key)} style={[s.segment, segment === key && s.segmentOn]}>
              <Text style={[s.segmentText, segment === key && s.segmentTextOn]}>{label}</Text>
            </Pressable>
          ))}
        </View>
        {state.failed.length > 0 && !state.error ? <Text style={s.warn}>Algumas empresas não responderam: {state.failed.join(', ')}.</Text> : null}
      </View>}
      ListEmptyComponent={state.loading ? <Text style={s.empty}>Carregando…</Text>
        : state.error ? <View><Text style={s.error}>{state.error}</Text><Pressable accessibilityRole="button" onPress={state.reload} style={s.retry}><Text style={s.retryText}>Tentar novamente</Text></Pressable></View>
        : <Text style={s.empty}>{segment === 'unread' ? 'Nenhuma notificação não lida.' : 'Nenhuma notificação.'}</Text>}
      renderItem={({ item }) => {
        const sev = SEVERITY[item.severity] || SEVERITY.P2; const unread = !item.opened_at;
        const pending = item.require_ack && !item.acknowledged_at;
        return (
          <Pressable accessibilityRole="button" accessibilityLabel={`${item.title}. ${sev.label}. ${unread ? 'Não lida' : 'Lida'}.`} onPress={() => onOpen(item)} style={[s.card, unread && s.cardUnread]}>
            <View style={s.row}>
              <Text style={[s.sev, { color: sev.color }]}>{item.severity} · {sev.label}</Text>
              {unread ? <Text style={s.unread}>● Não lida</Text> : null}
            </View>
            <Text style={s.title}>{item.title}</Text>
            <Text style={s.body}>{item.body_safe}</Text>
            <Text style={s.meta}>{consolidated ? `${item.tenant_name} · ` : ''}{fmt(item.created_at)}{pending ? ' · Aguardando confirmação' : ''}</Text>
          </Pressable>
        );
      }}
    />
  );
}

const s = StyleSheet.create({
  page: { padding: 16, gap: 10, paddingBottom: 32 },
  segments: { flexDirection: 'row', backgroundColor: C.card, borderRadius: 10, borderWidth: 1, borderColor: C.border, padding: 4, marginBottom: 12 },
  segment: { flex: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 8 },
  segmentOn: { backgroundColor: C.blue },
  segmentText: { color: C.muted, fontWeight: '700' },
  segmentTextOn: { color: '#FFFFFF' },
  card: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, padding: 14, gap: 4, minHeight: 64 },
  cardUnread: { borderColor: C.blue, borderLeftWidth: 4 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sev: { fontWeight: '800', fontSize: 12 },
  unread: { color: C.blue, fontWeight: '700', fontSize: 12 },
  title: { color: C.text, fontWeight: '800', fontSize: 16 },
  body: { color: C.text, fontSize: 14 },
  meta: { color: C.muted, fontSize: 12 },
  empty: { color: C.muted, textAlign: 'center', paddingVertical: 32 },
  error: { color: C.danger, textAlign: 'center', paddingVertical: 16 },
  warn: { color: C.warning, marginBottom: 8, fontSize: 13 },
  retry: { minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 8, backgroundColor: C.blue, marginHorizontal: 32 },
  retryText: { color: '#FFFFFF', fontWeight: '800' },
});
