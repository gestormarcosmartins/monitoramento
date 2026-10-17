import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';
import { AlertCard, AlertItem } from './alerts';
import { SEVERITY_CARDS, SeverityKey, cardOf, homeCountParams, homeListParams, toggleCard } from './home-alerts';

// Tela de Início: apenas alertas ABERTOS (os que exigem atenção). Padrão: todos; os 4 cards filtram ao clicar.
type Requester = (path: string, options?: RequestInit, tenantHeader?: string) => Promise<any>;
type Status = 'idle' | 'loading' | 'ready' | 'unsupported' | 'error';
type Counts = Record<SeverityKey, number | null>;
const EMPTY_COUNTS: Counts = { critical: null, high: null, medium: null, low: null };
const hhmm = (date: Date | null) => (date ? date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—');

export function useHomeAlerts({ enabled, scope, resetKey, request }: { enabled: boolean; scope: string; resetKey: string; request: Requester }) {
  const [selected, setSelected] = useState<SeverityKey[]>([]);
  const [items, setItems] = useState<AlertItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [counts, setCounts] = useState<Counts>(EMPTY_COUNTS);
  const [failed, setFailed] = useState<string[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(request); requestRef.current = request;
  const listSeq = useRef(0); const countSeq = useRef(0);

  const loadCounts = useCallback(async () => {
    const mine = ++countSeq.current;
    const results = await Promise.allSettled(SEVERITY_CARDS.map((card) => requestRef.current(`/api/v1/alerts/consolidated?${homeCountParams(card.key, scope)}`, {}, '')));
    if (mine !== countSeq.current) return;
    const next = { ...EMPTY_COUNTS };
    results.forEach((result, index) => { if (result.status === 'fulfilled' && typeof result.value?.total === 'number') next[SEVERITY_CARDS[index].key] = result.value.total; });
    setCounts(next);
  }, [scope]);

  const loadList = useCallback(async () => {
    const mine = ++listSeq.current;
    setLoading(true); setError(null); setStatus((current) => (current === 'ready' ? current : 'loading'));
    try {
      const data = await requestRef.current(`/api/v1/alerts/consolidated?${homeListParams({ selected, scope })}`, {}, '');
      if (mine !== listSeq.current) return;
      setItems(data.items || []); setNextCursor(data.next_cursor || null);
      setFailed((data.tenants_failed || []).map((item: any) => item.tenant_name)); setUpdatedAt(new Date()); setStatus('ready');
    } catch (failure: any) {
      if (mine !== listSeq.current) return;
      if (failure?.status === 404 || failure?.status === 405) setStatus('unsupported');
      else { setStatus('error'); setError(failure?.message || 'Falha ao carregar os alertas.'); }
    } finally { if (mine === listSeq.current) setLoading(false); }
  }, [selected, scope]);

  // Trocar de usuário ou empresa descarta tudo e volta ao padrão (todos). Trocar a seleção só refaz a lista.
  useEffect(() => { listSeq.current++; countSeq.current++; setSelected([]); setItems([]); setNextCursor(null); setCounts(EMPTY_COUNTS); setFailed([]); setStatus('idle'); setError(null); }, [resetKey, scope]);
  useEffect(() => { if (enabled) loadList(); }, [enabled, loadList]);
  useEffect(() => { if (enabled) loadCounts(); }, [enabled, loadCounts]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loading) return;
    const mine = listSeq.current;
    setLoading(true);
    try {
      const data = await requestRef.current(`/api/v1/alerts/consolidated?${homeListParams({ selected, scope, cursor: nextCursor })}`, {}, '');
      if (mine !== listSeq.current) return;
      setItems((current) => { const seen = new Set(current.map((item) => item.id)); return [...current, ...(data.items || []).filter((item: AlertItem) => !seen.has(item.id))]; });
      setNextCursor(data.next_cursor || null);
    } catch (failure: any) {
      if (mine !== listSeq.current) return;
      if (failure?.status === 422) loadList(); else setError(failure?.message || 'Falha ao carregar mais alertas.');
    } finally { if (mine === listSeq.current) setLoading(false); }
  }, [nextCursor, loading, selected, scope, loadList]);

  const reload = useCallback(() => { loadList(); loadCounts(); }, [loadList, loadCounts]);
  return { selected, toggle: (key: SeverityKey) => setSelected((current) => toggleCard(current, key)), clear: () => setSelected([]),
    items, nextCursor, counts, failed, updatedAt, status, error, loading, reload, loadMore };
}

