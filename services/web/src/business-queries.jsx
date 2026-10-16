import React, {useEffect, useRef, useState} from 'react';
import {COLUMN_TYPES, columnsText, parseColumns, parseParameters, testMessage} from './business-queries-lib.js';

/** Query monitors (Business Monitor v1.0): Master governance (queries, workers, sources, queues) and, for people with
 *  `business.view`, execution history and results. Every operation is authorized by the backend; this file only decides
 *  what to show. New/edit flows are dedicated addressable pages (frontend standard), never modals or side panels. */
const ROOT = '/negocios';
const fmt = (v) => v ? new Date(v).toLocaleString('pt-BR') : '—';
const post = (data) => ({method: 'POST', body: JSON.stringify(data ?? {})});
const stateLabel = {ACTIVE: 'Ativo', DRAFT: 'Rascunho', SUSPENDED: 'Suspenso', DISABLED: 'Desativado', ONLINE: 'Online', OFFLINE: 'Offline',
  REVOKED: 'Revogado', SUSPENDED_W: 'Suspenso', SUCCEEDED: 'Sucesso', FAILED: 'Falha', TIMED_OUT: 'Tempo esgotado', REJECTED: 'Rejeitado',
  EXPIRED: 'Expirado', CANCELED: 'Cancelado', PENDING: 'Aguardando', LEASED: 'Reservado', RUNNING: 'Executando', TESTED: 'Testada',
  PUBLISHED: 'Publicada', SUPERSEDED: 'Substituída', REVOKED_R: 'Revogada'};
const label = (v) => stateLabel[v] || v || '—';
const ringLabel = {canary: 'Canário', stable: 'Estável', hold: 'Retido'};
const updateLabel = {DOWNLOADED: 'Baixada', APPLIED: 'Aplicada', FAILED: 'Falhou', ROLLED_BACK: 'Revertida'};
const channelLabel = {canary: 'Canário', stable: 'Estável', revoked: 'Revogada'};
const METRICS = [['row_count', 'Quantidade de linhas'], ['sum', 'Soma de uma coluna'], ['max', 'Máximo de uma coluna'],
  ['count_nonnull', 'Valores não nulos de uma coluna'], ['first_value', 'Primeiro valor de uma coluna']];

function Field({name, label: text, defaultValue = '', required = false, type = 'text', ...rest}) {
  return <label className="field"><span>{text}</span><input name={name} type={type} required={required} defaultValue={defaultValue} {...rest}/></label>;
}
function Select({name, label: text, rows, value = '', required = false, valueKey = 'id', textOf}) {
  return <label className="field"><span>{text}</span><select name={name} required={required} defaultValue={value}>
    <option value="">Selecione</option>{rows.map(x => <option key={x[valueKey]} value={x[valueKey]}>{textOf ? textOf(x) : x.name}</option>)}</select></label>;
}
const badge = (value) => <span className={`bm-status ${['ACTIVE', 'ONLINE', 'SUCCEEDED', 'PUBLISHED'].includes(value) ? 'normal'
  : ['FAILED', 'TIMED_OUT', 'REJECTED', 'REVOKED'].includes(value) ? 'critico' : ['SUSPENDED', 'TESTED', 'EXPIRED', 'PENDING', 'RUNNING', 'LEASED'].includes(value) ? 'atencao' : 'sem_dados'}`}>{label(value)}</span>;


/** Comandos de instalação já preenchidos. O token NUNCA entra nos comandos: o instalador o lê de forma protegida. */
function InstallGuide({info, workerId, tenantId}) {
  const server = info?.server_url || '';
  const key = info?.signing_public_key;
  const windows = `.\\install-business-worker.ps1 -ServerUrl "${server}" -TenantId "${tenantId}" -WorkerId "${workerId}" `
    + `-Token (Read-Host -AsSecureString "Token do worker") -SigningPublicKey "${key || '<CHAVE-PUBLICA>'}" `
    + `-DatabaseRef <nome_logico_do_banco> -ConnectionString "<string de conexão somente leitura>"`;
  const linux = `./install-business-worker.sh --server-url "${server}" --tenant-id "${tenantId}" --worker-id "${workerId}" `
    + `--signing-key "${key || '<CHAVE-PUBLICA>'}" --token-file worker.token --database-ref <nome_logico_do_banco> --connection-file conexao.txt`;
  const area = (id, text, rows) => <textarea id={id} readOnly rows={rows} value={text} onFocus={e => e.target.select()} style={{width: '100%', fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 13}}/>;
  return <section className="form-section">
    <h3>Instalação do Business Worker</h3>
    {!key && <p role="alert" className="bm-error">A chave pública de assinatura não está configurada no servidor (BUSINESS_SIGNING_KEY). Configure antes de instalar o worker.</p>}
    <p className="bm-context">Rode na máquina do cliente que enxerga o banco. A conexão com o banco fica somente nessa máquina, com usuário de leitura.
      Confirme a chave abaixo com quem administra o servidor antes de instalar.</p>
    <label className="field" htmlFor="bm-key"><span>Chave pública de assinatura</span>{area('bm-key', key || 'Não configurada', 2)}</label>
    <label className="field" htmlFor="bm-win"><span>Windows (PowerShell como Administrador)</span>{area('bm-win', windows, 4)}</label>
    <label className="field" htmlFor="bm-linux"><span>Linux (root)</span>{area('bm-linux', linux, 4)}</label>
    <p className="bm-context">No Linux, grave o token em <code>worker.token</code> e a conexão em <code>conexao.txt</code> com permissão restrita. O token não aparece nos comandos.</p>
  </section>;
}

