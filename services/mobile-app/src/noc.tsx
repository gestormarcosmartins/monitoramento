import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, AppState, FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from 'react-native';
import { C } from './theme';
import { NOC_FILTERS, NocFilter, POLL_MS, STATUS, ago, filterNoc, nextDelay, nocPath, nocServiceLabel, nocStatusLabel } from './monitoring-logic';

// NOC (somente leitura): um card por ambiente monitorado, mesmo conceito do portal. Fonte: GET /api/v1/dashboard/noc.
type Requester = (path: string, options?: RequestInit, tenantHeader?: string) => Promise<any>;
type NocService = { id: string; name: string; state: string; last_checked_at?: string | null };
export type NocEnvironment = {
  asset_id: string; environment: string; company_id: string; company: string; status: string; criticality?: string;
  services_total: number; services_failed: number; services_no_communication: number; open_alerts: number;
  availability: number | null; last_checked_at: string | null; services: NocService[];
};
export type NocData = { summary: { companies: number; environments: number; services: number; critical: number; attention: number; operational: number; no_communication: number }; environments: NocEnvironment[]; generated_at?: string };

export function useNoc({ enabled, scope, request }: { enabled: boolean; scope: string; request: Requester }) {
  const [data, setData] = useState<NocData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(request); requestRef.current = request;
  const delayRef = useRef(POLL_MS);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await requestRef.current(nocPath(scope), {}, '');
      setData(next); setUpdatedAt(new Date()); setError(null); delayRef.current = nextDelay(delayRef.current, false);
    } catch (failure: any) {
      setError(failure?.message || 'Falha ao atualizar.'); delayRef.current = nextDelay(delayRef.current, true);
    } finally { setLoading(false); }
  }, [scope]);

  useEffect(() => {
    if (!enabled) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout> | undefined;
    setData(null); setError(null); setUpdatedAt(null); delayRef.current = POLL_MS;
    const schedule = () => { timer = setTimeout(async () => { if (AppState.currentState === 'active') await load(); if (!stopped) schedule(); }, delayRef.current); };
    load(); schedule();
    const subscription = AppState.addEventListener('change', (state) => { if (state === 'active') load(); });
    return () => { stopped = true; if (timer) clearTimeout(timer); subscription.remove(); };
  }, [enabled, scope, load]);

  return { data, error, updatedAt, loading, reload: load };
}

