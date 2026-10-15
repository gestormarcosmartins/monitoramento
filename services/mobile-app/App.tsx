import { StatusBar } from 'expo-status-bar';
import * as AuthSession from 'expo-auth-session';
import * as SecureStore from 'expo-secure-store';
import * as WebBrowser from 'expo-web-browser';
import * as SplashScreen from 'expo-splash-screen';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, AppState, FlatList, Image, ImageBackground, InteractionManager, KeyboardAvoidingView, Linking, Platform, Pressable, RefreshControl, SafeAreaView, ScrollView, StatusBar as RNStatusBar, StyleSheet, Text, TextInput, View } from 'react-native';

import { C } from './src/theme';
import { onPushTap, registerForPush, unregisterPush } from './src/push';
import { Brand } from './src/brand';
import { TabBar } from './src/tabbar';
import { badgeFrom, useDashboard } from './src/monitoring';
import { NocScreen, useNoc } from './src/noc';
import { AlertsScreen, useAlerts } from './src/alerts';
import { MonitoringHub, MonitoringSection, AssetFilter } from './src/monitoring-hub';
import { EnvironmentItem, ServiceItem, useInventory } from './src/inventory';
import { Welcome, ServerStatus } from './src/welcome';
import { Icon } from './src/icons';
import { unreadBadgeText, validSavedTenant } from './src/session-logic';
import { NotificationsScreen, NotificationItem, useNotificationCount, useNotifications } from './src/notifications';

WebBrowser.maybeCompleteAuthSession();
SplashScreen.preventAutoHideAsync().catch(() => {});

type Tenant = { id: string; name: string; role: string };
type User = { id: string; display_name?: string; email?: string; tenants: Tenant[] };
type Ticket = { id: string; glpi_ticket_id?: number; title: string; description?: string; priority: string; status: string; sync_status?: string; updated_at?: string; created_at?: string; version: number; assignee_name?: string; assignee_team_name?: string; tenant_id?: string; tenant_name?: string; resolution_due_at?: string };
type Summary = { open: number; critical: number; overdue: number };
type TicketDetail = { ticket: Ticket; entries: Array<{ id: string; body: string; is_internal: boolean; created_at: string }>; tasks: Array<{ id: string; title: string; status: string }>; solutions: Array<{ id: string; description: string; status: string }> };
type Tab = 'home' | 'monitoring' | 'alerts' | 'tickets' | 'settings' | 'new' | 'noc' | 'notifications';
const TAB_TITLE: Record<Tab, string> = { home: 'Início', monitoring: 'Monitorar', alerts: 'Alertas', tickets: 'Chamados', settings: 'Mais', new: 'Novo chamado', noc: 'NOC', notifications: 'Notificações' };
const tenantKey = (userId: string) => `nexora.tenant.${userId}`;
type TestStep = { label: string; ok: boolean; ms: number; message: string };

const STORE = { server: 'nexora.server', access: 'nexora.access', refresh: 'nexora.refresh', pkce: 'nexora.pkce' };
const DEFAULT_SERVER = (process.env.EXPO_PUBLIC_NEXORA_SERVER || 'https://nexora.simmservices.com.br').replace(/\/+$/, '');
const STALE_SERVER = /sslip\.io/i;
const ALL_TENANTS = 'all';
const CONSOLIDATED_PAGE = 50;
const redirectUri = AuthSession.makeRedirectUri({ scheme: 'nexora', path: 'auth' });
const statusLabel: Record<string, string> = { new: 'Novo', triaged: 'Triado', assigned: 'Atribuído', acknowledged: 'Reconhecido', in_progress: 'Em atendimento', waiting_customer: 'Aguardando cliente', waiting_supplier: 'Aguardando fornecedor', resolved: 'Resolvido', closed: 'Fechado', cancelled: 'Cancelado', duplicate: 'Duplicado' };
const priorityLabel: Record<string, string> = { low: 'Baixa', medium: 'Média', high: 'Alta', critical: 'Crítica' };

