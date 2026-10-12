import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';
import { AlertFilter, InventoryFilter, POLL_MS, STATUS, ago, badgeFrom, nextDelay, serviceState } from './monitoring-logic';

export { badgeFrom };

// Visao Geral do Monitoramento (CD-09). Tudo vem de GET /api/v1/dashboard, que ja consolida as empresas autorizadas.
// A lista de alertas fica em alerts.tsx (GET /api/v1/alerts/consolidated). Sem estado "reconhecido" no backend, nao ha acoes de alerta.

type Totals = { critical_alerts: number; attention_alerts: number; open_incidents: number; services_active: number; services_failed: number; services_normalizing: number; services_inactive: number; availability: number; companies_with_alerts: number };
type CompanyRow = { id: string; name: string; services: number; open_alerts: number; critical_alerts: number; attention_alerts: number; services_failed: number; services_normalizing: number };
type IncidentRow = { id: string; title: string; priority: string; status: string; created_at: string; company: string; service_name?: string | null };
type ServiceRow = { id: string; name: string; company: string; last_status: string; active: boolean; incidents_7d: number };
type EnvironmentRow = { id: string; name: string; company: string; services: number; failures: number; status: string };
export type Dashboard = { totals: Totals; companies: CompanyRow[]; recent_incidents: IncidentRow[]; critical_services: ServiceRow[]; environments: EnvironmentRow[] };
type Requester = (path: string, options?: RequestInit, tenantHeader?: string) => Promise<any>;

const PRIORITY: Record<string, string> = { critical: 'Crítica', high: 'Alta', medium: 'Média', low: 'Baixa' };
const INCIDENT_STATUS: Record<string, string> = { new: 'Novo', acknowledged: 'Reconhecido', in_progress: 'Em tratamento', resolved: 'Resolvido', ignored: 'Ignorado', triaged: 'Triado', assigned: 'Atribuído' };