const hhmm = (date: Date | null) => (date ? date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—');

function Summary({ summary }: { summary: NocData['summary'] }) {
  const items = [
    { label: 'Críticos', value: summary.critical, color: STATUS.critical },
    { label: 'Atenção', value: summary.attention, color: STATUS.attention },
    { label: 'Operacionais', value: summary.operational, color: STATUS.healthy },
    { label: 'Sem comunicação', value: summary.no_communication, color: '#9DB2CE' },
  ];
  return (
    <View style={s.summary}>
      {items.map((item) => (
        <View key={item.label} style={s.kpi} accessible accessibilityLabel={`${item.label}: ${item.value}`}>
          <View style={[s.kpiBar, { backgroundColor: item.color }]} />
          <Text style={s.kpiValue}>{item.value}</Text>
          <Text style={s.kpiLabel} numberOfLines={2}>{item.label}</Text>
        </View>
      ))}
    </View>
  );
}

function Card({ item, showCompany }: { item: NocEnvironment; showCompany: boolean }) {
  const status = nocStatusLabel(item.status);
  const parts = [`${item.services_total} serviço(s)`];
  if (item.availability != null) parts.push(`${item.availability}% operacional`);
  if (item.services_failed) parts.push(`${item.services_failed} em falha`);
  return (
    <View style={[s.card, { borderTopColor: status.color }]} accessible accessibilityLabel={`${item.environment}, ${item.company}, ${status.label}. ${parts.join(', ')}`}>
      <View style={s.cardHead}>
        <View style={{ flex: 1 }}>
          <Text style={s.title}>{item.environment}</Text>
          {showCompany ? <Text style={s.muted}>{item.company}</Text> : null}
        </View>
        <View style={[s.badge, { backgroundColor: status.color + '22' }]}><Text style={[s.badgeText, { color: item.status === 'attention' ? '#FDE68A' : status.color }]}>{status.label}</Text></View>
      </View>
      <Text style={s.meta}>{parts.join(' · ')}</Text>
      {item.services.length ? (
        <View style={s.chips}>
          {item.services.map((service) => {
            const state = nocServiceLabel(service.state);
            return (
              <View key={service.id} style={s.chip}>
                <View style={[s.dot, { backgroundColor: state.color }]} />
                <Text style={s.chipText} numberOfLines={1}>{service.name}</Text>
                <Text style={s.chipState}>{state.label}</Text>
              </View>
            );
          })}
        </View>
      ) : null}
      <View style={s.foot}>
        <Text style={s.muted}>{item.last_checked_at ? `Atualizado ${ago(item.last_checked_at)}` : 'Nunca coletado'}</Text>
        {item.open_alerts > 0 ? <Text style={[s.muted, { color: C.danger, fontWeight: '700' }]}>{item.open_alerts} alerta(s) aberto(s)</Text> : null}
      </View>
    </View>
  );
}

export function NocScreen({ noc, consolidated, contextLabel }: { noc: ReturnType<typeof useNoc>; consolidated: boolean; contextLabel: string }) {
  const [filter, setFilter] = useState<NocFilter>('all');
  const [text, setText] = useState('');
  const { data, error, loading, updatedAt, reload } = noc;
  const rows = useMemo(() => filterNoc(data?.environments || [], filter, text), [data, filter, text]);
  const total = data?.environments.length || 0;

  const header = (
    <View style={s.header}>
      <View style={s.titleRow}>
        <View style={{ flex: 1 }}>
          <Text style={s.kicker}>NOC</Text>
          <Text style={s.h1}>{contextLabel}</Text>
        </View>
        <Text style={s.muted}>Atualizado às {hhmm(updatedAt)}</Text>
      </View>
      {error ? <Pressable onPress={reload} style={s.error} accessibilityRole="button"><Text style={s.errorText}>{data ? 'Não foi possível atualizar. Exibindo os últimos dados. Toque para tentar de novo.' : error}</Text></Pressable> : null}
      {data ? <Summary summary={data.summary} /> : null}
      <TextInput style={s.search} value={text} onChangeText={setText} placeholder="Buscar empresa ou ambiente" placeholderTextColor={C.muted} autoCapitalize="none" autoCorrect={false} clearButtonMode="while-editing" accessibilityLabel="Buscar empresa ou ambiente" />
      <View style={s.filters}>
        {NOC_FILTERS.map((option) => {
          const on = option.key === filter;
          return (
            <Pressable key={option.key} accessibilityRole="button" accessibilityState={{ selected: on }} onPress={() => setFilter(option.key)} style={[s.filter, on && s.filterOn]}>
              <Text style={[s.filterText, on && s.filterTextOn]}>{option.label}</Text>
            </Pressable>
          );
        })}
      </View>
      {data ? <Text style={s.muted} accessibilityLiveRegion="polite">{rows.length} de {total} ambiente(s)</Text> : null}
    </View>
  );

  const empty = !data
    ? (loading || !error ? <View style={s.center}><ActivityIndicator color={C.blue} /><Text style={s.muted}>Carregando o NOC…</Text></View> : null)
    : !total ? <Text style={s.emptyText}>Nenhum ambiente monitorado nas empresas autorizadas.</Text>
    : <Text style={s.emptyText}>Nenhum ambiente com esses filtros.</Text>;

  return (
    <FlatList
      data={rows}
      keyExtractor={(item) => `${item.company_id}:${item.asset_id}`}
      renderItem={({ item }) => <Card item={item} showCompany={consolidated} />}
      ListHeaderComponent={header}
      ListEmptyComponent={empty}
      contentContainerStyle={s.page}
      keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} />}
    />
  );
}

const s = StyleSheet.create({
  page: { padding: 18, paddingBottom: 32, gap: 12 }, header: { gap: 12, marginBottom: 4 },
  titleRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
  kicker: { color: C.blueText, fontSize: 13, fontWeight: '800', letterSpacing: 1.5 }, h1: { color: C.text, fontSize: 26, fontWeight: '800' },
  muted: { color: C.muted, fontSize: 12 },
  summary: { flexDirection: 'row', gap: 8 },
  kpi: { flex: 1, minHeight: 84, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, padding: 10, paddingTop: 12, overflow: 'hidden' },
  kpiBar: { position: 'absolute', top: 0, left: 0, right: 0, height: 4 }, kpiValue: { color: C.text, fontSize: 24, fontWeight: '800' }, kpiLabel: { color: C.muted, fontSize: 11 },
  search: { minHeight: 44, borderWidth: 1, borderColor: C.border, borderRadius: 12, backgroundColor: C.card, paddingHorizontal: 14, color: C.text, fontSize: 15 },
  filters: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  filter: { minHeight: 44, paddingHorizontal: 14, borderRadius: 22, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, justifyContent: 'center' }, filterOn: { backgroundColor: C.blue, borderColor: C.blue },
  filterText: { color: C.text, fontSize: 13, fontWeight: '600' }, filterTextOn: { color: 'white' },
  card: { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, borderTopWidth: 4, padding: 14, gap: 10 },
  cardHead: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' }, title: { color: C.text, fontSize: 17, fontWeight: '800' },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 }, badgeText: { fontSize: 12, fontWeight: '800' },
  meta: { color: C.text, fontSize: 13 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, paddingVertical: 5, borderRadius: 8, borderWidth: 1, borderColor: C.border, backgroundColor: '#0F2747', maxWidth: '100%' },
  dot: { width: 8, height: 8, borderRadius: 4 }, chipText: { color: C.text, fontSize: 12, flexShrink: 1 }, chipState: { color: C.muted, fontSize: 12 },
  foot: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 },
  center: { alignItems: 'center', gap: 10, paddingVertical: 40 }, emptyText: { color: C.muted, textAlign: 'center', paddingVertical: 32 },
  error: { backgroundColor: '#4A1D26', borderRadius: 12, padding: 12 }, errorText: { color: '#FCA5A5', fontSize: 13 },
});