export function HomeScreen({ state, title, consolidated, openTicket }: { state: ReturnType<typeof useHomeAlerts>; title: string; consolidated: boolean; openTicket: (id: string, tenantId?: string) => void }) {
  const { selected, toggle, clear, items, nextCursor, counts, failed, updatedAt, status, error, loading, reload, loadMore } = state;
  const filtered = selected.length > 0;
  const header = (
    <View style={s.header}>
      <Text style={s.eyebrow}>VISÃO OPERACIONAL</Text>
      <Text style={s.h1}>{title}</Text>
      <Text style={s.muted}>Alertas em aberto que exigem atenção · Atualizado às {hhmm(updatedAt)}</Text>
      <View style={s.grid}>
        {SEVERITY_CARDS.map((card) => {
          const on = selected.includes(card.key);
          const count = counts[card.key];
          return (
            <Pressable key={card.key} accessibilityRole="checkbox" accessibilityState={{ checked: on }} accessibilityLabel={`${card.label}: ${count ?? 'carregando'} alerta(s) em aberto${on ? ', selecionado' : ''}`}
              onPress={() => toggle(card.key)} style={[s.card, on && { borderColor: card.color, backgroundColor: C.blueSoft }]}>
              <View style={[s.dot, { backgroundColor: card.color }]} />
              <Text style={[s.count, { color: card.color }]}>{count ?? '—'}</Text>
              <Text style={s.cardLabel}>{card.label}</Text>
              {on ? <Text style={s.on}>Selecionado</Text> : null}
            </Pressable>
          );
        })}
      </View>
      {filtered ? <Pressable accessibilityRole="button" onPress={clear} style={s.clear}><Text style={s.clearText}>Mostrar todos</Text></Pressable> : null}
      {failed.length ? (
        <View style={s.warn}><Text style={s.warnText}>Dados incompletos: não foi possível carregar {failed.join(', ')}.</Text><Pressable accessibilityRole="button" onPress={reload} style={s.clear}><Text style={s.clearText}>Tentar novamente</Text></Pressable></View>
      ) : null}
      {status === 'ready' && error ? <Text style={s.errorInline}>{error}</Text> : null}
    </View>
  );

  let empty: React.ReactNode;
  if (status === 'unsupported') empty = <View style={s.notice}><Text style={s.noticeTitle}>Lista de alertas indisponível</Text><Text style={s.muted}>Este servidor ainda não tem a listagem consolidada de alertas. Peça a atualização da Central.</Text></View>;
  else if (status === 'error') empty = <View style={s.errorBox}><Text style={s.errorText}>{error}</Text><Pressable accessibilityRole="button" onPress={reload} style={s.clear}><Text style={s.clearText}>Tentar novamente</Text></Pressable></View>;
  else if (status === 'ready') empty = <Text style={s.empty}>{filtered ? 'Nenhum alerta em aberto com os cards selecionados.' : 'Nenhum alerta em aberto. Tudo certo por aqui.'}</Text>;
  else empty = <Text style={s.empty}>Carregando…</Text>;

  return (
    <FlatList
      data={status === 'ready' ? items : []}
      keyExtractor={(item) => item.id}
      contentContainerStyle={s.page}
      refreshControl={<RefreshControl refreshing={loading && status !== 'idle'} onRefresh={reload} />}
      ListHeaderComponent={header}
      ListEmptyComponent={empty}
      ListFooterComponent={status === 'ready' && nextCursor ? <Pressable accessibilityRole="button" disabled={loading} onPress={loadMore} style={[s.more, loading && { opacity: 0.55 }]}><Text style={s.moreText}>Carregar mais</Text></Pressable> : null}
      renderItem={({ item }) => { const card = cardOf(item.severity); return <AlertCard item={item} showCompany={consolidated} openTicket={openTicket} level={{ label: card.label.replace('Críticos', 'Crítico'), color: card.color }} />; }}
    />
  );
}

const s = StyleSheet.create({
  page: { padding: 18, paddingBottom: 32, gap: 10 }, header: { gap: 10, marginBottom: 4 },
  eyebrow: { color: C.blueText, fontSize: 12, fontWeight: '800', letterSpacing: 1 }, h1: { color: C.text, fontSize: 26, lineHeight: 32, fontWeight: '800' }, muted: { color: C.muted, fontSize: 13, lineHeight: 19 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  card: { flexBasis: '47%', flexGrow: 1, minHeight: 88, borderRadius: 14, borderWidth: 2, borderColor: C.border, backgroundColor: C.card, padding: 12, justifyContent: 'center', gap: 2 },
  dot: { width: 10, height: 10, borderRadius: 5 }, count: { fontSize: 30, fontWeight: '800', lineHeight: 36 }, cardLabel: { color: C.text, fontSize: 14, fontWeight: '700' }, on: { color: C.blueText, fontSize: 11, fontWeight: '800' },
  clear: { minHeight: 44, justifyContent: 'center' }, clearText: { color: C.blueText, fontWeight: '800' },
  warn: { backgroundColor: '#3A2A12', borderColor: '#7C4A12', borderWidth: 1, borderRadius: 12, padding: 12, gap: 4 }, warnText: { color: '#FDBA74', fontSize: 13, lineHeight: 18 }, errorInline: { color: '#FCA5A5', fontSize: 13 },
  empty: { color: C.muted, textAlign: 'center', paddingVertical: 32 },
  notice: { backgroundColor: '#12305C', borderColor: '#27508F', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, noticeTitle: { color: '#BFDBFE', fontWeight: '800' },
  errorBox: { backgroundColor: '#3B1620', borderColor: '#7F2A35', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, errorText: { color: '#FCA5A5', fontSize: 13 },
  more: { minHeight: 48, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, alignItems: 'center', justifyContent: 'center', marginTop: 4 }, moreText: { color: C.text, fontWeight: '800' },
});
