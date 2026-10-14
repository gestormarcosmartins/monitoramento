import React, { useEffect, useMemo, useRef, useState } from 'react';
import './notifications.css';

const ROOT = '/notificacoes';
const SEVERITY = { P0: 'P0 · Crítica', P1: 'P1 · Alta', P2: 'P2 · Média', P3: 'P3 · Baixa' };
const STATES = {
  PENDING: 'Pendente', PROCESSING: 'Processando', SENT_TO_EXPO: 'Enviada ao Expo', PROVIDER_ACCEPTED: 'Aceita pelo provedor',
  RETRY_WAIT: 'Nova tentativa agendada', FAILED_FINAL: 'Falha definitiva', DEVICE_INVALID: 'Aparelho inválido', EXPIRED: 'Expirada',
};
const fmt = (v) => (v ? new Date(v).toLocaleString('pt-BR') : '—');
const route = () => {
  const parts = window.location.pathname.split('/').filter(Boolean);
  return { page: parts[1] || 'central', id: parts[2] || null };
};
const statusOf = (n) => (n.acknowledged_at ? 'Confirmada' : n.require_ack ? 'Aguardando confirmação' : n.opened_at ? 'Lida' : 'Não lida');

function Pagination({ total, page, size, setPage, setSize, label }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="service-pagination">
    <span>Mostrando {total ? (page - 1) * size + 1 : 0} a {Math.min(page * size, total)} de {total} registros</span>
    <div>
      <label>Registros por página <select value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(1); }}><option>10</option><option>25</option><option>50</option></select></label>
      <button type="button" aria-label={`Página anterior de ${label}`} disabled={page === 1} onClick={() => setPage(page - 1)}>‹</button>
      <b>{page}</b>
      <button type="button" aria-label={`Próxima página de ${label}`} disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button>
    </div>
  </div>;
}

function useList(load, deps) {
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const seq = useRef(0);
  const reload = () => {
    const id = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: '' }));
    load().then((data) => { if (id === seq.current) setState({ loading: false, error: '', data }); })
      .catch((e) => { if (id === seq.current) setState({ loading: false, error: e.message || 'Falha ao consultar', data: null }); });
  };
  useEffect(() => { reload(); return () => { seq.current++; }; }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return [state, reload];
}

function Central({ request, ui, navigate }) {
  const { ListPage, MetricGroup, FilterBar, DataGrid, Loading, Unavailable } = ui;
  const [state, reload] = useList(() => request('/api/v1/notificacoes?limit=200'), []);
  const [search, setSearch] = useState('');
  const [severity, setSeverity] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const items = state.data?.items || [];
  const filtered = useMemo(() => items.filter((n) =>
    (!severity || n.severity === severity) && (!status || statusOf(n) === status) &&
    `${n.title} ${n.body_safe} ${n.category}`.toLowerCase().includes(search.trim().toLowerCase())), [items, search, severity, status]);
  const rows = filtered.slice((page - 1) * size, page * size);
  const chips = [search && ['Busca', search, () => setSearch('')], severity && ['Severidade', SEVERITY[severity], () => setSeverity('')], status && ['Status', status, () => setStatus('')]].filter(Boolean);
  const totals = state.data?.totals;
  const metric = (value) => (state.loading ? '…' : value ?? '—');
  return <ListPage title="Central de notificações" eyebrow="NEXORA › NOTIFICAÇÕES"
    description="Alertas de infraestrutura enviados a você nesta Empresa, com estado de leitura e confirmação."
    metrics={<MetricGroup items={[
      { label: 'Total', value: metric(totals?.total) }, { label: 'Críticas (P0/P1)', value: metric(totals?.critical) },
      { label: 'Não lidas', value: metric(totals?.unread) }, { label: 'Aguardando confirmação', value: metric(totals?.awaiting_ack) }]} />}
    filters={<FilterBar>
      <label className="field"><span>Buscar</span><input type="search" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Título, categoria…" /></label>
      <label className="field"><span>Severidade</span><select value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }}><option value="">Todas</option>{Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="field"><span>Status</span><select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">Todos</option>{['Não lida', 'Lida', 'Aguardando confirmação', 'Confirmada'].map((v) => <option key={v}>{v}</option>)}</select></label>
      {chips.length > 0 && <div className="filter-chips">{chips.map(([name, value, clear]) => <button type="button" key={name} onClick={() => { clear(); setPage(1); }} aria-label={`Remover filtro ${name}: ${value}`}>{name}: {value} ×</button>)}
        <button type="button" onClick={() => { setSearch(''); setSeverity(''); setStatus(''); setPage(1); }}>Limpar</button></div>}
    </FilterBar>}>
    {state.loading && !state.data ? <Loading /> : state.error ? <Unavailable title="Não foi possível carregar as notificações." description={state.error} onRetry={reload} /> : <>
      <DataGrid stateKey="notifications-central" empty="Nenhuma notificação encontrada." rows={rows}
        onRow={(n) => navigate(`central/${n.id}`)}
        columns={[
          { key: 'title', label: 'Notificação', icon: 'bell', render: (n) => <span><strong>{n.title}</strong><br /><small>{n.body_safe}</small></span> },
          { key: 'severity', label: 'Severidade', render: (n) => SEVERITY[n.severity] || n.severity },
          { key: 'category', label: 'Categoria' },
          { key: 'created_at', label: 'Recebida em', render: (n) => fmt(n.created_at) },
          { key: 'status', label: 'Status', render: (n) => statusOf(n) },
        ]}
        actions={(n) => <button type="button" onClick={() => navigate(`central/${n.id}`)}>Visualizar</button>} />
      <Pagination total={filtered.length} page={page} size={size} setPage={setPage} setSize={setSize} label="notificações" />
    </>}
  </ListPage>;
}