export default function BusinessQueries({page, tenantId, request, access, isMaster, ui, navigate}) {
  const {ListPage, MetricGroup, FilterBar, DataGrid, FormPage, FormSection, FormActions} = ui;
  const call = useRef(request); call.current = request;
  const caps = access.capabilities || [];
  const canView = caps.includes('business.view');
  const segments = window.location.pathname.split('/').slice(3).filter(Boolean);
  const [items, setItems] = useState([]);
  const [aux, setAux] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [issuedToken, setIssuedToken] = useState(null);
  const [linkedWorker, setLinkedWorker] = useState(null);
  const [workerMode, setWorkerMode] = useState('agent');
  const [executions, setExecutions] = useState({items: [], page: 1, page_size: 25});
  const [result, setResult] = useState(null);
  const [testState, setTestState] = useState(null);
  const sequence = useRef(0);
  const sub = segments[1];

  const run = async (fn, done) => {
    setBusy(true); setError(''); setNotice('');
    try { const out = await fn(); if (done) await done(out); return out; }
    catch (e) { setError(e.message || 'Falha na operação'); return null; }
    finally { setBusy(false); }
  };

  const load = async () => {
    const id = ++sequence.current;
    setBusy(true); setError('');
    try {
      let data = {};
      if (page === 'consultas') {
        if (isMaster) {
          const list = await call.current('/api/v1/master/business/monitors');
          data.items = list;
          if (segments[0] && segments[0] !== 'novo') data.detail = await call.current(`/api/v1/master/business/monitors/${segments[0]}/revisions`);
          if (segments[0] === 'novo' || sub === 'revisao') {
            data.indicators = await call.current('/api/v1/master/business/indicators');
            data.sources = await call.current('/api/v1/master/business/data-sources');
          }
        } else data.items = canView ? await call.current('/api/v1/business/monitors') : [];
      } else if (page === 'workers') {
        data.items = await call.current('/api/v1/master/business/workers');
        if (segments[0] === 'novo' || sub === 'instalar') data.install = await call.current('/api/v1/master/business/install-info');
        if (segments[0] === 'novo') data.agents = await call.current('/api/v1/master/business/agents');
      }
      else if (page === 'fontes') {
        data.items = await call.current('/api/v1/master/business/data-sources');
        if (segments[0] === 'novo' || sub === 'editar') data.workers = (await call.current('/api/v1/master/business/workers')).filter(w => w.status !== 'REVOKED');
      } else if (page === 'atualizacoes') {
        data.items = await call.current('/api/v1/master/business/releases');
      } else if (page === 'filas') {
        data.broker = await call.current('/api/v1/master/business/broker/status');
        data.items = await call.current('/api/v1/master/business/broker/dead-letters');
        data.workers = await call.current('/api/v1/master/business/workers');
      }
      if (id !== sequence.current) return;
      setItems(data.items || []); setAux(data);
    } catch (e) { if (id === sequence.current) setError(e.message || 'Falha ao carregar'); }
    finally { if (id === sequence.current) setBusy(false); }
  };
  useEffect(() => { setNotice(''); setResult(null); setTestState(null); if (tenantId) load(); /* eslint-disable-next-line */ }, [tenantId, page, window.location.pathname]);

  const loadExecutions = async (id, pageNumber = 1, pageSize = executions.page_size) => {
    if (!canView) return;
    try { setExecutions(await call.current(`/api/v1/business/monitors/${id}/executions?page=${pageNumber}&page_size=${pageSize}`)); }
    catch (e) { setError(e.message || 'Falha ao consultar execuções'); }
  };
  useEffect(() => { if (page === 'consultas' && segments[0] && segments[0] !== 'novo' && !sub) loadExecutions(segments[0]); /* eslint-disable-next-line */ }, [page, window.location.pathname, canView]);
  useEffect(() => {
    if (page === 'consultas' && sub === 'execucoes' && segments[2] && canView) {
      setResult(null);
      call.current(`/api/v1/business/monitors/${segments[0]}/executions/${segments[2]}/result`).then(setResult).catch(e => setError(e.message || 'Resultado indisponível'));
    }
    // eslint-disable-next-line
  }, [page, window.location.pathname, canView]);

  const header = (title, description) => ({title, description, eyebrow: 'NEXORA › MONITORAMENTO DE NEGÓCIOS'});
  const alerts = <>{error && <p role="alert" className="bm-error">{error}</p>}{notice && <p role="status" className="info-banner">{notice}</p>}{busy && <p role="status">Carregando...</p>}</>;
  const filtered = items.filter(x => `${x.name || ''} ${x.indicator_name || ''} ${x.database_ref || ''} ${x.execution_id || ''}`.toLowerCase().includes(search.toLowerCase())
    && (!statusFilter || x.status === statusFilter));

  // ------------------------------------------------------------------ Consultas
  if (page === 'consultas') {
    const detail = aux.detail;
    if (segments[0] === 'novo' || (segments[0] && sub === 'revisao' && segments[2] === 'nova')) {
      const revising = segments[0] !== 'novo';
      const base = revising ? detail?.revisions?.[0] : null;
      if (revising && !detail) return <section className="panel">{alerts}</section>;
      if (!isMaster) return <section className="panel"><h2>Acesso não autorizado</h2><p>Somente o Master técnico governa consultas SQL.</p></section>;
      const submit = (event) => {
        event.preventDefault();
        const f = Object.fromEntries(new FormData(event.currentTarget).entries());
        const columns = parseColumns(f.columns), parameters = parseParameters(f.parameters);
        if (columns.error || parameters.error) { setError(columns.error || parameters.error); return; }
        const metric = f.metric_kind === 'row_count' ? {kind: 'row_count'} : {kind: f.metric_kind, column: f.metric_column};
        const limits = {}; for (const key of ['timeout_seconds', 'max_rows', 'max_bytes']) if (f[key]) limits[key] = Number(f[key]);
        const body = {query: f.query, parameters: parameters.parameters, columns: columns.columns, metric, ...(Object.keys(limits).length ? {limits} : {})};
        run(() => revising ? call.current(`/api/v1/master/business/monitors/${segments[0]}/revisions`, post(body))
          : call.current('/api/v1/master/business/monitors', post({...body, name: f.name, description: f.description, indicator_id: f.indicator_id,
            source_id: f.source_id, interval_seconds: Number(f.interval_seconds)})),
        (out) => navigate(`consultas/${revising ? segments[0] : out.id}`));
      };
      return <FormPage title={revising ? `Nova revisão — ${detail.name}` : 'Nova consulta de negócio'}
        description={revising ? 'A revisão nasce como rascunho: teste e publique para valer. A anterior segue ativa até lá.' : 'A consulta só roda depois de testada e publicada pelo Master.'}
        onBack={() => navigate(revising ? `consultas/${segments[0]}` : 'consultas')}>
        {alerts}
        <form className="nexora-form" onSubmit={submit}>
          {!revising && <FormSection title="Identificação">
            <Field name="name" label="Nome da consulta" required minLength="3" maxLength="160"/>
            <Field name="description" label="Descrição" maxLength="2000"/>
            <Select name="indicator_id" label="Indicador alimentado" rows={(aux.indicators || []).filter(i => i.active)} textOf={i => `${i.name} (${i.code})`} required/>
            <Select name="source_id" label="Fonte de dados" rows={aux.sources || []} textOf={s => `${s.name} — ${s.dialect} · worker ${s.worker_name}`} required/>
            <Field name="interval_seconds" label="Intervalo (segundos, de 60 a 86400)" type="number" min="60" max="86400" defaultValue="300" required/>
          </FormSection>}
          <FormSection title="Consulta SQL" description="Somente SELECT. Use @parametro e declare o valor ao lado; $today vira a data do dia. O servidor valida e normaliza o texto.">
            <label className="field wide"><span>Consulta</span><textarea name="query" rows="8" required minLength="6" maxLength="20000" defaultValue={base?.query_text || ''} spellCheck="false"/></label>
            <label className="field"><span>Parâmetros (JSON)</span><textarea name="parameters" rows="4" spellCheck="false" defaultValue={base ? JSON.stringify(base.parameters || {}) : ''} placeholder='{"hoje": "$today"}'/></label>
          </FormSection>
          <FormSection title="Resultado esperado" description="Uma coluna por linha, no formato nome:tipo, na mesma ordem do SELECT.">
            <label className="field"><span>Colunas</span><textarea name="columns" rows="5" required spellCheck="false" defaultValue={columnsText(base?.column_schema)} placeholder={'documento:string\nvalor_vencido:decimal'}/></label>
            <label className="field"><span>Cálculo do indicador</span><select name="metric_kind" defaultValue={base?.metric?.kind || 'sum'}>{METRICS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
            <Field name="metric_column" label="Coluna do cálculo (vazio para quantidade de linhas)" defaultValue={base?.metric?.column || ''}/>
          </FormSection>
          <FormSection title="Limites (opcionais)" description="Máximos permitidos: 60 s, 5000 linhas e 5 MB. Padrão: 15 s, 500 linhas.">
            <Field name="timeout_seconds" label="Tempo máximo (s)" type="number" min="1" max="60" defaultValue={base?.limits?.timeout_seconds || ''}/>
            <Field name="max_rows" label="Máximo de linhas" type="number" min="1" max="5000" defaultValue={base?.limits?.max_rows || ''}/>
            <Field name="max_bytes" label="Máximo de bytes" type="number" min="1" max="5242880" defaultValue={base?.limits?.max_bytes || ''}/>
          </FormSection>
          <FormActions onCancel={() => navigate(revising ? `consultas/${segments[0]}` : 'consultas')} submitting={busy} submitLabel={revising ? 'Criar revisão' : 'Criar consulta'}/>
        </form>
      </FormPage>;
    }

    if (segments[0] && sub === 'situacao' && segments[2]) {
      const action = segments[2], names = {suspend: 'Suspender', resume: 'Retomar', disable: 'Desativar'};
      if (!names[action]) return <p>Ação desconhecida.</p>;
      return <FormPage title={`${names[action]} consulta`} description={action === 'disable' ? 'Desativar cancela as execuções ainda não iniciadas. A decisão fica em auditoria.' : 'A decisão fica em auditoria.'}
        onBack={() => navigate(`consultas/${segments[0]}`)}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const reason = new FormData(e.currentTarget).get('reason');
          run(() => call.current(`/api/v1/master/business/monitors/${segments[0]}/status`, post({action, reason})), () => navigate(`consultas/${segments[0]}`)); }}>
          <FormSection title="Justificativa"><Field name="reason" label="Motivo (de 4 a 300 caracteres)" required minLength="4" maxLength="300"/></FormSection>
          <FormActions onCancel={() => navigate(`consultas/${segments[0]}`)} submitting={busy} submitLabel={names[action]}/>
        </form>
      </FormPage>;
    }

    if (segments[0] && sub === 'execucoes' && segments[2]) {
      return <FormPage title="Resultado da execução" description="Dados de negócio autorizados pela permissão de visualização. Cada leitura é registrada em auditoria." onBack={() => navigate(`consultas/${segments[0]}`)}>
        {alerts}
        {!canView ? <p>Sem permissão de visualização.</p> : result && <>
          <MetricGroup items={[{label: 'Estado', value: label(result.state)}, {label: 'Linhas', value: result.row_count ?? '—'}, {label: 'Truncado', value: result.truncated ? 'Sim' : 'Não'}]}/>
          <DataGrid rows={(result.rows || []).slice(0, 500).map((r, i) => ({id: i, ...r}))}
            columns={(result.columns || []).map(c => ({key: c.name, label: `${c.name} (${c.type})`}))}/>
          {(result.rows || []).length > 500 && <p className="bm-context">Mostrando as primeiras 500 linhas.</p>}</>}
      </FormPage>;
    }

    if (segments[0] && segments[0] !== 'novo') {
      if (!detail) return <section className="panel">{alerts}</section>;
      const latest = detail.revisions[0];
      const testRevision = async (revision) => {
        setTestState({revision, state: 'PENDING'});
        const started = await run(() => call.current(`/api/v1/master/business/monitors/${segments[0]}/revisions/${revision}/test`, post()));
        if (!started) { setTestState(null); return; }
        for (let attempt = 0; attempt < 24; attempt++) {
          await new Promise(r => setTimeout(r, 2500));
          const meta = await call.current(`/api/v1/master/business/executions/${started.execution_id}`).catch(() => null);
          if (meta && !['PENDING', 'LEASED', 'RUNNING'].includes(meta.state)) { setTestState({revision, ...meta}); await load(); return; }
        }
        setTestState({revision, state: 'PENDING', slow: true});
      };
      const publish = (revision) => run(() => call.current(`/api/v1/master/business/monitors/${segments[0]}/revisions/${revision}/publish`, post()), async () => { setNotice(`Revisão ${revision} publicada.`); await load(); });
      return <section className="panel list-page">
        <ui.PageHeader {...header(detail.name, detail.description || 'Consulta de negócio')} actions={<>
          <button type="button" className="secondary" onClick={() => navigate('consultas')}>← Voltar</button>
          {isMaster && <button type="button" className="primary" onClick={() => navigate(`consultas/${segments[0]}/revisao/nova`)}>+ Nova revisão</button>}</>}/>
        {alerts}
        <MetricGroup items={[{label: 'Situação', value: label(detail.status)}, {label: 'Intervalo', value: `${detail.interval_seconds}s`},
          {label: 'Publicada', value: detail.published_revision ? `v${detail.published_revision}` : '—'}, {label: 'Mais recente', value: `v${detail.current_revision}`}, {label: 'Banco', value: detail.dialect}]}/>
        {isMaster && <div className="bm-row-actions" role="group" aria-label="Situação da consulta">
          {detail.status === 'ACTIVE' && <button type="button" onClick={() => navigate(`consultas/${segments[0]}/situacao/suspend`)}>Suspender</button>}
          {['SUSPENDED', 'DISABLED'].includes(detail.status) && detail.published_revision && <button type="button" onClick={() => navigate(`consultas/${segments[0]}/situacao/resume`)}>Retomar</button>}
          {detail.status !== 'DISABLED' && <button type="button" onClick={() => navigate(`consultas/${segments[0]}/situacao/disable`)}>Desativar</button>}</div>}
        {testState && (() => { const m = testMessage(testState); return <div ref={el => el && el.scrollIntoView({block: 'nearest', behavior: 'smooth'})} role={m.tone === 'error' ? 'alert' : 'status'} aria-live="polite" className={`bm-test-banner bm-test-${m.tone}`}>
          {m.tone === 'info' && <span className="bm-spinner" aria-hidden="true"/>}<strong>{m.tone === 'success' ? '✔ ' : m.tone === 'error' ? '✖ ' : ''}{m.text}</strong></div>; })()}
        <h3>Revisões</h3>
        <DataGrid stateKey={`business:${tenantId}:revisions:${segments[0]}`} rows={detail.revisions.map(r => ({...r, id: r.revision}))}
          columns={[{key: 'revision', label: 'Revisão', render: r => `v${r.revision}`}, {key: 'state', label: 'Estado', render: r => badge(r.state)},
            {key: 'created_at', label: 'Criada', render: r => fmt(r.created_at)}, {key: 'test_state', label: 'Último teste', render: r => r.test_state ? <>{badge(r.test_state)} {r.test_error_code || ''}</> : '—'},
            {key: 'published_at', label: 'Publicada em', render: r => fmt(r.published_at)}]}
          actions={isMaster ? (r) => <>
            {['DRAFT', 'TESTED'].includes(r.state) && <button type="button" disabled={busy || (testState && testState.revision === r.revision && ['PENDING', 'LEASED', 'RUNNING'].includes(testState.state))} onClick={() => testRevision(r.revision)}>{testState && testState.revision === r.revision && ['PENDING', 'LEASED', 'RUNNING'].includes(testState.state) ? 'Testando…' : 'Testar'}</button>}
            {r.state === 'TESTED' && <button type="button" className="primary" disabled={busy} onClick={() => publish(r.revision)}>Publicar</button>}</> : undefined}/>
        <h3>Consulta da revisão v{latest.revision}</h3>
        <pre className="bm-sql" tabIndex="0" aria-label="Texto normalizado da consulta">{latest.query_text}</pre>
        <p className="bm-context">Parâmetros: <code>{JSON.stringify(latest.parameters)}</code> · Colunas: {columnsText(latest.column_schema).replaceAll('\n', ', ')} · Cálculo: {latest.metric.kind}{latest.metric.column ? ` (${latest.metric.column})` : ''}</p>
        <h3>Execuções recentes</h3>
        {!canView ? <p className="bm-context">O histórico de execuções exige a permissão de visualização do Business Monitor.</p> : <>
          <DataGrid rows={executions.items} columns={[{key: 'scheduled_at', label: 'Agendada', render: r => fmt(r.scheduled_at)}, {key: 'state', label: 'Estado', render: r => badge(r.state)},
            {key: 'revision', label: 'Revisão', render: r => `v${r.revision}`}, {key: 'duration_ms', label: 'Duração', render: r => r.duration_ms != null ? `${r.duration_ms} ms` : '—'},
            {key: 'row_count', label: 'Linhas'}, {key: 'error_code', label: 'Erro'}]}
            actions={(r) => r.state === 'SUCCEEDED' ? <button type="button" onClick={() => navigate(`consultas/${segments[0]}/execucoes/${r.id}`)}>Visualizar resultado</button> : null}/>
          <div className="bm-pager" role="navigation" aria-label="Paginação das execuções">
            <label>Itens <select value={executions.page_size} onChange={e => loadExecutions(segments[0], 1, Number(e.target.value))}>{[10, 25, 50].map(n => <option key={n}>{n}</option>)}</select></label>
            <button type="button" disabled={executions.page <= 1} onClick={() => loadExecutions(segments[0], executions.page - 1)}>Anterior</button>
            <span>Página {executions.page}</span>
            <button type="button" disabled={executions.items.length < executions.page_size} onClick={() => loadExecutions(segments[0], executions.page + 1)}>Próxima</button></div></>}
      </section>;
    }

    const failing = items.filter(m => ['FAILED', 'TIMED_OUT', 'REJECTED'].includes(m.last_state)).length;
    return <ListPage {...header('Consultas de negócio', 'Consultas SQL somente leitura executadas pelo Business Worker. Cada alteração passa por rascunho, teste e publicação do Master.')}
      actions={isMaster ? <button type="button" className="primary" onClick={() => navigate('consultas/novo')}>+ Nova consulta</button> : null}
      metrics={<MetricGroup items={[{label: 'Consultas', value: busy ? '—' : items.length}, {label: 'Ativas', value: items.filter(m => m.status === 'ACTIVE').length},
        {label: 'Suspensas', value: items.filter(m => m.status === 'SUSPENDED').length}, {label: 'Última execução com falha', value: failing}]}/>}
      filters={<FilterBar><label>Pesquisar <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Nome ou indicador"/></label>
        <label>Situação <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}><option value="">Todas</option>
          {['ACTIVE', 'DRAFT', 'SUSPENDED', 'DISABLED'].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
        <button type="button" onClick={load} disabled={busy}>Atualizar</button></FilterBar>}>
      {alerts}
      {!isMaster && !canView ? <p>Sem permissão para consultar monitores.</p> : <DataGrid stateKey={`business:${tenantId}:consultas`} rows={filtered}
        columns={[{key: 'name', label: 'Consulta'}, {key: 'indicator_name', label: 'Indicador'}, {key: 'status', label: 'Situação', render: r => badge(r.status)},
          {key: 'interval_seconds', label: 'Intervalo', render: r => `${r.interval_seconds}s`}, {key: 'published_revision', label: 'Publicada', render: r => r.published_revision ? `v${r.published_revision}` : '—'},
          {key: 'worker_name', label: 'Worker', render: r => <>{r.worker_name} {badge(r.worker_status)}</>},
          {key: 'last_state', label: 'Última execução', render: r => r.last_state ? <>{badge(r.last_state)} <small>{fmt(r.last_finished_at)}</small></> : '—'}]}
        actions={(r) => <button type="button" onClick={() => navigate(`consultas/${r.id}`)}>Visualizar</button>}/>}
    </ListPage>;
  }

  // ------------------------------------------------------------------ Workers
  if (page === 'workers') {
    if (!isMaster) return <section className="panel"><h2>Acesso não autorizado</h2><p>Os Business Workers são administrados pelo Master técnico.</p></section>;
    if (segments[0] === 'novo') {
      if (issuedToken) return <FormPage title="Worker criado" description="Copie o token agora: ele não será exibido novamente." onBack={() => { setIssuedToken(null); navigate('workers'); }}>
        <section className="form-section"><div className="form-section-grid">
          <Field name="id" label="Identificador do worker" defaultValue={issuedToken.id} readOnly onFocus={e => e.target.select()}/>
          <Field name="token" label="Token do worker (exibido uma única vez)" defaultValue={issuedToken.worker_token} readOnly onFocus={e => e.target.select()}/>
          <Field name="tenant" label="Identificador da Empresa" defaultValue={tenantId} readOnly onFocus={e => e.target.select()}/></div>
          <p className="bm-context">Guarde o token em arquivo protegido; não o envie por e-mail ou chat.</p></section>
        <InstallGuide info={aux.install} workerId={issuedToken.id} tenantId={tenantId}/>
        <div className="form-actions"><button type="button" className="primary" onClick={() => { setIssuedToken(null); navigate('workers'); }}>Concluir</button></div>
      </FormPage>;
      if (linkedWorker) return <FormPage title="Worker vinculado ao agente" description="Nenhum token foi gerado: o worker usa a identidade do agente de monitoramento já instalado." onBack={() => { setLinkedWorker(null); navigate('workers'); }}>
        <section className="form-section"><p className="bm-context">O worker <strong>{linkedWorker.name}</strong> foi vinculado ao agente. Para ele começar a executar consultas, abra o aplicativo <strong>Nexora Agent — Configuração</strong> na máquina do agente,
          marque <strong>Consultas de negócio</strong>, informe a conexão do banco (usuário somente leitura), use <strong>Testar conexão</strong> e salve. O worker fica <strong>Online</strong> quando o agente enviar o primeiro sinal.</p></section>
        <div className="form-actions"><button type="button" className="primary" onClick={() => { setLinkedWorker(null); navigate('workers'); }}>Concluir</button></div>
      </FormPage>;
      return <FormPage title="Novo worker" description="Cada worker tem identidade, token e fila próprios e executa somente as fontes ligadas a ele." onBack={() => navigate('workers')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.currentTarget).entries());
          if (workerMode === 'agent') run(() => call.current('/api/v1/master/business/workers/from-agent', post({agent_id: f.agent_id, name: f.name || undefined, capacity: Number(f.capacity)})), (out) => setLinkedWorker(out));
          else run(() => call.current('/api/v1/master/business/workers', post({name: f.name, capacity: Number(f.capacity)})), (out) => setIssuedToken(out)); }}>
          <FormSection title="Tipo de worker"><label className="field"><span>Como o worker se identifica</span>
            <select name="mode" value={workerMode} onChange={e => setWorkerMode(e.target.value)}>
              <option value="agent">Dentro de um agente de monitoramento já instalado (recomendado, sem token)</option>
              <option value="token">Serviço independente (token exibido uma única vez)</option></select></label></FormSection>
          <FormSection title="Identificação">
            {workerMode === 'agent' && <Select name="agent_id" label="Agente de monitoramento" rows={(aux.agents || []).filter(a => !a.worker_id)} textOf={a => `${a.name} (${a.status})`} required/>}
            <Field name="name" label={workerMode === 'agent' ? 'Nome do worker (opcional, padrão: nome do agente)' : 'Nome do worker'} required={workerMode !== 'agent'} minLength="2" maxLength="120"/>
            <Field name="capacity" label="Consultas simultâneas (1 a 4)" type="number" min="1" max="4" defaultValue="1" required/></FormSection>
          <FormActions onCancel={() => navigate('workers')} submitting={busy} submitLabel="Criar worker"/>
        </form></FormPage>;
    }
    if (segments[0] && sub === 'instalar') {
      const worker = items.find(w => w.id === segments[0]);
      return <FormPage title={`Instalar worker${worker ? ` — ${worker.name}` : ''}`} description="Comandos prontos para a máquina do cliente. O token só é exibido na criação; se perdeu, revogue e crie outro worker." onBack={() => navigate('workers')}>
        {alerts}
        <InstallGuide info={aux.install} workerId={segments[0]} tenantId={tenantId}/>
        <div className="form-actions"><button type="button" className="primary" onClick={() => navigate('workers')}>Voltar</button></div>
      </FormPage>;
    }
    if (segments[0] && sub === 'editar') {
      const worker = items.find(w => w.id === segments[0]);
      if (!worker) return <section className="panel">{alerts}</section>;
      return <FormPage title={`Editar worker — ${worker.name}`} description="Nome e número de consultas simultâneas. O token e a identidade não mudam." onBack={() => navigate('workers')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.currentTarget).entries());
          run(() => call.current(`/api/v1/master/business/workers/${segments[0]}`, {method: 'PUT', body: JSON.stringify({name: f.name, capacity: Number(f.capacity)})}), () => navigate('workers')); }}>
          <FormSection title="Identificação"><Field name="name" label="Nome do worker" defaultValue={worker.name} required minLength="2" maxLength="120"/>
            <Field name="capacity" label="Consultas simultâneas (1 a 4)" type="number" min="1" max="4" defaultValue={worker.capacity} required/></FormSection>
          <FormActions onCancel={() => navigate('workers')} submitting={busy} submitLabel="Salvar worker"/>
        </form></FormPage>;
    }
    if (segments[0] && sub === 'anel') {
      const worker = items.find(w => w.id === segments[0]);
      return <FormPage title={`Anel de atualização${worker ? ` — ${worker.name}` : ''}`} description="Canário recebe versões novas primeiro; Estável só recebe versões já promovidas; Retido congela o worker na versão atual." onBack={() => navigate('workers')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const ring = new FormData(e.currentTarget).get('ring');
          run(() => call.current(`/api/v1/master/business/workers/${segments[0]}/update-ring`, {method: 'PUT', body: JSON.stringify({ring})}), () => navigate('workers')); }}>
          <FormSection title="Anel"><label className="field"><span>Anel</span><select name="ring" defaultValue={worker?.update_ring || 'stable'}>
            {Object.entries(ringLabel).map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label></FormSection>
          <FormActions onCancel={() => navigate('workers')} submitting={busy} submitLabel="Salvar anel"/>
        </form></FormPage>;
    }
    if (segments[0] && sub === 'revogar') {
      const worker = items.find(w => w.id === segments[0]);
      return <FormPage title={`Revogar worker${worker ? ` — ${worker.name}` : ''}`} description="O token deixa de valer imediatamente e as execuções pendentes são canceladas. Não há como reativar: crie outro worker." onBack={() => navigate('workers')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); run(() => call.current(`/api/v1/master/business/workers/${segments[0]}/revoke`, post()), () => navigate('workers')); }}>
          <FormSection title="Confirmação"><p>Confirme a revogação de <strong>{worker?.name || segments[0]}</strong>. A ação fica em auditoria.</p></FormSection>
          <FormActions onCancel={() => navigate('workers')} submitting={busy} submitLabel="Revogar worker"/>
        </form></FormPage>;
    }
    return <ListPage {...header('Business Workers', 'Serviços do cliente que executam as consultas. O agente de infraestrutura não participa.')}
      actions={<button type="button" className="primary" onClick={() => navigate('workers/novo')}>+ Novo worker</button>}
      metrics={<MetricGroup items={[{label: 'Workers', value: busy ? '—' : items.length}, {label: 'Online', value: items.filter(w => w.status === 'ONLINE').length},
        {label: 'Offline', value: items.filter(w => w.status === 'OFFLINE').length}, {label: 'Aguardando execução', value: items.reduce((n, w) => n + Number(w.queued_jobs || 0), 0)}]}/>}
      filters={<FilterBar><label>Pesquisar <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Nome do worker"/></label>
        <label>Situação <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}><option value="">Todas</option>
          {['ONLINE', 'OFFLINE', 'REVOKED'].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
        <button type="button" onClick={load} disabled={busy}>Atualizar</button></FilterBar>}>
      {alerts}
      <DataGrid stateKey={`business:${tenantId}:workers`} rows={filtered} columns={[{key: 'name', label: 'Worker'}, {key: 'status', label: 'Situação', render: r => badge(r.status)},
        {key: 'agent_name', label: 'Agente', render: r => r.agent_name || 'Independente'}, {key: 'version', label: 'Versão'}, {key: 'last_heartbeat_at', label: 'Último sinal', render: r => fmt(r.last_heartbeat_at)}, {key: 'capacity', label: 'Capacidade'},
        {key: 'active_jobs', label: 'Em execução'}, {key: 'queued_jobs', label: 'Na fila'}, {key: 'sources', label: 'Fontes'},
        {key: 'update_ring', label: 'Anel de atualização', render: r => ringLabel[r.update_ring] || '—'},
        {key: 'update_status', label: 'Última atualização', render: r => r.update_status ? `${updateLabel[r.update_status] || r.update_status} ${r.update_version || ''}` : '—'}]}
        actions={(r) => r.status !== 'REVOKED' ? <>
          <button type="button" onClick={() => navigate(`workers/${r.id}/editar`)}>Editar</button>
          <button type="button" onClick={() => navigate(`workers/${r.id}/instalar`)}>Instalar</button>
          <button type="button" onClick={() => navigate(`workers/${r.id}/anel`)}>Editar anel</button>
          <button type="button" onClick={() => navigate(`workers/${r.id}/revogar`)}>Revogar</button></> : null}/>
    </ListPage>;
  }

  // ------------------------------------------------------------------ Fontes
  if (page === 'fontes') {
    if (!isMaster) return <section className="panel"><h2>Acesso não autorizado</h2><p>As fontes de dados são administradas pelo Master técnico.</p></section>;
    if (segments[0] && sub === 'editar') {
      const source = items.find(x => x.id === segments[0]);
      if (!source) return <section className="panel">{alerts}</section>;
      return <FormPage title={`Editar fonte — ${source.name}`} description="Mudar o worker ou o nome lógico do banco cancela as consultas que ainda estão na fila desta fonte. O tipo de banco não muda: as consultas foram validadas para ele." onBack={() => navigate('fontes')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.currentTarget).entries());
          run(() => call.current(`/api/v1/master/business/data-sources/${segments[0]}`, {method: 'PUT', body: JSON.stringify({name: f.name, worker_id: f.worker_id, database_ref: f.database_ref})}), () => navigate('fontes')); }}>
          <FormSection title="Fonte"><Field name="name" label="Nome da fonte" defaultValue={source.name} required minLength="2" maxLength="120"/>
            <Select name="worker_id" label="Worker que executa" rows={aux.workers || []} value={source.worker_id} required/>
            <Field name="dialect" label="Banco (não editável)" defaultValue={source.dialect === 'sqlserver' ? 'SQL Server' : 'PostgreSQL'} readOnly/>
            <Field name="database_ref" label="Nome lógico do banco (minúsculas, números e _)" defaultValue={source.database_ref} required pattern="[a-z][a-z0-9_]{1,63}" title="Comece com letra minúscula; use letras minúsculas, números e _"/></FormSection>
          <FormActions onCancel={() => navigate('fontes')} submitting={busy} submitLabel="Salvar fonte"/>
        </form></FormPage>;
    }
    if (segments[0] && sub === 'situacao') {
      const source = items.find(x => x.id === segments[0]);
      if (!source) return <section className="panel">{alerts}</section>;
      const next = !source.active;
      return <FormPage title={`${next ? 'Reativar' : 'Inativar'} fonte — ${source.name}`} description={next ? 'As consultas desta fonte voltam a ser agendadas.' : 'As consultas desta fonte deixam de ser agendadas e as que estão na fila são canceladas. As que já estão rodando terminam.'} onBack={() => navigate('fontes')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const reason = new FormData(e.currentTarget).get('reason') || '';
          run(() => call.current(`/api/v1/master/business/data-sources/${segments[0]}/active`, post({active: next, reason})), () => navigate('fontes')); }}>
          <FormSection title="Confirmação"><p>Confirme a mudança de situação de <strong>{source.name}</strong>. A ação fica em auditoria.</p>
            <Field name="reason" label="Motivo (opcional)" maxLength="500"/></FormSection>
          <FormActions onCancel={() => navigate('fontes')} submitting={busy} submitLabel={next ? 'Reativar fonte' : 'Inativar fonte'}/>
        </form></FormPage>;
    }
    if (segments[0] === 'novo') return <FormPage title="Nova fonte de dados" description="Só o nome lógico do banco é guardado aqui. Usuário, senha e servidor ficam no worker, no cliente." onBack={() => navigate('fontes')}>
      {alerts}
      <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.currentTarget).entries());
        run(() => call.current('/api/v1/master/business/data-sources', post({name: f.name, worker_id: f.worker_id, dialect: f.dialect, database_ref: f.database_ref})), () => navigate('fontes')); }}>
        <FormSection title="Fonte"><Field name="name" label="Nome da fonte" required minLength="2" maxLength="120"/>
          <Select name="worker_id" label="Worker que executa" rows={aux.workers || []} required/>
          <label className="field"><span>Banco</span><select name="dialect" defaultValue="postgres"><option value="postgres">PostgreSQL</option><option value="sqlserver">SQL Server</option></select></label>
          <Field name="database_ref" label="Nome lógico do banco (minúsculas, números e _)" required pattern="[a-z][a-z0-9_]{1,63}" title="Comece com letra minúscula; use letras minúsculas, números e _"/></FormSection>
        <FormActions onCancel={() => navigate('fontes')} submitting={busy} submitLabel="Criar fonte"/>
      </form></FormPage>;
    return <ListPage {...header('Fontes de dados', 'Bancos consultados pelos workers, identificados pelo nome lógico configurado no cliente.')}
      actions={<button type="button" className="primary" onClick={() => navigate('fontes/novo')}>+ Nova fonte</button>}
      metrics={<MetricGroup items={[{label: 'Fontes', value: busy ? '—' : items.length}, {label: 'Ativas', value: items.filter(s => s.active).length}]}/>}
      filters={<FilterBar><label>Pesquisar <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Nome ou banco"/></label><button type="button" onClick={load} disabled={busy}>Atualizar</button></FilterBar>}>
      {alerts}
      <DataGrid stateKey={`business:${tenantId}:fontes`} rows={filtered} columns={[{key: 'name', label: 'Fonte'}, {key: 'dialect', label: 'Banco'}, {key: 'database_ref', label: 'Nome lógico'},
        {key: 'worker_name', label: 'Worker', render: r => <>{r.worker_name} {badge(r.worker_status)}</>}, {key: 'active', label: 'Ativa', render: r => r.active ? 'Sim' : 'Não'}]}
        actions={(r) => <>
          <button type="button" onClick={() => navigate(`fontes/${r.id}/editar`)}>Editar</button>
          <button type="button" onClick={() => navigate(`fontes/${r.id}/situacao`)}>{r.active ? 'Inativar' : 'Reativar'}</button></>}/>
    </ListPage>;
  }

  // ------------------------------------------------------------------ Atualizações (signed releases, canary ring)
  if (page === 'atualizacoes') {
    if (!isMaster) return <section className="panel"><h2>Acesso não autorizado</h2><p>As versões do Business Worker são administradas pelo Master técnico.</p></section>;
    const release = items.find(r => r.id === segments[0]);
    if (segments[0] === 'novo') return <FormPage title="Registrar versão do Worker" description="O pacote já deve estar na pasta releases do servidor (copiado pela operação). O servidor calcula o SHA-256 e a versão nasce no canal Canário." onBack={() => navigate('atualizacoes')}>
      {alerts}
      <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.currentTarget).entries());
        run(() => call.current('/api/v1/master/business/releases', post({version: f.version, platform: f.platform, file_name: f.file_name, notes: f.notes})), () => navigate('atualizacoes')); }}>
        <FormSection title="Pacote">
          <Field name="version" label="Versão (ex.: 1.2.0)" required pattern="[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}" title="Use o formato 1.2.0"/>
          <label className="field"><span>Plataforma</span><select name="platform" defaultValue="linux-x64"><option value="linux-x64">Linux x64</option><option value="win-x64">Windows x64</option></select></label>
          <Field name="file_name" label="Nome do arquivo na pasta releases" required pattern="[A-Za-z0-9._-]{1,120}" title="Somente letras, números, ponto, hífen e sublinhado"/>
          <Field name="notes" label="Observações (opcional)" maxLength="500"/></FormSection>
        <FormActions onCancel={() => navigate('atualizacoes')} submitting={busy} submitLabel="Registrar versão"/>
      </form></FormPage>;
    if (segments[0] && ['promover', 'revogar'].includes(segments[1])) {
      const promote = segments[1] === 'promover';
      return <FormPage title={`${promote ? 'Promover' : 'Revogar'} versão${release ? ` ${release.version} (${release.platform})` : ''}`}
        description={promote ? 'Promover libera a versão para todos os workers fora de retenção. Exige ao menos um worker canário online que já aplicou esta versão.'
          : 'Revogar impede novas ofertas e manda os workers que a executam voltarem à última versão estável.'} onBack={() => navigate('atualizacoes')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const reason = new FormData(e.currentTarget).get('reason');
          run(() => call.current(`/api/v1/master/business/releases/${segments[0]}/${promote ? 'promote' : 'revoke'}`, post(promote ? {} : {reason})), () => navigate('atualizacoes')); }}>
          <FormSection title="Confirmação">{promote ? <p>Confirme a promoção. A decisão fica em auditoria.</p>
            : <Field name="reason" label="Motivo (de 4 a 300 caracteres)" required minLength="4" maxLength="300"/>}</FormSection>
          <FormActions onCancel={() => navigate('atualizacoes')} submitting={busy} submitLabel={promote ? 'Promover versão' : 'Revogar versão'}/>
        </form></FormPage>;
    }
    return <ListPage {...header('Atualizações do Worker', 'Versões assinadas do Business Worker. Entram como Canário; só viram Estável depois de aplicadas com sucesso por um worker canário.')}
      actions={<button type="button" className="primary" onClick={() => navigate('atualizacoes/novo')}>+ Registrar versão</button>}
      metrics={<MetricGroup items={[{label: 'Versões', value: busy ? '—' : items.length}, {label: 'Canário', value: items.filter(r => r.channel === 'canary').length},
        {label: 'Estável', value: items.filter(r => r.channel === 'stable').length}, {label: 'Revogadas', value: items.filter(r => r.channel === 'revoked').length}]}/>}
      filters={<FilterBar><label>Pesquisar <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Versão ou arquivo"/></label><button type="button" onClick={load} disabled={busy}>Atualizar</button></FilterBar>}>
      {alerts}
      <DataGrid stateKey={`business:${tenantId}:atualizacoes`} rows={items.filter(r => `${r.version} ${r.file_name}`.toLowerCase().includes(search.toLowerCase()))}
        columns={[{key: 'version', label: 'Versão'}, {key: 'platform', label: 'Plataforma'}, {key: 'channel', label: 'Canal', render: r => channelLabel[r.channel] || r.channel},
          {key: 'size_bytes', label: 'Tamanho', render: r => `${(r.size_bytes / 1048576).toFixed(1)} MB`}, {key: 'sha256', label: 'SHA-256', render: r => <code title={r.sha256}>{String(r.sha256).slice(0, 12)}…</code>},
          {key: 'created_at', label: 'Registrada', render: r => fmt(r.created_at)}]}
        actions={(r) => <>{r.channel === 'canary' && <button type="button" onClick={() => navigate(`atualizacoes/${r.id}/promover`)}>Ativar versão</button>}
          {r.channel !== 'revoked' && <button type="button" onClick={() => navigate(`atualizacoes/${r.id}/revogar`)}>Revogar</button>}</>}/>
    </ListPage>;
  }

  // ------------------------------------------------------------------ Filas (broker and dead letters)
  if (page === 'filas') {
    if (!isMaster) return <section className="panel"><h2>Acesso não autorizado</h2><p>As filas são visíveis ao Master técnico.</p></section>;
    if (segments[0] && ['reprocessar', 'descartar'].includes(segments[1])) {
      const reprocess = segments[1] === 'reprocessar';
      return <FormPage title={reprocess ? 'Reprocessar mensagem morta' : 'Descartar mensagem morta'}
        description={reprocess ? 'Cria uma nova execução com os mesmos dados e remove a mensagem morta. A original fica como evidência.' : 'Remove a mensagem sem executar. A decisão fica em auditoria.'}
        onBack={() => navigate('filas')}>
        {alerts}
        <form className="nexora-form" onSubmit={(e) => { e.preventDefault(); const reason = new FormData(e.currentTarget).get('reason');
          run(() => call.current(`/api/v1/master/business/broker/dead-letters/${segments[0]}/${reprocess ? 'reprocess' : 'discard'}`, post({reason})), () => navigate('filas')); }}>
          <FormSection title="Justificativa"><Field name="execution_id" label="Execução" defaultValue={segments[0]} readOnly/><Field name="reason" label="Motivo (de 4 a 300 caracteres)" required minLength="4" maxLength="300"/></FormSection>
          <FormActions onCancel={() => navigate('filas')} submitting={busy} submitLabel={reprocess ? 'Reprocessar' : 'Descartar'}/>
        </form></FormPage>;
    }
    const broker = aux.broker || {};
    const names = Object.fromEntries((aux.workers || []).map(w => [w.id, w.name]));
    return <ListPage {...header('Filas e mensagens mortas', 'Visão da fila de jobs do broker. Mensagens mortas são execuções que não puderam ser entregues ou lidas.')}
      metrics={<MetricGroup items={[{label: 'Modo', value: broker.mode || '—'}, {label: 'Aguardando', value: broker.pending ?? '—'}, {label: 'Em execução', value: broker.active ?? '—'},
        {label: 'Mais antiga (s)', value: broker.oldest_pending_seconds ?? '—'}, {label: 'Expiradas em 24 h', value: broker.expired_24h ?? '—'}, {label: 'Mensagens mortas', value: broker.dlq_depth ?? (items.length || '—')}]}/>}
      filters={<FilterBar><label>Pesquisar <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Execução"/></label><button type="button" onClick={load} disabled={busy}>Atualizar</button></FilterBar>}>
      {alerts}
      {broker.queues && <p className="bm-context">Filas por worker: {Object.entries(broker.queues).map(([id, n]) => `${names[id] || id}: ${n}`).join(' · ') || '—'}</p>}
      <DataGrid stateKey={`business:${tenantId}:filas`} showEmptyGrid rows={filtered.map(m => ({...m, id: m.execution_id}))} empty="Nenhuma mensagem morta nesta Empresa."
        columns={[{key: 'execution_id', label: 'Execução'}, {key: 'reason', label: 'Motivo'}, {key: 'queue', label: 'Fila de origem'}]}
        actions={(r) => <><button type="button" onClick={() => navigate(`filas/${r.execution_id}/reprocessar`)}>Reprocessar</button>
          <button type="button" onClick={() => navigate(`filas/${r.execution_id}/descartar`)}>Descartar</button></>}/>
    </ListPage>;
  }
  return <p>Página não encontrada.</p>;
}