async function timed(label: string, run: () => Promise<string>): Promise<TestStep> { const started = Date.now(); try { const message = await run(); return { label, ok: true, ms: Date.now() - started, message }; } catch (error: any) { const aborted = error?.name === 'AbortError'; return { label, ok: false, ms: Date.now() - started, message: aborted ? 'Sem resposta em 10 segundos.' : (error?.message || 'Falha desconhecida.') }; } }
async function probe(url: string, headers: Record<string, string> = {}) { const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000); try { return await fetch(url, { headers, signal: controller.signal, redirect: 'manual' as RequestRedirect }); } finally { clearTimeout(timer); } }
function normalizeServer(value: string) { const clean = value.trim().replace(/\/+$/, ''); if (!/^https?:\/\//i.test(clean)) throw new Error('Informe uma URL iniciada por http:// ou https://'); return clean; }
function Button({ title, onPress, kind = 'primary', disabled = false }: { title: string; onPress: () => void; kind?: 'primary' | 'secondary' | 'danger'; disabled?: boolean }) { return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={({ pressed }) => [s.button, s[`button_${kind}`], (disabled || pressed) && s.buttonDim]}><Text style={[s.buttonText, kind === 'secondary' && s.buttonSecondaryText]}>{title}</Text></Pressable>; }
function Field({ label, value, onChangeText, placeholder, multiline = false }: any) { return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput value={value} onChangeText={onChangeText} placeholder={placeholder} placeholderTextColor="#7F93B0" multiline={multiline} autoCapitalize="none" style={[s.input, multiline && s.textarea]} /></View>; }
function Badge({ text, tone = 'neutral' }: { text: string; tone?: 'neutral' | 'success' | 'warning' | 'danger' }) { return <View style={[s.badge, s[`badge_${tone}`]]}><Text style={s.badgeText}>{text}</Text></View>; }

export default function App() {
  const [booting, setBooting] = useState(true); const [busy, setBusy] = useState(false);
  const [loginActivityReady, setLoginActivityReady] = useState(false);
  const [server, setServer] = useState(DEFAULT_SERVER); const [serverDraft, setServerDraft] = useState(DEFAULT_SERVER);
  const [token, setToken] = useState(''); const [refreshToken, setRefreshToken] = useState('');
  const accessTokenRef = useRef(''); const refreshTokenRef = useRef('');
  const refreshPromiseRef = useRef<Promise<string> | null>(null);
  const authRequestRef = useRef<AuthSession.AuthRequest | null>(null);
  const automaticLoginStartedRef = useRef(false); const exchangingRef = useRef(false); const mutatingRef = useRef(false);
  const [me, setMe] = useState<User | null>(null); const [tenantId, setTenantId] = useState('');
  const [tab, setTab] = useState<Tab>('home'); const [returnTab, setReturnTab] = useState<Tab>('home'); const [tickets, setTickets] = useState<Ticket[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null); const [serverTotal, setServerTotal] = useState<number | null>(null);
  const [failedCompanies, setFailedCompanies] = useState<string[]>([]); const [summary, setSummary] = useState<Summary | null>(null);
  const consolidatedRef = useRef(true);
  const [selected, setSelected] = useState<TicketDetail | null>(null); const [notice, setNotice] = useState('');
  const [entered, setEntered] = useState(false); const [serverStatus, setServerStatus] = useState<ServerStatus>({ state: 'checking' });
  const [testing, setTesting] = useState(false); const [testResults, setTestResults] = useState<TestStep[]>([]);
  const consolidated = tenantId === ALL_TENANTS;
  const tenant = consolidated ? { id: ALL_TENANTS, name: 'Todas as empresas', role: `${me?.tenants.length || 0} empresas` } : me?.tenants.find((item) => item.id === tenantId);
  const canOperateIn = (id?: string) => ['master', 'admin', 'manager', 'operator'].includes(me?.tenants.find((item) => item.id === id)?.role || '');
  const discovery = useMemo(() => server ? ({ authorizationEndpoint: `${server}/auth/realms/central-monitor/protocol/openid-connect/auth`, tokenEndpoint: `${server}/auth/realms/central-monitor/protocol/openid-connect/token`, revocationEndpoint: `${server}/auth/realms/central-monitor/protocol/openid-connect/revoke`, endSessionEndpoint: `${server}/auth/realms/central-monitor/protocol/openid-connect/logout` }) : null, [server]);

  useEffect(() => { (async () => { const [savedAccess, savedRefresh, savedServer] = await Promise.all([SecureStore.getItemAsync(STORE.access), SecureStore.getItemAsync(STORE.refresh), SecureStore.getItemAsync(STORE.server)]); const usable = savedServer && !STALE_SERVER.test(savedServer) ? savedServer : DEFAULT_SERVER; if (usable !== savedServer) { await Promise.all([SecureStore.setItemAsync(STORE.server, usable), SecureStore.deleteItemAsync(STORE.access), SecureStore.deleteItemAsync(STORE.refresh)]); setServer(usable); setServerDraft(usable); setBooting(false); return; } setServer(usable); setServerDraft(usable); if (savedAccess) { accessTokenRef.current = savedAccess; setToken(savedAccess); } if (savedRefresh) { refreshTokenRef.current = savedRefresh; setRefreshToken(savedRefresh); } setBooting(false); })(); }, []);
  useEffect(() => { let timer: ReturnType<typeof setTimeout> | undefined; const prepare = (state = AppState.currentState) => { if (state !== 'active' || timer) return; InteractionManager.runAfterInteractions(() => { timer = setTimeout(() => setLoginActivityReady(true), 900); }); }; prepare(); const subscription = AppState.addEventListener('change', prepare); return () => { subscription.remove(); if (timer) clearTimeout(timer); }; }, []);
  useEffect(() => { const subscription = Linking.addEventListener('url', ({ url }) => completeLogin(url)); Linking.getInitialURL().then((url) => { if (url) completeLogin(url); }); return () => subscription.remove(); }, [discovery]);
  useEffect(() => { if (!booting) SplashScreen.hideAsync().catch(() => {}); }, [booting]);
  useEffect(() => { if (server && token && !me) loadIdentity(); }, [server, token]);
  useEffect(() => { if (!me) return; registerForPush(request); }, [me?.id]);
  useEffect(() => { if (!me) return; return onPushTap(async (target) => { try { await request(`/api/v1/notificacoes/${target.notificationId}/abrir`, { method: 'POST' }, target.tenantId); if (target.actionType === 'INCIDENT' && target.actionRef) await openTicket(target.actionRef, target.tenantId); } catch (error: any) { Alert.alert('Notificação', error.message); } }); }, [me?.id]);
  useEffect(() => { if (me && tenantId) loadTickets(); }, [tenantId]);
  useEffect(() => { if (me && !entered) checkServer(); }, [me, entered]);

  const dashboard = useDashboard({ enabled: !!me && entered, scope: tenantId, request });
  const notifScope = useMemo(() => (consolidated ? me?.tenants || [] : tenant ? [tenant] : []), [consolidated, me, tenantId]);
  const notifCount = useNotificationCount({ enabled: !!me && entered, scope: notifScope, request });
  const notifications = useNotifications({ enabled: !!me && entered && tab === 'notifications', scope: notifScope, request });
  const noc = useNoc({ enabled: !!me && entered && tab === 'noc', scope: tenantId, request });
  const [monitorSection, setMonitorSection] = useState<MonitoringSection>('overview');
  const [serviceAsset, setServiceAsset] = useState<AssetFilter>(null);
  useEffect(() => { setServiceAsset(null); }, [tenantId]);
  const environments = useInventory<EnvironmentItem>({ kind: 'environments', enabled: !!me && entered && tab === 'monitoring' && monitorSection === 'environments', scope: tenantId, resetKey: me?.id || '', request });
  const services = useInventory<ServiceItem>({ kind: 'services', enabled: !!me && entered && tab === 'monitoring' && monitorSection === 'services', scope: tenantId, resetKey: me?.id || '', request, assetId: serviceAsset?.id });
  const alerts = useAlerts({ enabled: !!me && entered && (tab === 'alerts' || (tab === 'monitoring' && monitorSection === 'alerts')), scope: tenantId, resetKey: me?.id || '', request });
  async function renewAccess() {
    if (refreshPromiseRef.current) return refreshPromiseRef.current;
    if (!discovery || !refreshTokenRef.current) throw new Error('Sessão expirada. Entre novamente.');
    refreshPromiseRef.current = (async () => {
      try {
        const result = await AuthSession.refreshAsync({ clientId: 'central-monitor', refreshToken: refreshTokenRef.current }, discovery);
        accessTokenRef.current = result.accessToken;
        setToken(result.accessToken);
        await SecureStore.setItemAsync(STORE.access, result.accessToken);
        if (result.refreshToken) {
          refreshTokenRef.current = result.refreshToken;
          setRefreshToken(result.refreshToken);
          await SecureStore.setItemAsync(STORE.refresh, result.refreshToken);
        }
        return result.accessToken;
      } catch {
        await logout(false);
        throw new Error('Sua sessão expirou. Entre novamente para continuar.');
      } finally {
        refreshPromiseRef.current = null;
      }
    })();
    return refreshPromiseRef.current;
  }
  async function parseResponse(response: Response) { if (!response.ok) { let message = `Falha ${response.status}`; try { const data = await response.json(); const text = typeof data.message === 'string' ? data.message : typeof data.detail === 'string' ? data.detail : ''; message = `${text || message} (HTTP ${response.status}${data.correlation_id ? `, ref ${data.correlation_id}` : ''})`; } catch { message = `${message} (HTTP ${response.status})`; } throw Object.assign(new Error(message), { status: response.status }); } return response.status === 204 ? null : response.json(); }
  async function request(path: string, options: RequestInit = {}, tenantHeader = tenantId, retry = true): Promise<any> { if (tenantHeader === ALL_TENANTS) throw new Error('Selecione uma empresa específica para esta operação.'); const headers: Record<string, string> = { Accept: 'application/json', Authorization: `Bearer ${accessTokenRef.current}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}) }; if (tenantHeader) headers['X-Tenant-ID'] = tenantHeader; let response = await fetch(`${server}${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } }); if (response.status === 401 && retry && refreshTokenRef.current) { headers.Authorization = `Bearer ${await renewAccess()}`; response = await fetch(`${server}${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } }); } return parseResponse(response); }
  async function saveServer() { try { const next = normalizeServer(serverDraft); const changed = next !== server; await SecureStore.setItemAsync(STORE.server, next); setServer(next); setServerDraft(next); setNotice('Servidor salvo. Entre com sua conta Nexora.'); if (changed) await logout(false); } catch (error: any) { Alert.alert('Endereço inválido', error.message); } }
  async function login(provider?: 'google') { if (!discovery || busy) return; setBusy(true); try { const authRequest = new AuthSession.AuthRequest({ clientId: 'central-monitor', redirectUri, responseType: AuthSession.ResponseType.Code, scopes: ['openid', 'profile', 'email'], usePKCE: true, extraParams: provider ? { kc_idp_hint: provider } : undefined }); authRequestRef.current = authRequest; const authorizationUrl = await authRequest.makeAuthUrlAsync(discovery); await SecureStore.setItemAsync(STORE.pkce, JSON.stringify({ verifier: authRequest.codeVerifier || '', state: authRequest.state || '' })); await Linking.openURL(authorizationUrl); setBusy(false); } catch (error: any) { authRequestRef.current = null; await SecureStore.deleteItemAsync(STORE.pkce); setBusy(false); Alert.alert('Acesso', error?.message || 'Não foi possível abrir a tela de usuário e senha.'); } }
  async function completeLogin(url: string) { if (!url.startsWith(redirectUri) || !discovery || exchangingRef.current) return; const saved = await SecureStore.getItemAsync(STORE.pkce); if (!saved) return; exchangingRef.current = true; setBusy(true); try { const pkce = JSON.parse(saved) as { verifier: string; state: string }; const query = url.split('?')[1] || ''; const params = new URLSearchParams(query); const returnedError = params.get('error_description') || params.get('error'); const code = params.get('code'); if (returnedError) throw new Error(decodeURIComponent(returnedError)); if (!code) throw new Error('O servidor não devolveu o código de acesso.'); if (pkce.state && params.get('state') !== pkce.state) throw new Error('Resposta de acesso inválida. Tente entrar novamente.'); const exchanged = await AuthSession.exchangeCodeAsync({ clientId: 'central-monitor', code, redirectUri, extraParams: { code_verifier: pkce.verifier } }, discovery); accessTokenRef.current = exchanged.accessToken; refreshTokenRef.current = exchanged.refreshToken || ''; setToken(exchanged.accessToken); setRefreshToken(exchanged.refreshToken || ''); await SecureStore.setItemAsync(STORE.access, exchanged.accessToken); if (exchanged.refreshToken) await SecureStore.setItemAsync(STORE.refresh, exchanged.refreshToken); else await SecureStore.deleteItemAsync(STORE.refresh); } catch (error: any) { Alert.alert('Acesso', error?.message || 'Não foi possível concluir o acesso.'); } finally { authRequestRef.current = null; exchangingRef.current = false; await SecureStore.deleteItemAsync(STORE.pkce); setBusy(false); } }
  async function logout(clearServer = false) { if (accessTokenRef.current) await unregisterPush(request); const toRevoke = refreshTokenRef.current; if (toRevoke && discovery) { try { await AuthSession.revokeAsync({ clientId: 'central-monitor', token: toRevoke, tokenTypeHint: AuthSession.TokenTypeHint.RefreshToken }, discovery); } catch {} } await SecureStore.deleteItemAsync(STORE.pkce); accessTokenRef.current = ''; refreshTokenRef.current = ''; automaticLoginStartedRef.current = false; await Promise.all([SecureStore.deleteItemAsync(STORE.access), SecureStore.deleteItemAsync(STORE.refresh)]); if (clearServer) await SecureStore.deleteItemAsync(STORE.server); setToken(''); setRefreshToken(''); setMe(null); setEntered(false); setTenantId(''); setTickets([]); setSelected(null); setTab(clearServer ? 'settings' : 'home'); if (clearServer) { setServer(''); setServerDraft(''); } }
  async function rememberTenant(id: string) { if (me) await SecureStore.setItemAsync(tenantKey(me.id), id).catch(() => {}); }
  function chooseTenant(id: string) { setSelected(null); setTenantId(id); rememberTenant(id); }
  function openNotifications() { if (tab !== 'notifications') setReturnTab(tab); setSelected(null); setTab('notifications'); }
  async function openNotification(item: NotificationItem) {
    try {
      await request(`/api/v1/notificacoes/${item.id}/abrir`, { method: 'POST' }, item.tenant_id);
      notifications.reload(); notifCount.refresh();
      if (item.action_type === 'INCIDENT' && item.action_ref) await openTicket(item.action_ref, item.tenant_id);
      else Alert.alert('Notificação', 'Este tipo de aviso ainda não abre no aplicativo. Consulte o painel Nexora.');
    } catch (error: any) { Alert.alert('Notificação', error.message); }
  }
  async function loadIdentity() { setBusy(true); try { const user: User = await request('/api/v1/auth/me', {}, '', true); setMe(user); const saved = await SecureStore.getItemAsync(tenantKey(user.id)).catch(() => null); const savedValid = validSavedTenant(saved, user.tenants); if (savedValid) { setTenantId(savedValid); setEntered(true); } else { if (user.tenants.length) setTenantId((current) => current || user.tenants[0].id); if (user.tenants.length === 1) setEntered(true); } } catch (error: any) { await logout(false); Alert.alert('Sessão', error.message); } finally { setBusy(false); } }
  async function loadPerCompany() {
    const results = await Promise.allSettled(me!.tenants.map((company) => request('/api/v1/tickets?page=1&page_size=100', {}, company.id).then((data) => (data.items || []).map((item: Ticket) => ({ ...item, tenant_id: item.tenant_id || company.id, tenant_name: company.name })))));
    const merged: Ticket[] = []; const failed: string[] = [];
    results.forEach((result, index) => { if (result.status === 'fulfilled') merged.push(...(result.value as Ticket[])); else failed.push(me!.tenants[index].name); });
    merged.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    setTickets(merged); setNextCursor(null); setServerTotal(merged.length); setFailedCompanies(failed);
  }
  async function loadSummary() {
    try {
      const data = tenantId === ALL_TENANTS ? (consolidatedRef.current ? await request('/api/v1/tickets/consolidated/summary', {}, '') : null) : await request('/api/v1/tickets/summary');
      const totals = data?.totals || data;
      setSummary(totals ? { open: Number(totals.open ?? 0), critical: Number(totals.critical ?? 0), overdue: Number(totals.overdue ?? 0) } : null);
    } catch { setSummary(null); }
  }
  async function loadTickets() {
    if (!tenantId || !me) return;
    setBusy(true);
    try {
      if (tenantId !== ALL_TENANTS) {
        const data = await request('/api/v1/tickets?page=1&page_size=100');
        setTickets(data.items || []); setNextCursor(null); setServerTotal(data.total ?? null); setFailedCompanies([]);
      } else if (consolidatedRef.current) {
        try {
          const data = await request(`/api/v1/tickets/consolidated?page_size=${CONSOLIDATED_PAGE}`, {}, '');
          setTickets(data.items || []); setNextCursor(data.next_cursor || null); setServerTotal(data.total ?? null);
          setFailedCompanies((data.tenants_failed || []).map((item: any) => item.tenant_name));
        } catch (error: any) {
          if (![404, 405, 422].includes(error?.status)) throw error;
          consolidatedRef.current = false; // o servidor ainda nao publicou o endpoint consolidado
          await loadPerCompany();
        }
      } else { await loadPerCompany(); }
      await loadSummary();
    } catch (error: any) { Alert.alert('Chamados', error.message); } finally { setBusy(false); }
  }
  async function loadMore() {
    if (!nextCursor || busy || tenantId !== ALL_TENANTS) return;
    setBusy(true);
    try {
      const data = await request(`/api/v1/tickets/consolidated?page_size=${CONSOLIDATED_PAGE}&cursor=${encodeURIComponent(nextCursor)}`, {}, '');
      setTickets((current) => { const seen = new Set(current.map((item) => item.id)); return [...current, ...(data.items || []).filter((item: Ticket) => !seen.has(item.id))]; });
      setNextCursor(data.next_cursor || null);
      if (data.tenants_failed?.length) setFailedCompanies((current) => Array.from(new Set([...current, ...data.tenants_failed.map((item: any) => item.tenant_name)])));
    } catch (error: any) {
      if (error?.status === 422) { setNextCursor(null); setBusy(false); await loadTickets(); return; }
      Alert.alert('Chamados', error.message);
    } finally { setBusy(false); }
  }
  async function openTicket(id: string, ticketTenant?: string) { setBusy(true); try { setSelected(await request(`/api/v1/tickets/${id}`, {}, ticketTenant || tenantId)); } catch (error: any) { Alert.alert('Chamado', error.message); } finally { setBusy(false); } }
  async function checkServer() {
    if (!server) return;
    setServerStatus({ state: 'checking' });
    const step = await timed('Servidor', async () => { const r = await probe(`${server}/api/v1/health/live`); if (!r.ok) throw new Error(`HTTP ${r.status}`); return 'ok'; });
    setServerStatus({ state: step.ok ? 'ok' : 'fail', at: new Date() });
  }
  async function testConnection() {
    if (testing) return;
    setTesting(true); setTestResults([]);
    const steps: TestStep[] = [];
    const push = (step: TestStep) => { steps.push(step); setTestResults([...steps]); return step; };
    const expectOk = (response: Response, what: string) => { if (response.status >= 300 && response.status < 400) throw new Error(`${what} redirecionou (HTTP ${response.status}). Confira o endereço do servidor.`); if (!response.ok) throw new Error(`${what} respondeu HTTP ${response.status}.`); };
    try {
      const reachable = push(await timed('Servidor', async () => { const r = await probe(`${server}/api/v1/health/live`); expectOk(r, 'Servidor'); return 'Servidor acessível.'; }));
      if (reachable.ok) {
        push(await timed('Login (Keycloak)', async () => { const r = await probe(`${server}/auth/realms/central-monitor/.well-known/openid-configuration`); expectOk(r, 'Login'); return 'Serviço de login disponível.'; }));
        if (token) {
          const session = push(await timed('Sessão', async () => { const user = await request('/api/v1/auth/me', {}, '', true); return `Autenticado como ${user.display_name || user.email || 'usuário'} (${user.tenants?.length || 0} empresa(s)).`; }));
          if (session.ok && tenantId) push(await timed('Chamados', async () => { const data = await request('/api/v1/tickets?page=1&page_size=1', {}, consolidated ? me?.tenants[0]?.id || '' : tenantId); return `Lista disponível (${data.total ?? 0} chamado(s)).`; }));
          if (session.ok && (me?.tenants.length || 0) > 1) push(await timed('Chamados consolidados', async () => { const data = await request('/api/v1/tickets/consolidated?page_size=1', {}, ''); return `Disponível: ${data.total ?? 0} chamado(s) em ${data.totals_by_tenant?.length ?? '?'} empresa(s)${data.partial ? ' (parcial)' : ''}.`; }));
        }
      }
    } finally { setTesting(false); }
  }
  async function mutate(path: string, body?: object, method = 'POST', writeTenant?: string) { if (mutatingRef.current) return false; mutatingRef.current = true; setBusy(true); try { await request(path, { method, body: body ? JSON.stringify(body) : undefined }, writeTenant || tenantId); setNotice('Operação registrada e aguardando confirmação da integração.'); if (selected) await openTicket(selected.ticket.id, writeTenant); await loadTickets(); return true; } catch (error: any) { Alert.alert('Operação não concluída', error.message); return false; } finally { mutatingRef.current = false; setBusy(false); } }

  if (booting) return <SafeAreaView style={s.center}><ActivityIndicator size="large" color={C.blue} /><Text style={s.muted}>Preparando Nexora…</Text></SafeAreaView>;
  if (!server) return <ServerSetup value={serverDraft} setValue={setServerDraft} save={saveServer} />;
  if (!token || !me) return <Login login={login} busy={busy || !loginActivityReady} />;
  if (!entered) return <Welcome name={me.display_name || me.email || 'Usuário'} server={server} status={serverStatus} tenants={me.tenants} tenantId={tenantId} onSelect={setTenantId} onEnter={() => { rememberTenant(tenantId); setEntered(true); }} onLogout={() => logout(false)} onRecheck={checkServer} version={`Nexora Mobile 1.0.14 · ${Platform.OS === 'android' ? 'Android' : Platform.OS}`} />;
  const content = selected ? <TicketScreen detail={selected} canOperate={canOperateIn(selected.ticket.tenant_id || tenantId)} back={() => setSelected(null)} mutate={(path: string, body?: object, method?: string) => mutate(path, body, method, selected.ticket.tenant_id || tenantId)} /> : tab === 'home' ? <Home tickets={tickets} tenant={tenant} open={openTicket} summary={summary} failed={failedCompanies} refresh={loadTickets} /> : tab === 'tickets' ? <Tickets tickets={tickets} busy={busy} refresh={loadTickets} open={openTicket} nextCursor={nextCursor} loadMore={loadMore} total={serverTotal} failed={failedCompanies} onNew={me.tenants.some((item) => canOperateIn(item.id) || item.role === 'client') ? () => setTab('new') : undefined} /> : tab === 'alerts' ? <AlertsScreen state={alerts} contextLabel={consolidated ? 'Todas autorizadas' : tenant?.name || 'Empresa'} consolidated={consolidated} openTicket={openTicket} /> : tab === 'notifications' ? <NotificationsScreen state={notifications} consolidated={consolidated} onOpen={openNotification} /> : tab === 'monitoring' ? <View style={{ flex: 1 }}><Pressable accessibilityRole="button" accessibilityLabel="Abrir o painel NOC" onPress={() => setTab('noc')} style={h.nocLink}><Icon name="noc" size={22} color={C.blue} /><Text style={h.nocLinkText}>Abrir NOC</Text></Pressable><MonitoringHub section={monitorSection} setSection={setMonitorSection} dashboard={dashboard} alerts={alerts} environments={environments} services={services} serviceAsset={serviceAsset} setServiceAsset={setServiceAsset} contextLabel={consolidated ? 'Todas autorizadas' : tenant?.name || 'Empresa'} consolidated={consolidated} openTicket={openTicket} /></View> : tab === 'noc' ? <NocScreen noc={noc} consolidated={consolidated} contextLabel={consolidated ? 'Todas autorizadas' : tenant?.name || 'Empresa'} /> : tab === 'new' ? <NewTicket tenants={me.tenants.filter((item) => canOperateIn(item.id) || item.role === 'client')} consolidated={consolidated} create={async (data: object, company?: string) => { const ok = await mutate('/api/v1/tickets', data, 'POST', company || tenantId); if (ok) setTab('tickets'); }} /> : <Settings server={serverDraft} me={me} tenantId={tenantId} setTenantId={chooseTenant} openNotifications={openNotifications} unread={notifCount.count} logout={() => logout(false)} reset={() => logout(false)} testConnection={testConnection} testing={testing} testResults={testResults} />;
  const subScreen = !selected && (tab === 'new' || tab === 'noc' || tab === 'notifications'); const goBack = () => setTab(tab === 'notifications' ? returnTab : tab === 'new' ? 'tickets' : 'monitoring'); const barTab: Tab = tab === 'new' ? 'tickets' : tab === 'noc' ? 'monitoring' : tab === 'notifications' ? returnTab : tab; const unreadText = unreadBadgeText(notifCount.count); return <SafeAreaView style={s.app}><StatusBar style="light" /><View style={h.header}>{subScreen ? <Pressable accessibilityRole="button" accessibilityLabel="Voltar" onPress={goBack} style={h.iconBtn}><View style={{ transform: [{ rotate: '180deg' }] }}><Icon name="seta" size={24} color="#FFFFFF" /></View></Pressable> : null}<View style={h.titleBox}><Text accessibilityRole="header" numberOfLines={1} style={h.title}>{selected ? 'Chamado' : TAB_TITLE[tab]}</Text>{tab === 'home' && !selected ? <Text numberOfLines={1} style={h.legend}>{tenant?.name || 'Empresa'}</Text> : null}</View>{busy && <ActivityIndicator color="white" />}<Pressable accessibilityRole="button" accessibilityLabel={notifCount.count ? `Notificações, ${notifCount.count} não lida(s)` : 'Notificações'} onPress={openNotifications} style={h.iconBtn}><Icon name="sino" size={26} color="#FFFFFF" />{notifCount.count > 0 ? <View style={h.bellBadge}><Text style={h.bellBadgeText}>{unreadText}</Text></View> : null}</Pressable></View>{notice ? <Pressable onPress={() => setNotice('')} style={s.notice}><Text style={s.noticeText}>{notice}</Text></Pressable> : null}<View style={s.body}>{content}</View>{!selected && <TabBar active={barTab as any} onChange={setTab} monitoringBadge={badgeFrom(dashboard.data)} />}</SafeAreaView>;
}

function ServerSetup({ value, setValue, save }: any) { return <SafeAreaView style={s.app}><StatusBar style="light" /><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={s.setup}><Text style={s.logo}>Nexora</Text><Text style={s.slogan}>Monitore. Antecipe. Resolva.</Text><View style={s.setupCard}><Text style={s.h1}>Conectar ao servidor</Text><Text style={s.muted}>Informe o endereço HTTPS do Nexora na Oracle. Para desenvolvimento, também é aceito o endereço IP local.</Text><Field label="Endereço do servidor" value={value} onChangeText={setValue} placeholder="https://nexora.suaempresa.com.br" /><Button title="Salvar e continuar" onPress={save} /></View></KeyboardAvoidingView></SafeAreaView>; }
function Login({ login, busy }: any) {
  return <SafeAreaView style={s.app}><StatusBar style="light" /><ImageBackground source={require('./assets/bg-login.png')} resizeMode="cover" style={h2.bg}><View style={h2.top}><Image source={require('./assets/nexora-logo-completo-provisorio.png')} resizeMode="contain" accessibilityLabel="Nexora, operação inteligente em escala" style={h2.logo} /></View><View style={h2.panel}><Text accessibilityRole="header" style={h2.title}>Entrar</Text><Text style={h2.help}>Use sua conta Nexora. O acesso é feito no navegador seguro do aparelho, com verificação em duas etapas quando exigida.</Text><Button title={busy ? 'Abrindo acesso…' : 'Entrar'} disabled={busy} onPress={() => login()} /></View></ImageBackground></SafeAreaView>;
}
function Home({ tickets, tenant, open, summary, failed, refresh }: any) { const active = tickets.filter((t: Ticket) => !['resolved', 'closed', 'cancelled', 'duplicate'].includes(t.status)); const open_ = summary?.open ?? active.length; const critical = summary?.critical ?? active.filter((t: Ticket) => t.priority === 'critical').length; const overdue = summary?.overdue ?? active.filter((t: Ticket) => t.resolution_due_at && new Date(t.resolution_due_at).getTime() < Date.now()).length; return <ScrollView contentContainerStyle={s.page}><Text style={s.eyebrow}>VISÃO OPERACIONAL</Text><Text style={s.h1}>{tenant?.name}</Text><Incomplete names={failed} retry={refresh} /><View style={s.metrics}><Metric value={open_} label="Em aberto" /><Metric value={critical} label="Críticos" tone="danger" /><Metric value={overdue} label="Vencidos" /></View><Text style={s.h2}>Atualizados recentemente</Text>{tickets.slice(0, 5).map((item: Ticket) => <TicketCard key={item.id} item={item} open={open} />)}</ScrollView>; }
function Incomplete({ names, retry }: { names: string[]; retry: () => void }) { if (!names?.length) return null; return <View style={s.warnBox}><Text style={s.warnText}>Dados incompletos: não foi possível carregar {names.join(', ')}.</Text><Button title="Tentar novamente" kind="secondary" onPress={retry} /></View>; }
function Metric({ value, label, tone }: any) { return <View style={s.metric}><Text style={[s.metricValue, tone === 'danger' && { color: C.danger }]}>{value}</Text><Text style={s.metricLabel}>{label}</Text></View>; }
function Tickets({ tickets, busy, refresh, open, nextCursor, loadMore, total, failed, onNew }: any) { const shown = tickets.length; const all = total ?? shown; return <FlatList data={tickets} keyExtractor={(item) => item.id} contentContainerStyle={s.page} refreshControl={<RefreshControl refreshing={busy} onRefresh={refresh} />} ListHeaderComponent={<><Text style={s.eyebrow}>OPERAÇÃO</Text><Text style={s.h1}>Chamados</Text><Text style={s.muted}>{all} registro(s) autorizados{all > shown ? ` · mostrando ${shown}` : ''}</Text>{onNew ? <Button title="Novo chamado" onPress={onNew} /> : null}<Incomplete names={failed} retry={refresh} /></>} ListEmptyComponent={<Text style={s.empty}>Nenhum chamado disponível.</Text>} ListFooterComponent={nextCursor ? <Button title="Carregar mais" kind="secondary" disabled={busy} onPress={loadMore} /> : null} renderItem={({ item }) => <TicketCard item={item} open={open} />} />; }
function TicketCard({ item, open }: { item: Ticket; open: (id: string, tenantId?: string) => void }) { const tone = item.priority === 'critical' ? 'danger' : item.priority === 'high' ? 'warning' : 'neutral'; return <Pressable onPress={() => open(item.id, item.tenant_id)} style={s.ticket}><View style={s.ticketTop}><Text numberOfLines={2} style={s.ticketTitle}>{item.title}</Text><Badge text={priorityLabel[item.priority] || item.priority} tone={tone} /></View>{item.tenant_name ? <Text style={s.company}>{item.tenant_name}</Text> : null}<Text style={s.muted}>#{item.glpi_ticket_id || item.id.slice(0, 8)} · {statusLabel[item.status] || item.status}</Text>{item.sync_status === 'pending' && <Text style={s.pending}>Aguardando confirmação do GLPI</Text>}</Pressable>; }
function TicketScreen({ detail, canOperate, back, mutate }: any) { const { ticket, entries, tasks } = detail; const [comment, setComment] = useState(''); const [solution, setSolution] = useState(''); const closed = ['resolved', 'closed', 'cancelled', 'duplicate'].includes(ticket.status); return <ScrollView contentContainerStyle={s.page}><Button title="← Voltar" kind="secondary" onPress={back} /><Text style={s.eyebrow}>CHAMADO #{ticket.glpi_ticket_id || ticket.id.slice(0, 8)}</Text><Text style={s.h1}>{ticket.title}</Text><View style={s.badgeRow}><Badge text={priorityLabel[ticket.priority] || ticket.priority} tone={ticket.priority === 'critical' ? 'danger' : 'neutral'} /><Badge text={statusLabel[ticket.status] || ticket.status} tone={closed ? 'success' : 'warning'} /></View>{ticket.sync_status === 'pending' && <View style={s.pendingBox}><Text style={s.pending}>Alteração local pendente de confirmação no GLPI.</Text></View>}<Text style={s.description}>{ticket.description || 'Sem descrição.'}</Text>{canOperate && !closed && <><Button title="Assumir atendimento" onPress={() => mutate(`/api/v1/tickets/${ticket.id}/claim`)} /><Field label="Interação" value={comment} onChangeText={setComment} placeholder="Descreva o atendimento" multiline /><Button title="Registrar interação pública" kind="secondary" onPress={async () => { if (comment.trim() && await mutate(`/api/v1/tickets/${ticket.id}/entries`, { body: comment.trim(), visibility: 'public' })) setComment(''); }} />{!tasks.some((task: any) => ['pending', 'in_progress'].includes(task.status)) && <><Field label="Solução" value={solution} onChangeText={setSolution} placeholder="Descreva a solução aplicada" multiline /><Button title="Finalizar chamado" onPress={async () => { if (!solution.trim()) return Alert.alert('Solução obrigatória'); const made = await mutate(`/api/v1/tickets/${ticket.id}/solutions`, { solution_type: 'permanent', description: solution.trim() }); if (made) await mutate(`/api/v1/tickets/${ticket.id}/transitions`, { target_status: 'resolved', reason: 'Finalizado pelo aplicativo Nexora' }); }} /></>}</>}<Text style={s.h2}>Interações</Text>{entries.map((entry: any) => <View key={entry.id} style={s.entry}><Text style={s.entryType}>{entry.is_internal ? 'Nota privada' : 'Interação pública'}</Text><Text style={s.description}>{entry.body}</Text></View>)}</ScrollView>; }
function NewTicket({ create, tenants, consolidated }: any) { const [company, setCompany] = useState(''); const [title, setTitle] = useState(''); const [description, setDescription] = useState(''); const [priority, setPriority] = useState('medium'); return <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled"><Text style={s.eyebrow}>NOVO REGISTRO</Text><Text style={s.h1}>Abrir chamado</Text><Field label="Título" value={title} onChangeText={setTitle} placeholder="Resumo do incidente" /><Field label="Descrição" value={description} onChangeText={setDescription} placeholder="Informe impacto, sintomas e contexto" multiline />{consolidated && <><Text style={s.label}>Empresa</Text><View style={s.choiceRow}>{tenants.map((item: Tenant) => <Pressable key={item.id} onPress={() => setCompany(item.id)} style={[s.choice, company === item.id && s.choiceOn]}><Text style={[s.choiceText, company === item.id && s.choiceTextOn]}>{item.name}</Text></Pressable>)}</View></>}<Text style={s.label}>Prioridade</Text><View style={s.choiceRow}>{['low', 'medium', 'high', 'critical'].map((item) => <Pressable key={item} onPress={() => setPriority(item)} style={[s.choice, priority === item && s.choiceOn]}><Text style={[s.choiceText, priority === item && s.choiceTextOn]}>{priorityLabel[item]}</Text></Pressable>)}</View><Button title="Criar chamado" onPress={() => { if (title.trim().length < 3 || description.trim().length < 3) return Alert.alert('Preencha título e descrição.'); if (consolidated && !company) return Alert.alert('Selecione a empresa do chamado.'); create({ title: title.trim(), description: description.trim(), priority, category: 'general', ticket_type: 'incident' }, consolidated ? company : undefined); }} /></ScrollView>; }
function Settings({ server, me, tenantId, setTenantId, openNotifications, unread, logout, reset, testConnection, testing, testResults }: any) {
  const current = tenantId === ALL_TENANTS ? { name: 'Todas as empresas', role: `${me.tenants.length} empresas · leitura consolidada` } : me.tenants.find((item: Tenant) => item.id === tenantId);
  const choose = (id: string, name: string) => { if (id === tenantId) return; Alert.alert('Trocar empresa?', `Mudar para ${name}? Dados, contadores e notificações serão recarregados para essa empresa.`, [{ text: 'Cancelar', style: 'cancel' }, { text: 'Trocar', onPress: () => setTenantId(id) }]); };
  return <ScrollView contentContainerStyle={s.page}><View style={s.card}><Text style={s.eyebrow}>EMPRESA ATIVA</Text><Text style={s.h2}>{current?.name || 'Empresa'}</Text><Text style={s.muted}>{current?.role}</Text></View><View style={s.card}><Text style={s.h2}>Trocar empresa</Text>{me.tenants.length > 1 && <Pressable accessibilityRole="button" onPress={() => choose(ALL_TENANTS, 'Todas as empresas')} style={[s.tenant, tenantId === ALL_TENANTS && s.tenantOn]}><Text style={s.ticketTitle}>Todas as empresas{tenantId === ALL_TENANTS ? ' · atual' : ''}</Text><Text style={s.muted}>{me.tenants.length} empresas · leitura consolidada</Text></Pressable>}{me.tenants.map((item: Tenant) => <Pressable accessibilityRole="button" key={item.id} onPress={() => choose(item.id, item.name)} style={[s.tenant, tenantId === item.id && s.tenantOn]}><Text style={s.ticketTitle}>{item.name}{tenantId === item.id ? ' · atual' : ''}</Text><Text style={s.muted}>{item.role}</Text></Pressable>)}</View><Button title={unread ? `Notificações (${unread} não lidas)` : 'Notificações'} kind="secondary" onPress={openNotifications} /><View style={s.card}><Text style={s.h2}>Servidor Oracle</Text><Text style={s.description}>{server}</Text><Badge text="Conexão fixa" tone="success" /><Button title={testing ? 'Testando…' : 'Testar conexão'} kind="secondary" disabled={testing} onPress={testConnection} />{testResults.map((step: TestStep) => <View key={step.label} style={[s.testRow, step.ok ? s.testOk : s.testFail]}><Text style={s.testTitle}>{step.ok ? '✓' : '✗'} {step.label} · {step.ms} ms</Text><Text style={s.testMessage}>{step.message}</Text></View>)}</View><Button title="Sair da conta" kind="secondary" onPress={logout} /><Button title="Limpar sessão" kind="danger" onPress={() => Alert.alert('Limpar sessão?', 'A conta será removida deste aparelho. O endereço Oracle permanecerá configurado.', [{ text: 'Cancelar' }, { text: 'Limpar', style: 'destructive', onPress: reset }])} /><Text style={s.version}>Nexora Mobile 1.0.14 · {Platform.OS}</Text></ScrollView>;
}

const s = StyleSheet.create({
  app: { flex: 1, backgroundColor: C.bg }, center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, backgroundColor: C.bg }, body: { flex: 1 }, header: { minHeight: 96, backgroundColor: C.navy, paddingHorizontal: 20, paddingTop: (Platform.OS === 'android' ? RNStatusBar.currentHeight || 24 : 0) + 18, paddingBottom: 18, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, brand: { color: 'white', fontSize: 23, fontWeight: '800' }, headerSub: { color: '#AFC3DD', fontSize: 12, marginTop: 2 },
  setup: { flex: 1, backgroundColor: C.navy, justifyContent: 'center', padding: 24 }, logo: { color: 'white', fontSize: 42, fontWeight: '900' }, slogan: { color: '#AFC3DD', fontSize: 16, marginTop: 4, marginBottom: 28 }, setupCard: { backgroundColor: C.card, padding: 20, borderRadius: 18, gap: 14 }, page: { padding: 18, paddingBottom: 32, gap: 12 }, h1: { color: C.text, fontSize: 26, lineHeight: 32, fontWeight: '800' }, h2: { color: C.text, fontSize: 18, fontWeight: '700', marginTop: 8 }, eyebrow: { color: C.blueText, fontSize: 12, fontWeight: '800', letterSpacing: 1 }, muted: { color: C.muted, lineHeight: 20 }, description: { color: C.text, lineHeight: 22 }, empty: { color: C.muted, textAlign: 'center', paddingVertical: 40 },
  field: { gap: 6 }, label: { color: C.text, fontSize: 13, fontWeight: '700' }, input: { minHeight: 48, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 13, color: C.text, backgroundColor: C.card, fontSize: 16 }, textarea: { minHeight: 110, textAlignVertical: 'top', paddingTop: 12 }, button: { minHeight: 48, paddingHorizontal: 16, borderRadius: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: C.blue }, button_primary: { backgroundColor: C.blue }, button_secondary: { backgroundColor: C.card, borderWidth: 1, borderColor: C.border }, button_danger: { backgroundColor: C.danger }, buttonText: { color: 'white', fontWeight: '800' }, buttonSecondaryText: { color: C.text }, buttonDim: { opacity: .55 },
  metrics: { flexDirection: 'row', gap: 8 }, metric: { flex: 1, backgroundColor: C.card, borderRadius: 14, padding: 12, borderWidth: 1, borderColor: C.border }, metricValue: { color: C.text, fontSize: 27, fontWeight: '900' }, metricLabel: { color: C.muted, fontSize: 11 }, ticket: { backgroundColor: C.card, padding: 15, borderRadius: 14, borderWidth: 1, borderColor: C.border, gap: 7 }, ticketTop: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' }, ticketTitle: { flex: 1, color: C.text, fontSize: 16, fontWeight: '700' }, pending: { color: C.warning, fontSize: 12, fontWeight: '700' }, pendingBox: { backgroundColor: '#3A2A12', padding: 12, borderRadius: 10 },
  badgeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, badge: { backgroundColor: '#16335C', borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 }, badge_success: { backgroundColor: '#134E3A' }, badge_warning: { backgroundColor: '#4A3216' }, badge_danger: { backgroundColor: '#4A1D26' }, badge_neutral: { backgroundColor: '#16335C' }, badgeText: { color: C.text, fontSize: 11, fontWeight: '800' }, entry: { backgroundColor: C.card, borderRadius: 12, padding: 13, borderWidth: 1, borderColor: C.border }, entryType: { color: C.blueText, fontSize: 11, fontWeight: '800', marginBottom: 5 }, card: { backgroundColor: C.card, borderRadius: 14, padding: 14, gap: 12, borderWidth: 1, borderColor: C.border },
  choiceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, choice: { paddingHorizontal: 12, minHeight: 42, justifyContent: 'center', borderWidth: 1, borderColor: C.border, borderRadius: 10, backgroundColor: C.card }, choiceOn: { backgroundColor: C.blue, borderColor: C.blue }, choiceText: { color: C.text, fontWeight: '700' }, choiceTextOn: { color: 'white' }, tenant: { borderWidth: 1, borderColor: C.border, borderRadius: 11, padding: 12 }, tenantOn: { borderColor: C.blue, backgroundColor: '#12305C' }, tabs: { flexDirection: 'row', backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingBottom: Platform.OS === 'ios' ? 10 : 4 }, tab: { flex: 1, minHeight: 54, alignItems: 'center', justifyContent: 'center' }, tabText: { color: C.muted, fontWeight: '600' }, tabActive: { color: C.blueText, fontWeight: '800' }, notice: { backgroundColor: '#0F3326', paddingHorizontal: 18, paddingVertical: 10 }, noticeText: { color: C.success, fontWeight: '700' }, warnBox: { backgroundColor: '#3A2A12', borderColor: '#7C4A12', borderWidth: 1, borderRadius: 12, padding: 12, gap: 8 }, warnText: { color: '#FDBA74', fontSize: 13, lineHeight: 18 }, company: { color: C.blueText, fontSize: 12, fontWeight: '800' }, testRow: { borderRadius: 10, padding: 10, gap: 2, borderWidth: 1 }, testOk: { backgroundColor: '#0F3326', borderColor: '#1F6B4B' }, testFail: { backgroundColor: '#3B1620', borderColor: '#7F2A35' }, testTitle: { color: C.text, fontWeight: '800', fontSize: 13 }, testMessage: { color: C.muted, fontSize: 12, lineHeight: 17 }, version: { textAlign: 'center', color: C.muted, fontSize: 12, marginTop: 12 },
});

const h = StyleSheet.create({
  header: { backgroundColor: C.navy, paddingHorizontal: 12, paddingTop: (Platform.OS === 'android' ? RNStatusBar.currentHeight || 24 : 0) + 8, paddingBottom: 10, minHeight: 64, flexDirection: 'row', alignItems: 'center', gap: 8 },
  titleBox: { flex: 1, paddingLeft: 4 },
  title: { color: '#FFFFFF', fontSize: 22, fontWeight: '800' },
  legend: { color: C.onNavyMuted, fontSize: 12, marginTop: 1 },
  iconBtn: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  bellBadge: { position: 'absolute', top: 3, right: 1, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4, backgroundColor: C.danger, alignItems: 'center', justifyContent: 'center' },
  bellBadgeText: { color: '#FFFFFF', fontSize: 11, fontWeight: '800' },
  nocLink: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 44, marginHorizontal: 16, marginTop: 12, paddingHorizontal: 12, borderRadius: 10, borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  nocLinkText: { color: C.blueText, fontWeight: '800' },
});

const h2 = StyleSheet.create({
  bg: { flex: 1, justifyContent: 'space-between' },
  top: { alignItems: 'center', paddingTop: 72, paddingHorizontal: 32 },
  logo: { width: '100%', height: 120 },
  panel: { margin: 16, marginBottom: 32, padding: 20, gap: 12, borderRadius: 16, backgroundColor: 'rgba(11,31,59,0.88)', borderWidth: 1, borderColor: C.border },
  title: { color: C.text, fontSize: 24, fontWeight: '800' },
  help: { color: C.muted, fontSize: 14, lineHeight: 20 },
});
