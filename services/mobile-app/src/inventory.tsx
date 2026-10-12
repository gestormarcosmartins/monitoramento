import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from 'react-native';
import { C } from './theme';
import {
  ENVIRONMENT_FILTERS, InventoryFilter, InventoryKind, SERVICE_FILTERS, alertBreakdown, checkedText, inventoryParams, inventoryStatusLabel, kindLabel, monitorTypeLabel, serviceBreakdown,
} from './monitoring-logic';

// Abas Ambientes e Servicos (CD-09): listas de GET /api/v1/assets/consolidated e /api/v1/monitors/consolidated, somente leitura.
// O estado de cada item (saudavel, indisponivel, sem dados...) vem calculado do servidor; o app nunca o recalcula.
// Os endpoints nao devolvem configuration, metadata nem address.

export type EnvironmentItem = {
  id: string; tenant_id: string; tenant_name: string; name: string; kind: string; criticality: string; active: boolean; status: string;
  services: { total: number; up: number; down: number; unknown: number; inactive: number }; alerts: { critical: number; attention: number; info: number };
};
export type ServiceItem = {
  id: string; tenant_id: string; tenant_name: string; name: string; monitor_type: string; active: boolean; severity: string; state: string; last_status: string; stale: boolean;
  last_checked_at?: string | null; last_message?: string | null; interval_seconds?: number | null; asset_id: string; asset_name: string; responsible_team_name?: string | null;
  open_alerts: { critical: number; attention: number; info: number };
};
type Requester = (path: string, options?: RequestInit, tenantHeader?: string) => Promise<any>;
type LoadStatus = 'idle' | 'loading' | 'ready' | 'unsupported' | 'error';
type Hit = { id: string };

