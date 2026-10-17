import React, {useCallback, useEffect, useRef, useState} from 'react';
import {PAGE_SIZES, STATUS_ORDER, activeFilters, buildKpis, buildQuery, describeValue, formatDateTime, formatLimit,
  pageRange, sourceLine, statusMeta} from './business-overview-lib.js';

const NARROW = '(max-width: 720px)';
const storageKey = (tenantId) => `nexora:business-overview:${tenantId}`;
const DEFAULT_FILTERS = {q: '', status: null, page: 1, pageSize: 10};

function readFilters(tenantId) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey(tenantId)) || 'null');
    if (!saved) return DEFAULT_FILTERS;
    return {q: String(saved.q || ''), status: STATUS_ORDER.includes(saved.status) ? saved.status : null,
      page: Math.max(1, Number(saved.page) || 1), pageSize: PAGE_SIZES.includes(Number(saved.pageSize)) ? Number(saved.pageSize) : 10};
  } catch { return DEFAULT_FILTERS; }
}

function useNarrow() {
  const read = () => typeof window !== 'undefined' && Boolean(window.matchMedia) && window.matchMedia(NARROW).matches;
  const [narrow, setNarrow] = useState(read);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const query = window.matchMedia(NARROW), onChange = () => setNarrow(query.matches);
    query.addEventListener('change', onChange); onChange();
    return () => query.removeEventListener('change', onChange);
  }, []);
  return narrow;
}

const ICONS = {
  critical: <><circle cx="8" cy="8" r="6.5"/><path d="M8 4.5v4M8 11v.1"/></>,
  warning: <><path d="M8 2 14.5 13.5h-13z"/><path d="M8 6.5v3M8 11.6v.1"/></>,
  clock: <><circle cx="8" cy="8" r="6.5"/><path d="M8 4.5V8l2.5 1.5"/></>,
  nodata: <><circle cx="8" cy="8" r="6.5" strokeDasharray="2.4 2"/><path d="M5.5 8h5"/></>,
  norule: <><rect x="2" y="2" width="12" height="12" rx="2" strokeDasharray="2.4 2"/><path d="M5 11 11 5"/></>,
  ok: <><circle cx="8" cy="8" r="6.5"/><path d="m5 8.2 2.2 2.2L11 6"/></>,
};
const StatusIcon = ({name}) => <svg className="bm-ov-icon" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{ICONS[name] || ICONS.nodata}</svg>;
const StatusBadge = ({status}) => {
  const meta = statusMeta(status);
  return <span className={`bm-ov-status bm-ov-status--${meta.tone}`}><StatusIcon name={meta.icon}/><span>{meta.label}</span></span>;
};

const Limit = ({item, kind}) => {
  const value = kind === 'alerta' ? item.limiteAlerta : item.limiteCritico;
  if (value === null || value === undefined) return <span className="bm-ov-muted">{formatLimit(null)}</span>;
  const sign = item.direcao === 'MENOR_PIORA' ? '≤' : item.direcao === 'MAIOR_PIORA' ? '≥' : '';
  return <span className="bm-ov-num">{sign && <abbr title={sign === '≥' ? 'A partir de' : 'Até'}>{sign} </abbr>}{formatLimit(value, item.unidade)}</span>;
};

function CurrentValue({item}) {
  const view = describeValue(item);
  return <div className="bm-ov-value">
    <strong className="bm-ov-num">{view.text}</strong>
    {view.lastKnown && <small>{view.lastKnown.label}: <span className="bm-ov-num">{view.lastKnown.text}</span> em {view.lastKnown.at}</small>}
  </div>;
}

function IndicatorCard({item, canOpen, onOpen}) {
  const meta = statusMeta(item.statusPrincipal), view = describeValue(item);
  return <li className={`bm-ov-card bm-ov-card--${meta.tone}`}>
    <div className="bm-ov-card-head"><div><h3>{item.nome}</h3><code>{item.codigo}</code></div><StatusBadge status={item.statusPrincipal}/></div>
    {item.motivoQualidade && <p className="bm-ov-reason">{item.motivoQualidade}</p>}
    <dl>
      <div><dt>Valor atual</dt><dd><strong className="bm-ov-num">{view.text}</strong></dd></div>
      {view.lastKnown && <div><dt>{view.lastKnown.label}</dt><dd><span className="bm-ov-num">{view.lastKnown.text}</span> em {view.lastKnown.at}</dd></div>}
      <div><dt>Alerta</dt><dd><Limit item={item} kind="alerta"/></dd></div>
      <div><dt>Crítico</dt><dd><Limit item={item} kind="critico"/></dd></div>
      <div><dt>Última coleta</dt><dd>{formatDateTime(item.coletadoEm)}</dd></div>
      {item.fonte && <div><dt>Fonte</dt><dd>{item.fonte}</dd></div>}
    </dl>
    {canOpen(item) && <button type="button" className="bm-ov-open" aria-label={`Editar indicador ${item.nome}`} onClick={() => onOpen(item)}>Editar</button>}
  </li>;
}