function Detail({ id, request, ui, navigate, onOpenIncident }) {
  const { PageHeader, Loading, Unavailable } = ui;
  const [state, reload] = useList(() => request('/api/v1/notificacoes?limit=200'), [id]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);
  const item = state.data?.items.find((n) => String(n.id) === id);
  useEffect(() => { // abrir registra a visita e é a autorização real do destino
    if (!item || opened) return;
    setOpened(true);
    request(`/api/v1/notificacoes/${id}/abrir`, { method: 'POST' }).then(reload).catch((e) => setMessage(e.message));
  }, [item?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <Unavailable title="Não foi possível carregar a notificação." description={state.error} onRetry={reload} />;
  if (!item) return <Unavailable title="Notificação não encontrada." description="Ela pode ter expirado ou não pertencer a esta Empresa." onRetry={() => navigate('central')} />;
  const confirm = async () => {
    setBusy(true); setMessage('');
    try { await request(`/api/v1/notificacoes/${id}/confirmar`, { method: 'POST' }); reload(); }
    catch (e) { setMessage(e.message || 'Falha ao confirmar'); } finally { setBusy(false); }
  };
  return <section className="panel form-page">
    <div className="form-page-heading"><button type="button" className="back-button" onClick={() => navigate('central')}>← Voltar</button>
      <div><h2>{item.title}</h2><p>NEXORA › NOTIFICAÇÕES › Detalhe</p></div></div>
    <dl className="detail-list">
      <div><dt>Mensagem</dt><dd>{item.body_safe}</dd></div>
      <div><dt>Severidade</dt><dd>{SEVERITY[item.severity] || item.severity}</dd></div>
      <div><dt>Domínio / categoria</dt><dd>{item.domain} · {item.category}</dd></div>
      <div><dt>Recebida em</dt><dd>{fmt(item.created_at)}</dd></div>
      <div><dt>Lida em</dt><dd>{fmt(item.opened_at)}</dd></div>
      <div><dt>Confirmada em</dt><dd>{item.require_ack ? fmt(item.acknowledged_at) : 'Não exige confirmação'}</dd></div>
    </dl>
    {message && <p role="alert" className="form-error">{message}</p>}
    <div className="form-actions">
      {item.action_type === 'INCIDENT' && item.action_ref && <button type="button" className="secondary" onClick={() => onOpenIncident(item.action_ref)}>Ver incidente</button>}
      {item.require_ack && !item.acknowledged_at && <button type="button" className="primary" disabled={busy} onClick={confirm}>{busy ? 'Confirmando…' : 'Confirmar ciência'}</button>}
    </div>
  </section>;
}

function Devices({ request, ui }) {
  const { ListPage, MetricGroup, DataGrid, Loading, Unavailable } = ui;
  const [state, reload] = useList(() => request('/api/v1/notificacoes/dispositivos-push'), []);
  const [message, setMessage] = useState('');
  const rows = state.data || [];
  const revoke = async (device) => {
    if (!window.confirm('Revogar este aparelho? Ele deixará de receber notificações até entrar novamente no app.')) return;
    try { await request(`/api/v1/notificacoes/dispositivos-push/${device.id}`, { method: 'DELETE' }); reload(); }
    catch (e) { setMessage(e.message || 'Falha ao revogar'); }
  };
  return <ListPage title="Meus dispositivos" eyebrow="NEXORA › NOTIFICAÇÕES"
    description="Aparelhos vinculados à sua conta para receber push. O token completo nunca é exibido."
    metrics={<MetricGroup items={[{ label: 'Dispositivos', value: state.data ? rows.length : '…' }, { label: 'Ativos', value: state.data ? rows.filter((d) => d.enabled).length : '…' }]} />}>
    {message && <p role="alert" className="form-error">{message}</p>}
    {state.loading && !state.data ? <Loading /> : state.error ? <Unavailable title="Não foi possível carregar os dispositivos." description={state.error} onRetry={reload} /> :
      <DataGrid stateKey="notifications-devices" empty="Nenhum aparelho registrado. Entre no app Nexora para ativar." rows={rows}
        columns={[
          { key: 'platform', label: 'Aparelho', icon: 'bell', render: (d) => <span><strong>{d.platform === 'ios' ? 'iPhone / iPad' : 'Android'}</strong><br /><small>{d.token}</small></span> },
          { key: 'app_version', label: 'Versão do app' },
          { key: 'enabled', label: 'Estado', render: (d) => (d.enabled ? 'Ativo' : `Inativo${d.reason ? ` · ${d.reason}` : ''}`) },
          { key: 'last_seen_at', label: 'Última atividade', render: (d) => fmt(d.last_seen_at) },
        ]}
        actions={(d) => d.enabled ? <button type="button" onClick={() => revoke(d)}>Remover</button> : null} />}
  </ListPage>;
}

function Deliveries({ request, ui }) {
  const { ListPage, MetricGroup, FilterBar, DataGrid, Loading, Unavailable } = ui;
  const [stateFilter, setStateFilter] = useState('');
  const [state, reload] = useList(() => request(`/api/v1/notificacoes/entregas?limit=200${stateFilter ? `&state=${stateFilter}` : ''}`), [stateFilter]);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const data = state.data;
  const count = (name) => data?.summary.find((s) => s.state === name)?.total ?? 0;
  const total = data ? data.summary.reduce((a, s) => a + s.total, 0) : 0;
  const rows = (data?.items || []).slice((page - 1) * size, page * size);
  const accepted = count('PROVIDER_ACCEPTED') + count('SENT_TO_EXPO');
  const m = (v) => (data ? v : '…');
  return <ListPage title="Saúde das entregas" eyebrow="NEXORA › NOTIFICAÇÕES"
    description="Fila, receipts do Expo e falhas. Aceite do provedor não significa leitura pelo usuário."
    metrics={<MetricGroup items={[
      { label: 'Entregas', value: m(total) }, { label: 'Aceitas (Expo/provedor)', value: m(accepted) },
      { label: 'Na fila / retry', value: m(count('PENDING') + count('RETRY_WAIT')) }, { label: 'Falhas e inválidas', value: m(count('FAILED_FINAL') + count('DEVICE_INVALID')) },
      { label: 'Idade da fila (s)', value: m(data?.queue_age_seconds) }]} />}
    filters={<FilterBar><label className="field"><span>Estado</span>
      <select value={stateFilter} onChange={(e) => { setStateFilter(e.target.value); setPage(1); }}><option value="">Todos</option>{Object.entries(STATES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      {stateFilter && <div className="filter-chips"><button type="button" onClick={() => setStateFilter('')} aria-label="Remover filtro de estado">Estado: {STATES[stateFilter]} ×</button></div>}</FilterBar>}>
    {state.loading && !data ? <Loading /> : state.error ? <Unavailable title="Não foi possível carregar as entregas." description={state.error} onRetry={reload} /> : <>
      <DataGrid stateKey="notifications-deliveries" empty="Nenhuma entrega registrada." rows={rows}
        columns={[
          { key: 'created_at', label: 'Criada em', icon: 'bell', render: (d) => fmt(d.created_at) },
          { key: 'state', label: 'Estado', render: (d) => STATES[d.state] || d.state },
          { key: 'attempt_no', label: 'Tentativas' },
          { key: 'provider_error_code', label: 'Erro do provedor' },
          { key: 'receipt_checked_at', label: 'Receipt', render: (d) => fmt(d.receipt_checked_at) },
        ]} />
      <Pagination total={data.items.length} page={page} size={size} setPage={setPage} setSize={setSize} label="entregas" />
    </>}
  </ListPage>;
}

export default function Notifications({ request, ui, canAudit, onOpenIncident }) {
  const [current, setCurrent] = useState(route());
  const requestRef = useRef(request); requestRef.current = request;
  const stableRequest = (path, opts) => requestRef.current(path, opts);
  const navigate = (next) => {
    const target = `${ROOT}/${next}`;
    if (window.location.pathname !== target) window.history.pushState({}, '', target);
    setCurrent(route());
  };
  useEffect(() => { const pop = () => setCurrent(route()); window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop); }, []);
  const tabs = [['central', 'Central'], ['dispositivos', 'Meus dispositivos'], ...(canAudit ? [['entregas', 'Saúde das entregas']] : [])];
  const isDetail = current.page === 'central' && current.id;
  return <div className="nexora-notifications">
    {!isDetail && <div role="navigation" className="bm-tabs" aria-label="Navegação de Notificações">
      {tabs.map(([key, label]) => <button key={key} type="button" aria-current={current.page === key ? 'page' : undefined} className={current.page === key ? 'active' : ''} onClick={() => navigate(key)}>{label}</button>)}
    </div>}
    {isDetail ? <Detail id={current.id} request={stableRequest} ui={ui} navigate={navigate} onOpenIncident={onOpenIncident} />
      : current.page === 'dispositivos' ? <Devices request={stableRequest} ui={ui} />
      : current.page === 'entregas' && canAudit ? <Deliveries request={stableRequest} ui={ui} />
      : <Central request={stableRequest} ui={ui} navigate={navigate} />}
  </div>;
}