export function useDashboard({ enabled, scope, request }: { enabled: boolean; scope: string; request: Requester }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(request); requestRef.current = request;
  const delayRef = useRef(POLL_MS);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = scope && scope !== 'all' ? `?tenant_ids=${encodeURIComponent(scope)}` : '';
      const next = await requestRef.current(`/api/v1/dashboard${query}`, {}, '');
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
function Kpi({ value, label, color, note, onPress, hint }: { value: number; label: string; color: string; note?: string; onPress?: () => void; hint?: string }) {
  const body = (
    <>
      <View style={[s.kpiBar, { backgroundColor: color }]} />
      <Text style={s.kpiValue}>{value}</Text>
      <Text style={s.kpiLabel}>{label}</Text>
      {note ? <Text style={s.kpiNote}>{note}</Text> : null}
    </>
  );
  // So e clicavel quando existe a tela de destino (alertas ou servicos); os demais indicadores nao viram botao sem destino.
  if (!onPress) return <View style={s.kpi} accessible accessibilityLabel={`${label}: ${value}`}>{body}</View>;
  return <Pressable style={s.kpi} accessibilityRole="button" accessibilityLabel={`${label}: ${value}`} accessibilityHint={hint} onPress={onPress}>{body}</Pressable>;
}

function HealthBar({ totals }: { totals: Totals }) {
  const parts = [
    { key: 'ok', label: 'Saudáveis', value: totals.services_active, color: STATUS.healthy },
    { key: 'down', label: 'Indisponíveis', value: totals.services_failed, color: STATUS.critical },
    { key: 'unknown', label: 'Sem dados', value: totals.services_normalizing, color: STATUS.unknown },
    { key: 'off', label: 'Inativos', value: totals.services_inactive, color: STATUS.inactive },
  ];
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  if (!total) return <Text style={s.muted}>Nenhum serviço monitorado neste escopo.</Text>;
  return (
    <View style={{ gap: 10 }}>
      <View style={s.stack} accessibilityRole="image" accessibilityLabel={parts.map((part) => `${part.label}: ${part.value}`).join(', ')}>
        {parts.filter((part) => part.value > 0).map((part) => <View key={part.key} style={{ flex: part.value, backgroundColor: part.color }} />)}
      </View>
      <View style={s.legend}>
        {parts.map((part) => <View key={part.key} style={s.legendItem}><View style={[s.swatch, { backgroundColor: part.color }]} /><Text style={s.legendText}>{part.label} <Text style={s.legendNumber}>{part.value}</Text></Text></View>)}
      </View>
    </View>
  );
}

function Section({ title, hint, children, action }: { title: string; hint?: string; children: React.ReactNode; action?: { label: string; onPress: () => void } }) {
  return <View style={s.section}><View style={s.sectionHead}><Text style={s.h2}>{title}</Text>{action ? <Pressable accessibilityRole="button" onPress={action.onPress} style={s.sectionAction}><Text style={s.link}>{action.label}</Text></Pressable> : null}</View>{hint ? <Text style={s.muted}>{hint}</Text> : null}{children}</View>;
}

export function MonitoringScreen({ state, contextLabel, consolidated, onOpenAlerts, onOpenServices, onOpenEnvironments }: { state: ReturnType<typeof useDashboard>; contextLabel: string; consolidated: boolean; onOpenAlerts?: (filter: AlertFilter) => void; onOpenServices?: (filter: InventoryFilter) => void; onOpenEnvironments?: () => void }) {
  const { data, error, updatedAt, loading, reload } = state;
  const refresh = <RefreshControl refreshing={loading} onRefresh={reload} />;
  if (!data) {
    return (
      <ScrollView contentContainerStyle={s.page} refreshControl={refresh}>
        <Header context={contextLabel} updatedAt={updatedAt} />
        {error ? (
          <View style={s.errorBox}><Text style={s.errorTitle}>Não foi possível carregar o monitoramento</Text><Text style={s.errorText}>{error}</Text><Pressable accessibilityRole="button" onPress={reload} style={s.retry}><Text style={s.retryText}>Tentar novamente</Text></Pressable></View>
        ) : <Text style={s.muted}>Carregando…</Text>}
      </ScrollView>
    );
  }
  const t = data.totals;
  return (
    <ScrollView contentContainerStyle={s.page} refreshControl={refresh}>
      <Header context={contextLabel} updatedAt={updatedAt} />
      {error ? <View style={s.warnBox}><Text style={s.warnText}>Falha ao atualizar ({error}). Exibindo os dados de {hhmm(updatedAt)}.</Text><Pressable accessibilityRole="button" onPress={reload}><Text style={s.link}>Tentar novamente</Text></Pressable></View> : null}

      <Section title="Alertas e serviços" hint="Alertas abertos e serviços ativos no escopo autorizado.">
        <View style={s.kpis}>
          <Kpi value={t.critical_alerts} label="Alertas críticos" color={STATUS.critical} note="alta + desastre" hint="Abre a lista de alertas" onPress={onOpenAlerts ? () => onOpenAlerts('critical') : undefined} />
          <Kpi value={t.attention_alerts} label="Alertas de atenção" color={STATUS.attention} note="aviso + média" hint="Abre a lista de alertas" onPress={onOpenAlerts ? () => onOpenAlerts('attention') : undefined} />
          <Kpi value={t.services_active} label="Serviços saudáveis" color={STATUS.healthy} hint="Abre a lista de serviços" onPress={onOpenServices ? () => onOpenServices('healthy') : undefined} />
          <Kpi value={t.services_failed} label="Serviços indisponíveis" color={STATUS.critical} hint="Abre a lista de serviços" onPress={onOpenServices ? () => onOpenServices('problems') : undefined} />
        </View>
        <View style={s.kpis}>
          <Kpi value={t.services_normalizing} label="Sem dados" color={STATUS.unknown} note="não contam como saudáveis" hint="Abre a lista de serviços" onPress={onOpenServices ? () => onOpenServices('unknown') : undefined} />
          <Kpi value={t.open_incidents} label="Incidentes abertos" color={STATUS.attention} />
        </View>
      </Section>

      <Section title="Saúde dos serviços" hint={`Disponibilidade: ${Number(t.availability).toFixed(1).replace('.', ',')}% dos serviços ativos`}>
        <HealthBar totals={t} />
      </Section>

      {consolidated && data.companies.length > 1 ? (
        <Section title="Por empresa">
          {data.companies.map((company) => (
            <View key={company.id} style={s.row}>
              <View style={s.grow}><Text style={s.rowTitle}>{company.name}</Text><Text style={s.muted}>{company.services} serviço(s) · {company.services_failed} indisponível(is) · {company.services_normalizing} sem dados</Text></View>
              <View style={s.alignEnd}><Text style={s.count}>{company.open_alerts}</Text><Text style={s.countLabel}>alerta(s)</Text></View>
            </View>
          ))}
        </Section>
      ) : null}

      <Section title="Serviços mais críticos" hint="Por incidentes nos últimos 7 dias." action={onOpenServices ? { label: 'Ver todos', onPress: () => onOpenServices('all') } : undefined}>
        {data.critical_services.length ? data.critical_services.map((service) => {
          const state_ = serviceState(service);
          return (
            <View key={service.id} style={s.row}>
              <View style={s.grow}><Text style={s.rowTitle} numberOfLines={1}>{service.name}</Text><Text style={s.muted}>{service.company} · {service.incidents_7d} incidente(s)</Text></View>
              <View style={s.stateTag}><View style={[s.swatch, { backgroundColor: state_.color }]} /><Text style={s.stateText}>{state_.label}</Text></View>
            </View>
          );
        }) : <Text style={s.muted}>Nenhum serviço cadastrado.</Text>}
      </Section>

      <Section title="Incidentes recentes" hint="Incidentes são distintos de alertas; cada um nasce de um alerta.">
        {data.recent_incidents.length ? data.recent_incidents.map((incident) => (
          <View key={incident.id} style={s.row}>
            <View style={s.grow}><Text style={s.rowTitle} numberOfLines={2}>{incident.title}</Text><Text style={s.muted}>{incident.company}{incident.service_name ? ` · ${incident.service_name}` : ''} · {ago(incident.created_at)}</Text></View>
            <View style={s.alignEnd}><Text style={s.stateText}>{PRIORITY[incident.priority] || incident.priority}</Text><Text style={s.countLabel}>{INCIDENT_STATUS[incident.status] || incident.status}</Text></View>
          </View>
        )) : <Text style={s.muted}>Nenhum incidente registrado.</Text>}
      </Section>

      <Section title="Ambientes" action={onOpenEnvironments ? { label: 'Ver todos', onPress: onOpenEnvironments } : undefined}>
        {data.environments.length ? data.environments.map((environment) => (
          <View key={environment.id} style={s.row}>
            <View style={s.grow}><Text style={s.rowTitle} numberOfLines={1}>{environment.name}</Text><Text style={s.muted}>{environment.company} · {environment.services} serviço(s)</Text></View>
            <View style={s.stateTag}><View style={[s.swatch, { backgroundColor: environment.status === 'attention' ? STATUS.attention : STATUS.healthy }]} /><Text style={s.stateText}>{environment.status === 'attention' ? `${environment.failures} em falha` : 'Sem falhas'}</Text></View>
          </View>
        )) : <Text style={s.muted}>Nenhum ambiente cadastrado.</Text>}
      </Section>
    </ScrollView>
  );
}

function Header({ context, updatedAt }: { context: string; updatedAt: Date | null }) {
  return (
    <View style={{ gap: 2 }}>
      <Text style={s.eyebrow}>MONITORAMENTO</Text>
      <Text style={s.h1}>Visão geral</Text>
      <Text style={s.muted}>{context} · Atualizado às {hhmm(updatedAt)}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  page: { padding: 18, paddingBottom: 32, gap: 16 }, section: { gap: 10 }, sectionHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, sectionAction: { minHeight: 44, justifyContent: 'center' },
  eyebrow: { color: C.blue, fontSize: 12, fontWeight: '800', letterSpacing: 1 }, h1: { color: C.text, fontSize: 26, lineHeight: 32, fontWeight: '800' }, h2: { color: C.text, fontSize: 18, fontWeight: '700' },
  muted: { color: C.muted, fontSize: 13, lineHeight: 19 },
  kpis: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  kpi: { flexGrow: 1, flexBasis: '47%', backgroundColor: 'white', borderRadius: 14, padding: 12, borderWidth: 1, borderColor: C.border, overflow: 'hidden' },
  kpiBar: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 5 }, kpiValue: { color: C.text, fontSize: 28, fontWeight: '900', marginLeft: 4 }, kpiLabel: { color: C.text, fontSize: 13, fontWeight: '700', marginLeft: 4 }, kpiNote: { color: C.muted, fontSize: 11, marginLeft: 4 },
  stack: { flexDirection: 'row', height: 16, borderRadius: 8, overflow: 'hidden', backgroundColor: '#E2E8F0' }, legend: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 }, legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 }, swatch: { width: 12, height: 12, borderRadius: 3 }, legendText: { color: C.text, fontSize: 13 }, legendNumber: { fontWeight: '800' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: 'white', borderRadius: 12, padding: 12, borderWidth: 1, borderColor: C.border, minHeight: 56 }, grow: { flex: 1 }, alignEnd: { alignItems: 'flex-end' },
  rowTitle: { color: C.text, fontSize: 15, fontWeight: '700' }, count: { color: C.text, fontSize: 22, fontWeight: '900' }, countLabel: { color: C.muted, fontSize: 11 },
  stateTag: { flexDirection: 'row', alignItems: 'center', gap: 6 }, stateText: { color: C.text, fontSize: 12, fontWeight: '800' },
  errorBox: { backgroundColor: '#FEF2F2', borderColor: '#FECACA', borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 }, errorTitle: { color: '#991B1B', fontWeight: '800' }, errorText: { color: '#7F1D1D', fontSize: 13 },
  retry: { minHeight: 44, borderRadius: 10, borderWidth: 1, borderColor: '#FECACA', backgroundColor: 'white', alignItems: 'center', justifyContent: 'center' }, retryText: { color: '#991B1B', fontWeight: '800' },
  warnBox: { backgroundColor: '#FFF7ED', borderColor: '#FED7AA', borderWidth: 1, borderRadius: 12, padding: 12, gap: 6 }, warnText: { color: '#9A3412', fontSize: 13, lineHeight: 18 }, link: { color: C.blue, fontWeight: '800', minHeight: 44, textAlignVertical: 'center' },
});
