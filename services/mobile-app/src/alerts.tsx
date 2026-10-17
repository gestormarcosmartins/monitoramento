import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from 'react-native';
import { C } from './theme';
import { ALERT_FILTERS, AlertFilter, ago, alertParams, alertStatusLabel, severityLabel } from './monitoring-logic';

// Aba Alertas (CD-09): lista de GET /api/v1/alerts/consolidated, paginada por cursor, somente leitura.
// Sem "Meus", reconhecer ou atribuir: o backend nao tem responsavel nem estado "reconhecido" no alerta.

export type AlertItem = {
  id: string; tenant_id: string; tenant_name: string; title: string; message?: string | null; severity: string; severity_group: string; status: string;
  opened_at: string; resolved_at?: string | null; monitor_name?: string | null; asset_name?: string | null; incident_id?: string | null; incident_status?: string | null;
};
type Counts = { critical: number; attention: number; info: number };
type Requester = (path: string, options?: RequestInit, tenantHeader?: string) => Promise<any>;
type LoadStatus = 'idle' | 'loading' | 'ready' | 'unsupported' | 'error';

const INCIDENT_STATUS: Record<string, string> = { new: 'Novo', triaged: 'Triado', assigned: 'Atribuído', acknowledged: 'Reconhecido', in_progress: 'Em tratamento', waiting_customer: 'Aguardando cliente', waiting_supplier: 'Aguardando fornecedor', resolved: 'Resolvido', closed: 'Fechado', ignored: 'Ignorado' };
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—');
const hhmm = (date: Date | null) => (date ? date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—');

export function useAlerts({ enabled, scope, resetKey, request }: { enabled: boolean; scope: string; resetKey: string; request: Requester }) {
  const [filter, setFilter] = useState<AlertFilter>('problems');
  const [text, setText] = useState('');
  const [applied, setApplied] = useState('');
  const [items, setItems] = useState<AlertItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [status, setStatus] = useState<LoadStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(request); requestRef.current = request;
  const sequence = useRef(0);

  const clear = useCallback(() => {
    sequence.current += 1;
    setItems([]); setNextCursor(null); setTotal(null); setCounts(null); setFailed([]); setUpdatedAt(null); setStatus('idle'); setError(null); setLoading(false);
  }, []);

  const loadFirst = useCallback(async () => {
    const mine = ++sequence.current;
    setLoading(true); setError(null); setStatus((current) => (current === 'ready' ? current : 'loading'));
    try {
      const data = await requestRef.current(`/api/v1/alerts/consolidated?${alertParams({ filter, text: applied, scope })}`, {}, '');
      if (mine !== sequence.current) return;
      setItems(data.items || []); setNextCursor(data.next_cursor || null); setTotal(data.total ?? null); setCounts(data.counts || null);
      setFailed((data.tenants_failed || []).map((item: any) => item.tenant_name)); setUpdatedAt(new Date()); setStatus('ready');
    } catch (failure: any) {
      if (mine !== sequence.current) return;
      // 404/405: o servidor ainda nao publicou o endpoint. Nunca cai para GET /alerts (historico inteiro, sem paginacao).
      if (failure?.status === 404 || failure?.status === 405) setStatus('unsupported');
      else { setStatus('error'); setError(failure?.message || 'Falha ao carregar os alertas.'); }
    } finally { if (mine === sequence.current) setLoading(false); }
  }, [filter, applied, scope]);

  // Trocar de usuario, empresa, filtro ou busca descarta a lista anterior (nunca mostrar dados de outro escopo).
  useEffect(() => { clear(); }, [resetKey, scope, filter, applied, clear]);
  useEffect(() => { if (enabled) loadFirst(); }, [enabled, loadFirst]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loading) return;
    const mine = sequence.current;
    setLoading(true);
    try {
      const data = await requestRef.current(`/api/v1/alerts/consolidated?${alertParams({ filter, text: applied, scope, cursor: nextCursor })}`, {}, '');
      if (mine !== sequence.current) return;
      setItems((current) => { const seen = new Set(current.map((item) => item.id)); return [...current, ...(data.items || []).filter((item: AlertItem) => !seen.has(item.id))]; });
      setNextCursor(data.next_cursor || null);
      if (data.tenants_failed?.length) setFailed((current) => Array.from(new Set([...current, ...data.tenants_failed.map((item: any) => item.tenant_name)])));
    } catch (failure: any) {
      if (mine !== sequence.current) return;
      if (failure?.status === 422) loadFirst(); // cursor de outro filtro/escopo: recomeca do inicio
      else setError(failure?.message || 'Falha ao carregar mais alertas.');
    } finally { if (mine === sequence.current) setLoading(false); }
  }, [nextCursor, loading, filter, applied, scope, loadFirst]);

  return { filter, setFilter, text, setText, submitSearch: () => setApplied(text.trim()), clearSearch: () => { setText(''); setApplied(''); },
    items, nextCursor, total, counts, failed, updatedAt, status, error, loading, reload: loadFirst, loadMore, clear };
}