function Skeleton({narrow}) {
  return <div className="bm-ov-skeleton" aria-hidden="true">{Array.from({length: narrow ? 3 : 5}, (_, n) => <div key={n} className="bm-ov-skeleton-row"><span/><span/><span/></div>)}</div>;
}

export default function BusinessOverview({tenantId, tenantName, request, access, ui, onOpen}) {
  const {ListPage, MetricGroup, FilterBar, DataGrid, Unavailable} = ui;
  const narrow = useNarrow();
  const [filters, setFilters] = useState(() => readFilters(tenantId));
  const [searchText, setSearchText] = useState(filters.q);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const requestRef = useRef(request); requestRef.current = request;
  const sequence = useRef(0);
  const canEdit = (access?.capabilities || []).includes('business.monitor.update');
  const canOpen = (item) => canEdit && (item.acoesPermitidas || []).includes('EDITAR');

  const load = useCallback(async (current, manual = false) => {
    const id = ++sequence.current; // respostas antigas (filtro ou Empresa anteriores) são descartadas
    setLoading(true); setError(''); setNotice('');
    try {
      const result = await requestRef.current(`/api/v1/business/overview?${buildQuery(current)}`);
      if (id !== sequence.current) return;
      setData(result);
      if (manual) setNotice(`Dados atualizados às ${formatDateTime(result.consultadoEm).slice(-5)}.`);
      if (result.pagina !== current.page) setFilters((f) => ({...f, page: result.pagina}));
    } catch (e) {
      if (id === sequence.current) setError(e.message || 'Falha ao consultar os indicadores de negócio.');
    } finally { if (id === sequence.current) setLoading(false); }
  }, []);

  useEffect(() => { load(filters); }, [filters.q, filters.status, filters.page, filters.pageSize, tenantId]);
  useEffect(() => { try { sessionStorage.setItem(storageKey(tenantId), JSON.stringify(filters)); } catch { /* sem armazenamento */ } }, [filters, tenantId]);
  useEffect(() => { // a busca é aplicada ~300 ms após a digitação; Enter aplica na hora
    if (searchText.trim() === filters.q.trim()) return undefined;
    const timer = setTimeout(() => setFilters((f) => ({...f, q: searchText.trim(), page: 1})), 300);
    return () => clearTimeout(timer);
  }, [searchText, filters.q]);

  const apply = (patch) => setFilters((f) => ({...f, ...patch, page: patch.page ?? 1}));
  const clearAll = () => { setSearchText(''); setFilters((f) => ({...f, q: '', status: null, page: 1})); };
  const removeChip = (key) => { if (key === 'q') { setSearchText(''); apply({q: ''}); } else apply({status: null}); };
  const chips = activeFilters(filters);
  const totais = data?.totais;
  const kpis = buildKpis(totais, filters.status).map((kpi) => ({
    label: kpi.label, value: kpi.value, selected: kpi.key === filters.status,
    onSelect: () => apply({status: kpi.key === filters.status ? null : kpi.key}),
  }));
  const range = pageRange(data?.pagina || 1, data?.tamanhoPagina || filters.pageSize, data?.totalFiltrado || 0);
  const items = data?.itens || [];
  const firstLoad = loading && !data;
  const fatal = error && !data;
  const companyName = tenantName || 'Empresa selecionada';

  const refresh = <button type="button" className="secondary" onClick={() => load(filters, true)} disabled={loading} aria-busy={loading}>{loading && data ? 'Atualizando…' : 'Atualizar'}</button>;
  const context = <div className="bm-ov-context">
    <p>Última avaliação: <b>{formatDateTime(data?.ultimaAvaliacaoEm)}</b> · Consulta realizada em: <b>{formatDateTime(data?.consultadoEm)}</b></p>
    <p className="bm-ov-muted">Os dados refletem as últimas coletas registradas; não são em tempo real.{data?.indicadoresSuspensos ? ` ${data.indicadoresSuspensos} indicador(es) suspenso(s) não entram nos totais.` : ''}</p>
    <p className="visually-hidden" role="status" aria-live="polite">{loading ? 'Carregando indicadores…' : notice}</p>
    {notice && !loading && <p className="bm-ov-notice" aria-hidden="true">{notice}</p>}
  </div>;
  const metrics = <>{context}
    <MetricGroup className="bm-kpis" label="Resumo dos indicadores da Empresa" items={firstLoad ? ['Total', 'Críticos', 'Em atenção', 'Normais', 'Sem dados'].map((label) => ({label, value: <span className="bm-ov-skel-num"/>})) : kpis}/></>;

  const filterBar = <FilterBar title="Filtros">
    <label className="field bm-ov-search"><span>Buscar por nome ou código</span>
      <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} maxLength={120} placeholder="Nome ou código do indicador"
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); apply({q: searchText.trim()}); } }}/></label>
    {narrow && <button type="button" className="secondary bm-ov-toggle" aria-expanded={filtersOpen} aria-controls="bm-ov-more" onClick={() => setFiltersOpen((o) => !o)}>Filtros ({chips.filter((c) => c.key !== 'q').length})</button>}
    {(!narrow || filtersOpen) && <label className="field" id="bm-ov-more"><span>Status</span>
      <select value={filters.status || ''} onChange={(e) => apply({status: e.target.value || null})}>
        <option value="">Todos os status</option>
        {STATUS_ORDER.map((status) => <option key={status} value={status}>{statusMeta(status).label}</option>)}
      </select></label>}
    {chips.length > 0 && <div className="bm-ov-chips" aria-label="Filtros ativos">
      {chips.map((chip) => <span className="bm-ov-chip" key={chip.key}>{chip.label}<button type="button" aria-label={`Remover filtro ${chip.label}`} onClick={() => removeChip(chip.key)}>×</button></span>)}
      <button type="button" className="bm-ov-clear" onClick={clearAll}>Limpar</button>
    </div>}
  </FilterBar>;

  let body;
  if (fatal) body = <Unavailable title="Não foi possível carregar os indicadores." description={error} onRetry={() => load(filters, true)}/>;
  else if (firstLoad) body = <Skeleton narrow={narrow}/>;
  else if (totais && totais.total === 0) body = <div className="bm-ov-empty" role="status"><strong>Nenhum indicador de negócio cadastrado para esta Empresa.</strong><p>Quando houver indicadores autorizados, eles aparecem aqui. Nenhum dado demonstrativo é exibido.</p></div>;
  else if (!items.length) body = <div className="bm-ov-empty" role="status"><strong>Nenhum indicador encontrado com os filtros atuais.</strong><button type="button" className="secondary" onClick={clearAll}>Limpar filtros</button></div>;
  else if (narrow) body = <ul className="bm-ov-cards" aria-label="Indicadores de negócio">{items.map((item) => <IndicatorCard key={item.id} item={item} canOpen={canOpen} onOpen={onOpen}/>)}</ul>;
  else body = <DataGrid sortable={false} stateKey={`business-overview-${tenantId}`} rows={items}
    onRow={canEdit ? (item) => canOpen(item) && onOpen(item) : undefined}
    columns={[
      {key: 'name', label: 'Indicador', render: (r) => <div className="bm-ov-name"><strong>{r.nome}</strong><code>{r.codigo}</code><small>{sourceLine(r)}</small></div>},
      {key: 'valorAtual', label: 'Valor atual', render: (r) => <CurrentValue item={r}/>},
      {key: 'limiteAlerta', label: 'Alerta', render: (r) => <Limit item={r} kind="alerta"/>},
      {key: 'limiteCritico', label: 'Crítico', render: (r) => <Limit item={r} kind="critico"/>},
      {key: 'statusPrincipal', label: 'Status', render: (r) => <div className="bm-ov-statuscell"><StatusBadge status={r.statusPrincipal}/>{r.motivoQualidade && <small>{r.motivoQualidade}</small>}</div>},
      {key: 'coletadoEm', label: 'Última coleta', render: (r) => <span className="bm-ov-num">{formatDateTime(r.coletadoEm)}</span>},
    ]}
    actions={canEdit ? (r) => canOpen(r) ? <button type="button" aria-label={`Editar indicador ${r.nome}`} onClick={() => onOpen(r)}>Editar</button> : null : undefined}/>;

  const showPager = data && totais?.total > 0 && items.length > 0;
  return <ListPage title="Visão Geral de Negócios" className="bm-ov-page" eyebrow="NEXORA › MONITORAMENTO DE NEGÓCIOS"
    description={`Empresa: ${companyName}. Valor medido, limites de alerta e crítico e a confiabilidade da coleta de cada indicador.`}
    actions={refresh} metrics={metrics} filters={filterBar}>
    {error && data && <div role="alert" className="bm-error bm-ov-error">Não foi possível atualizar: {error} Os dados abaixo são da última consulta bem-sucedida ({formatDateTime(data.consultadoEm)}).{' '}
      <button type="button" className="secondary" onClick={() => load(filters, true)}>Tentar novamente</button></div>}
    {body}
    {showPager && <div className="bm-pager" role="navigation" aria-label="Paginação dos indicadores">
      <span>Exibindo {range.from}–{range.to} de {range.total}</span>
      <label>Itens por página <select value={filters.pageSize} onChange={(e) => apply({pageSize: Number(e.target.value)})}>{PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
      <button type="button" disabled={data.pagina <= 1 || loading} onClick={() => apply({page: data.pagina - 1})}>Anterior</button>
      <span>Página {data.pagina} de {data.totalPaginas}</span>
      <button type="button" disabled={data.pagina >= data.totalPaginas || loading} onClick={() => apply({page: data.pagina + 1})}>Próxima</button>
    </div>}
  </ListPage>;
}