const PATH: Record<InventoryKind, string> = { environments: '/api/v1/assets/consolidated', services: '/api/v1/monitors/consolidated' };
const hhmm = (date: Date | null) => (date ? date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—');

export function useInventory<T extends Hit>({ kind, enabled, scope, resetKey, request, assetId }: { kind: InventoryKind; enabled: boolean; scope: string; resetKey: string; request: Requester; assetId?: string | null }) {
  const [filter, setFilter] = useState<InventoryFilter>('problems');
  const [text, setText] = useState('');
  const [applied, setApplied] = useState('');
  const [items, setItems] = useState<T[]>([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState<number | null>(null);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const [truncated, setTruncated] = useState<string[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [status, setStatus] = useState<LoadStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(request); requestRef.current = request;
  const sequence = useRef(0);

  const clear = useCallback(() => {
    sequence.current += 1;
    setItems([]); setPage(1); setPages(1); setTotal(null); setCounts(null); setFailed([]); setTruncated([]); setUpdatedAt(null); setStatus('idle'); setError(null); setLoading(false);
  }, []);

  const fetchPage = useCallback((target: number) => requestRef.current(`${PATH[kind]}?${inventoryParams({ kind, filter, text: applied, scope, page: target, assetId })}`, {}, ''), [kind, filter, applied, scope, assetId]);

  const loadFirst = useCallback(async () => {
    const mine = ++sequence.current;
    setLoading(true); setError(null); setStatus((current) => (current === 'ready' ? current : 'loading'));
    try {
      const data = await fetchPage(1);
      if (mine !== sequence.current) return;
      setItems(data.items || []); setPage(data.page || 1); setPages(data.pages || 1); setTotal(data.total ?? null); setCounts(data.counts || null);
      setFailed((data.tenants_failed || []).map((item: any) => item.tenant_name)); setTruncated((data.tenants_truncated || []).map((item: any) => item.tenant_name));
      setUpdatedAt(new Date()); setStatus('ready');
    } catch (failure: any) {
      if (mine !== sequence.current) return;
      // 404/405: servidor sem o endpoint. Nao ha queda para GET /assets ou /monitors, que devolvem configuracao e todas as colunas.
      if (failure?.status === 404 || failure?.status === 405) setStatus('unsupported');
      else { setStatus('error'); setError(failure?.message || 'Falha ao carregar.'); }
    } finally { if (mine === sequence.current) setLoading(false); }
  }, [fetchPage]);

  // Trocar de usuario, empresa, filtro, busca ou ambiente descarta a lista anterior (nunca mostrar dados de outro escopo).
  useEffect(() => { clear(); }, [resetKey, scope, filter, applied, assetId, clear]);
  useEffect(() => { if (enabled) loadFirst(); }, [enabled, loadFirst]);

  const loadMore = useCallback(async () => {
    if (page >= pages || loading) return;
    const mine = sequence.current;
    setLoading(true);
    try {
      const data = await fetchPage(page + 1);
      if (mine !== sequence.current) return;
      setItems((current) => { const seen = new Set(current.map((item) => item.id)); return [...current, ...(data.items || []).filter((item: T) => !seen.has(item.id))]; });
      setPage(data.page || page + 1); setPages(data.pages || pages);
    } catch (failure: any) {
      if (mine !== sequence.current) return;
      setError(failure?.message || 'Falha ao carregar mais.');
    } finally { if (mine === sequence.current) setLoading(false); }
  }, [page, pages, loading, fetchPage]);

  return { kind, filter, setFilter, text, setText, submitSearch: () => setApplied(text.trim()), clearSearch: () => { setText(''); setApplied(''); },
    items, hasMore: page < pages, total, counts, failed, truncated, updatedAt, status, error, loading, reload: loadFirst, loadMore, clear };
}

type InventoryState<T extends Hit> = ReturnType<typeof useInventory<T>>;

function ListShell<T extends Hit>({ state, eyebrow, title, contextLabel, options, noun, renderItem, extraHeader }: {
  state: InventoryState<T>; eyebrow: string; title: string; contextLabel: string; options: Array<{ key: InventoryFilter; label: string }>; noun: string;
  renderItem: (item: T) => React.ReactElement; extraHeader?: React.ReactNode;
}) {
  const { filter, setFilter, text, setText, submitSearch, clearSearch, items, hasMore, total, counts, failed, truncated, updatedAt, status, error, loading, reload, loadMore } = state;
  const summary = status === 'ready' && total !== null
    ? `${total} ${noun}${counts ? ' · ' + Object.entries(counts).filter(([, value]) => value > 0).map(([key, value]) => `${value} ${inventoryStatusLabel(key).label.toLowerCase()}`).join(' · ') : ''}${hasMore ? ` · mostrando ${items.length}` : ''}`
    : null;
  const header = (
    <View style={s.header}>
      <Text style={s.eyebrow}>{eyebrow}</Text>
      <Text style={s.h1}>{title}</Text>
      <Text style={s.muted}>{contextLabel} · Atualizado às {hhmm(updatedAt)}</Text>
      {extraHeader}
      <View style={s.chips} accessibilityRole="radiogroup">
        {options.map((option) => {
          const on = option.key === filter;
          return <Pressable key={option.key} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => setFilter(option.key)} style={[s.chip, on && s.chipOn]}><Text style={[s.chipText, on && s.chipTextOn]}>{option.label}</Text></Pressable>;
        })}
      </View>
      <View style={s.search}>
        <TextInput value={text} onChangeText={setText} onSubmitEditing={submitSearch} returnKeyType="search" placeholder="Buscar por nome" placeholderTextColor="#91A0B5" autoCapitalize="none" accessibilityLabel={`Buscar ${title.toLowerCase()}`} style={s.input} />
        <Pressable accessibilityRole="button" onPress={submitSearch} style={s.searchButton}><Text style={s.searchText}>Buscar</Text></Pressable>
        {text ? <Pressable accessibilityRole="button" accessibilityLabel="Limpar busca" onPress={clearSearch} style={s.clearButton}><Text style={s.clearText}>✕</Text></Pressable> : null}
      </View>
      {summary ? <Text style={s.summary}>{summary}</Text> : null}
      {failed.length ? <View style={s.warn}><Text style={s.warnText}>Dados incompletos: não foi possível carregar {failed.join(', ')}.</Text><Pressable accessibilityRole="button" onPress={reload} style={s.link}><Text style={s.linkText}>Tentar novamente</Text></Pressable></View> : null}
      {truncated.length ? <View style={s.warn}><Text style={s.warnText}>Lista limitada para {truncated.join(', ')}: há mais itens do que o limite de exibição.</Text></View> : null}
      {status === 'ready' && error ? <Text style={s.errorInline}>{error}</Text> : null}
    </View>
  );

  let empty: React.ReactNode;
  if (status === 'unsupported') empty = <View style={s.notice}><Text style={s.noticeTitle}>Lista indisponível</Text><Text style={s.muted}>Este servidor ainda não tem a listagem consolidada de {noun}. Peça a atualização do backend; a Visão geral continua funcionando.</Text><Pressable accessibilityRole="button" onPress={reload} style={s.retry}><Text style={s.retryText}>Verificar novamente</Text></Pressable></View>;
  else if (status === 'error') empty = <View style={s.errorBox}><Text style={s.errorTitle}>Não foi possível carregar</Text><Text style={s.errorText}>{error}</Text><Pressable accessibilityRole="button" onPress={reload} style={s.retry}><Text style={s.retryText}>Tentar novamente</Text></Pressable></View>;
  else if (status === 'ready') empty = <Text style={s.empty}>Nenhum item para este filtro.</Text>;
  else empty = <Text style={s.empty}>Carregando…</Text>;

  return (
    <FlatList
      data={status === 'ready' ? items : []}
      keyExtractor={(item) => item.id}
      contentContainerStyle={s.page}
      refreshControl={<RefreshControl refreshing={loading && status !== 'idle'} onRefresh={reload} />}
      ListHeaderComponent={header}
      ListEmptyComponent={empty}
      ListFooterComponent={status === 'ready' && hasMore ? <Pressable accessibilityRole="button" disabled={loading} onPress={loadMore} style={[s.more, loading && { opacity: 0.55 }]}><Text style={s.moreText}>Carregar mais</Text></Pressable> : null}
      renderItem={({ item }) => renderItem(item)}
      keyboardShouldPersistTaps="handled"
    />
  );
}

function Card({ color, children, onPress, label }: { color: string; children: React.ReactNode; onPress?: () => void; label: string }) {
  const body = <><View style={[s.bar, { backgroundColor: color }]} /><View style={s.cardBody}>{children}</View></>;
  return onPress
    ? <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} style={s.card}>{body}</Pressable>
    : <View accessible accessibilityLabel={label} style={s.card}>{body}</View>;
}

function EnvironmentCard({ item, showCompany, openServices }: { item: EnvironmentItem; showCompany: boolean; openServices: (asset: { id: string; name: string }) => void }) {
  const status = inventoryStatusLabel(item.status);
  return (
    <Card color={status.color} label={`${status.label}: ${item.name}`}>
      <View style={s.cardTop}><Text style={s.severity}>{status.label}</Text><Text style={s.ago}>{kindLabel(item.kind)}</Text></View>
      <Text style={s.title} numberOfLines={2}>{item.name}</Text>
      {showCompany ? <Text style={s.muted}>{item.tenant_name}</Text> : null}
      <Text style={s.muted}>{serviceBreakdown(item.services)}</Text>
      <Text style={s.muted}>Alertas abertos: {alertBreakdown(item.alerts)}</Text>
      {item.services.total ? <Pressable accessibilityRole="button" onPress={() => openServices({ id: item.id, name: item.name })} style={s.link}><Text style={s.linkText}>Ver serviços deste ambiente</Text></Pressable> : null}
    </Card>
  );
}

function ServiceCard({ item, showCompany }: { item: ServiceItem; showCompany: boolean }) {
  const [open, setOpen] = useState(false);
  const state = inventoryStatusLabel(item.state);
  const where = [showCompany ? item.tenant_name : null, item.asset_name].filter(Boolean).join(' · ');
  return (
    <Card color={state.color} label={`${state.label}: ${item.name}`} onPress={() => setOpen(!open)}>
      <View style={s.cardTop}>
        <Text style={s.severity}>{state.label}</Text>
        {item.stale && item.active ? <Text style={s.stale}>Coleta desatualizada</Text> : null}
        <Text style={s.ago}>{monitorTypeLabel(item.monitor_type)}</Text>
      </View>
      <Text style={s.title} numberOfLines={open ? undefined : 2}>{item.name}</Text>
      {where ? <Text style={s.muted} numberOfLines={open ? undefined : 1}>{where}</Text> : null}
      <Text style={s.muted}>{item.active ? checkedText(item.last_checked_at) : 'Serviço inativo'}</Text>
      {item.open_alerts.critical || item.open_alerts.attention || item.open_alerts.info ? <Text style={s.muted}>Alertas abertos: {alertBreakdown(item.open_alerts)}</Text> : null}
      {open ? (
        <View style={s.details}>
          {item.last_message ? <Text style={s.message}>{item.last_message}</Text> : <Text style={s.muted}>Sem mensagem da última coleta.</Text>}
          {item.interval_seconds ? <Text style={s.muted}>Intervalo de coleta: {item.interval_seconds} s</Text> : null}
          <Text style={s.muted}>Severidade configurada: {item.severity}</Text>
          {item.responsible_team_name ? <Text style={s.muted}>Equipe responsável: {item.responsible_team_name}</Text> : null}
        </View>
      ) : null}
    </Card>
  );
}

export function EnvironmentsScreen({ state, contextLabel, consolidated, openServices }: { state: InventoryState<EnvironmentItem>; contextLabel: string; consolidated: boolean; openServices: (asset: { id: string; name: string }) => void }) {
  return <ListShell state={state} eyebrow="MONITORAMENTO" title="Ambientes" contextLabel={contextLabel} options={ENVIRONMENT_FILTERS} noun="ambiente(s)" renderItem={(item) => <EnvironmentCard item={item} showCompany={consolidated} openServices={openServices} />} />;
}

export function ServicesScreen({ state, contextLabel, consolidated, asset, clearAsset }: { state: InventoryState<ServiceItem>; contextLabel: string; consolidated: boolean; asset: { id: string; name: string } | null; clearAsset: () => void }) {
  const extra = asset ? (
    <View style={s.assetChip}>
      <Text style={s.assetText} numberOfLines={1}>Ambiente: {asset.name}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel="Remover filtro de ambiente" onPress={clearAsset} style={s.assetClear}><Text style={s.clearText}>✕</Text></Pressable>
    </View>
  ) : null;
  return <ListShell state={state} eyebrow="MONITORAMENTO" title="Serviços" contextLabel={contextLabel} options={SERVICE_FILTERS} noun="serviço(s)" extraHeader={extra} renderItem={(item) => <ServiceCard item={item} showCompany={consolidated} />} />;
}

const s = StyleSheet.create({
  page: { padding: 18, paddingBottom: 32, gap: 10 }, header: { gap: 10, marginBottom: 4 },
  eyebrow: { color: C.blue, fontSize: 12, fontWeight: '800', letterSpacing: 1 }, h1: { color: C.text, fontSize: 26, lineHeight: 32, fontWeight: '800' }, muted: { color: C.muted, fontSize: 13, lineHeight: 19 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, chip: { minHeight: 44, paddingHorizontal: 14, borderRadius: 22, borderWidth: 1, borderColor: C.border, backgroundColor: 'white', justifyContent: 'center' }, chipOn: { backgroundColor: C.blue, borderColor: C.blue },
  chipText: { color: C.text, fontWeight: '700', fontSize: 14 }, chipTextOn: { color: 'white' },
  search: { flexDirection: 'row', gap: 8, alignItems: 'center' }, input: { flex: 1, minHeight: 48, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 12, backgroundColor: 'white', color: C.text, fontSize: 15 },
  searchButton: { minHeight: 48, paddingHorizontal: 14, borderRadius: 12, backgroundColor: C.blue, justifyContent: 'center' }, searchText: { color: 'white', fontWeight: '800' },
  clearButton: { minHeight: 48, minWidth: 44, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: 'white', alignItems: 'center', justifyContent: 'center' }, clearText: { color: C.muted, fontWeight: '800' },
  summary: { color: C.text, fontSize: 13, fontWeight: '700' },
  assetChip: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#EFF6FF', borderColor: '#BFDBFE', borderWidth: 1, borderRadius: 12, paddingLeft: 12 }, assetText: { flex: 1, color: '#1E3A8A', fontWeight: '700' }, assetClear: { minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' },
  card: { flexDirection: 'row', backgroundColor: 'white', borderRadius: 14, borderWidth: 1, borderColor: C.border, overflow: 'hidden', minHeight: 64 }, bar: { width: 6 }, cardBody: { flex: 1, padding: 12, gap: 4 },
  cardTop: { flexDirection: 'row', alignItems: 'center', gap: 8 }, severity: { color: C.text, fontSize: 12, fontWeight: '800' }, ago: { color: C.muted, fontSize: 12, marginLeft: 'auto' },
  stale: { color: '#92400E', fontSize: 11, fontWeight: '800', backgroundColor: '#FEF3C7', borderRadius: 8, paddingHorizontal: 8, paddingVertical: 2, overflow: 'hidden' },
  title: { color: C.text, fontSize: 15, fontWeight: '700' }, details: { gap: 4, marginTop: 6, paddingTop: 8, borderTopWidth: 1, borderTopColor: C.border }, message: { color: C.text, fontSize: 13, lineHeight: 19 },
  link: { minHeight: 44, justifyContent: 'center' }, linkText: { color: C.blue, fontWeight: '800' },
  warn: { backgroundColor: '#FFF7ED', borderColor: '#FED7AA', borderWidth: 1, borderRadius: 12, padding: 12, gap: 4 }, warnText: { color: '#9A3412', fontSize: 13, lineHeight: 18 }, errorInline: { color: '#B91C1C', fontSize: 13 },
  empty: { color: C.muted, textAlign: 'center', paddingVertical: 32 },
  notice: { backgroundColor: '#EFF6FF', borderColor: '#BFDBFE', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, noticeTitle: { color: '#1E3A8A', fontWeight: '800' },
  errorBox: { backgroundColor: '#FEF2F2', borderColor: '#FECACA', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, errorTitle: { color: '#991B1B', fontWeight: '800' }, errorText: { color: '#7F1D1D', fontSize: 13 },
  retry: { minHeight: 44, borderRadius: 10, borderWidth: 1, borderColor: C.border, backgroundColor: 'white', alignItems: 'center', justifyContent: 'center' }, retryText: { color: C.text, fontWeight: '800' },
  more: { minHeight: 48, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: 'white', alignItems: 'center', justifyContent: 'center', marginTop: 4 }, moreText: { color: C.text, fontWeight: '800' },
});