export function AlertCard({ item, showCompany, openTicket, level }: { item: AlertItem; showCompany: boolean; openTicket: (id: string, tenantId?: string) => void; level?: { label: string; color: string } }) {
  const [open, setOpen] = useState(false);
  const severity = level || severityLabel(item.severity_group);
  const where = [showCompany ? item.tenant_name : null, item.asset_name, item.monitor_name].filter(Boolean).join(' · ');
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={`${severity.label}, ${alertStatusLabel(item.status)}: ${item.title}`} onPress={() => setOpen(!open)} style={s.card}>
      <View style={[s.bar, { backgroundColor: severity.color }]} />
      <View style={s.cardBody}>
        <View style={s.cardTop}>
          <Text style={s.severity}>{severity.label}</Text>
          <Text style={[s.stateTag, item.status !== 'open' && s.stateMuted]}>{alertStatusLabel(item.status)}</Text>
          <Text style={s.ago}>{ago(item.opened_at)}</Text>
        </View>
        <Text style={s.title} numberOfLines={open ? undefined : 2}>{item.title}</Text>
        {where ? <Text style={s.muted} numberOfLines={open ? undefined : 1}>{where}</Text> : null}
        {open ? (
          <View style={s.details}>
            {item.message ? <Text style={s.message}>{item.message}</Text> : null}
            <Text style={s.muted}>Aberto em {when(item.opened_at)}</Text>
            {item.resolved_at ? <Text style={s.muted}>Resolvido em {when(item.resolved_at)}</Text> : null}
            <Text style={s.muted}>Severidade: {item.severity}</Text>
            {item.incident_id ? (
              <View style={s.incident}>
                <Text style={s.muted}>Incidente: {INCIDENT_STATUS[item.incident_status || ''] || item.incident_status || '—'}</Text>
                <Pressable accessibilityRole="button" onPress={() => openTicket(item.incident_id as string, item.tenant_id)} style={s.link}><Text style={s.linkText}>Abrir chamado</Text></Pressable>
              </View>
            ) : <Text style={s.muted}>Sem incidente vinculado.</Text>}
          </View>
        ) : null}
      </View>
    </Pressable>
  );
}

export function AlertsScreen({ state, contextLabel, consolidated, openTicket }: { state: ReturnType<typeof useAlerts>; contextLabel: string; consolidated: boolean; openTicket: (id: string, tenantId?: string) => void }) {
  const { filter, setFilter, text, setText, submitSearch, clearSearch, items, nextCursor, total, counts, failed, updatedAt, status, error, loading, reload, loadMore } = state;
  const header = (
    <View style={s.header}>
      <Text style={s.eyebrow}>MONITORAMENTO</Text>
      <Text style={s.h1}>Alertas</Text>
      <Text style={s.muted}>{contextLabel} · Atualizado às {hhmm(updatedAt)}</Text>
      <View style={s.chips} accessibilityRole="radiogroup">
        {ALERT_FILTERS.map((option) => {
          const on = option.key === filter;
          return <Pressable key={option.key} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => setFilter(option.key)} style={[s.chip, on && s.chipOn]}><Text style={[s.chipText, on && s.chipTextOn]}>{option.label}</Text></Pressable>;
        })}
      </View>
      <View style={s.search}>
        <TextInput value={text} onChangeText={setText} onSubmitEditing={submitSearch} returnKeyType="search" placeholder="Buscar por título ou mensagem" placeholderTextColor="#7F93B0" autoCapitalize="none" accessibilityLabel="Buscar alertas" style={s.input} />
        <Pressable accessibilityRole="button" onPress={submitSearch} style={s.searchButton}><Text style={s.searchText}>Buscar</Text></Pressable>
        {text ? <Pressable accessibilityRole="button" accessibilityLabel="Limpar busca" onPress={clearSearch} style={s.clearButton}><Text style={s.clearText}>✕</Text></Pressable> : null}
      </View>
      {status === 'ready' && total !== null ? (
        <Text style={s.summary}>{total} alerta(s){counts ? ` · ${counts.critical} crítico(s) · ${counts.attention} de atenção · ${counts.info} informativo(s)` : ''}{nextCursor ? ` · mostrando ${items.length}` : ''}</Text>
      ) : null}
      {failed.length ? (
        <View style={s.warn}><Text style={s.warnText}>Dados incompletos: não foi possível carregar {failed.join(', ')}.</Text><Pressable accessibilityRole="button" onPress={reload} style={s.link}><Text style={s.linkText}>Tentar novamente</Text></Pressable></View>
      ) : null}
      {status === 'ready' && error ? <Text style={s.errorInline}>{error}</Text> : null}
    </View>
  );

  let empty: React.ReactNode = null;
  if (status === 'unsupported') empty = <View style={s.notice}><Text style={s.noticeTitle}>Lista de alertas indisponível</Text><Text style={s.muted}>Este servidor ainda não tem a listagem consolidada de alertas. Peça a atualização do backend; a Visão geral continua funcionando.</Text><Pressable accessibilityRole="button" onPress={reload} style={s.retry}><Text style={s.retryText}>Verificar novamente</Text></Pressable></View>;
  else if (status === 'error') empty = <View style={s.errorBox}><Text style={s.errorTitle}>Não foi possível carregar os alertas</Text><Text style={s.errorText}>{error}</Text><Pressable accessibilityRole="button" onPress={reload} style={s.retry}><Text style={s.retryText}>Tentar novamente</Text></Pressable></View>;
  else if (status === 'ready') empty = <Text style={s.empty}>Nenhum alerta para este filtro.</Text>;
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
      renderItem={({ item }) => <AlertCard item={item} showCompany={consolidated} openTicket={openTicket} />}
      keyboardShouldPersistTaps="handled"
    />
  );
}

const s = StyleSheet.create({
  page: { padding: 18, paddingBottom: 32, gap: 10 }, header: { gap: 10, marginBottom: 4 },
  eyebrow: { color: C.blueText, fontSize: 12, fontWeight: '800', letterSpacing: 1 }, h1: { color: C.text, fontSize: 26, lineHeight: 32, fontWeight: '800' }, muted: { color: C.muted, fontSize: 13, lineHeight: 19 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, chip: { minHeight: 44, paddingHorizontal: 14, borderRadius: 22, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, justifyContent: 'center' }, chipOn: { backgroundColor: C.blue, borderColor: C.blue },
  chipText: { color: C.text, fontWeight: '700', fontSize: 14 }, chipTextOn: { color: 'white' },
  search: { flexDirection: 'row', gap: 8, alignItems: 'center' }, input: { flex: 1, minHeight: 48, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 12, backgroundColor: C.card, color: C.text, fontSize: 15 },
  searchButton: { minHeight: 48, paddingHorizontal: 14, borderRadius: 12, backgroundColor: C.blue, justifyContent: 'center' }, searchText: { color: 'white', fontWeight: '800' },
  clearButton: { minHeight: 48, minWidth: 44, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, alignItems: 'center', justifyContent: 'center' }, clearText: { color: C.muted, fontWeight: '800' },
  summary: { color: C.text, fontSize: 13, fontWeight: '700' },
  card: { flexDirection: 'row', backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, overflow: 'hidden', minHeight: 64 }, bar: { width: 6 }, cardBody: { flex: 1, padding: 12, gap: 4 },
  cardTop: { flexDirection: 'row', alignItems: 'center', gap: 8 }, severity: { color: C.text, fontSize: 12, fontWeight: '800' }, stateTag: { color: C.text, fontSize: 12, fontWeight: '700', backgroundColor: '#3D3010', borderRadius: 8, paddingHorizontal: 8, paddingVertical: 2, overflow: 'hidden' }, stateMuted: { backgroundColor: '#16335C' }, ago: { color: C.muted, fontSize: 12, marginLeft: 'auto' },
  title: { color: C.text, fontSize: 15, fontWeight: '700' }, details: { gap: 4, marginTop: 6, paddingTop: 8, borderTopWidth: 1, borderTopColor: C.border }, message: { color: C.text, fontSize: 13, lineHeight: 19 },
  incident: { gap: 4 }, link: { minHeight: 44, justifyContent: 'center' }, linkText: { color: C.blueText, fontWeight: '800' },
  warn: { backgroundColor: '#3A2A12', borderColor: '#7C4A12', borderWidth: 1, borderRadius: 12, padding: 12, gap: 4 }, warnText: { color: '#FDBA74', fontSize: 13, lineHeight: 18 }, errorInline: { color: '#FCA5A5', fontSize: 13 },
  empty: { color: C.muted, textAlign: 'center', paddingVertical: 32 },
  notice: { backgroundColor: '#12305C', borderColor: '#27508F', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, noticeTitle: { color: '#BFDBFE', fontWeight: '800' },
  errorBox: { backgroundColor: '#3B1620', borderColor: '#7F2A35', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, errorTitle: { color: '#FCA5A5', fontWeight: '800' }, errorText: { color: '#FCA5A5', fontSize: 13 },
  retry: { minHeight: 44, borderRadius: 10, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, alignItems: 'center', justifyContent: 'center' }, retryText: { color: C.text, fontWeight: '800' },
  more: { minHeight: 48, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, alignItems: 'center', justifyContent: 'center', marginTop: 4 }, moreText: { color: C.text, fontWeight: '800' },
});
