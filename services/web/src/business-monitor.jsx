import React, { useEffect, useRef, useState } from 'react';
import './business-monitor.css';
import BusinessP1 from './business-p1.jsx';
import BusinessQueries from './business-queries.jsx';

const ROOT = '/negocios';
const fmt = (v) => v ? new Date(v).toLocaleString('pt-BR') : '—';
const val = (v) => v === null || v === undefined ? '—' : String(v);
const statusLabels = {normal:'Normal',atencao:'Atenção',critico:'Crítico',sem_dados:'Sem dados',suspenso:'Suspenso'};
const routeName = () => window.location.pathname.split('/')[2] || 'visao-geral';
const has = (perms, cap) => perms.includes(cap);

/** Master-only bootstrap: no screen can be reachable before this, since the UI itself
 * requires a grant to render (no implicit access, not even for the technical Master). */
function BusinessBootstrap({tenantId, request, access, isMaster, onDone}) {
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [users,setUsers]=useState([]);
  const [admins,setAdmins]=useState([]);
  const loadAdmins=()=>request('/api/v1/business/grants').then(rows=>setAdmins(rows.filter(r=>r.capability==='business.grants.manage'&&r.active))).catch(()=>setAdmins([]));
  useEffect(()=>{ if(access.enabled){ request('/api/v1/users').then(setUsers).catch(()=>setUsers([])); loadAdmins(); } },[access.enabled,tenantId]);
  if (!isMaster) return <div className="panel"><h2>Acesso não autorizado</h2><p>Este usuário não possui concessão para Monitoramento de Negócios nesta Empresa.</p></div>;
  const enable=async(event)=>{
    event.preventDefault();setBusy(true);setMessage('');
    const reason=new FormData(event.currentTarget).get('reason');
    try{ await request('/api/v1/business/enable',{method:'PUT',body:JSON.stringify({enabled:true,reason})}); await onDone(); }
    catch(e){setMessage(e.message||'Falha ao ativar');}finally{setBusy(false);}
  };
  const grant=async(event)=>{
    event.preventDefault();setBusy(true);setMessage('');
    const form=new FormData(event.currentTarget);
    try{
      await request('/api/v1/business/grants',{method:'PUT',body:JSON.stringify({
        user_id:form.get('user_id'),capability:'business.grants.manage',scope_type:'company',
        active:true,reason:form.get('reason')})});
      await loadAdmins();
      setMessage('ok');
      await onDone();
    }catch(e){setMessage(e.message||'Falha ao conceder acesso');}finally{setBusy(false);}
  };
  return <section className="panel">
    <h2>Ativação do Monitoramento de Negócios</h2>
    <p>Produto independente, sem acesso implícito: o Master técnico pode ativar e indicar o primeiro administrador, mas não pode conceder acesso a si mesmo.</p>
    {access.enabled && <ol className="bm-checklist" aria-label="Primeiro acesso">
      <li className="done">Produto ativado para esta Empresa.</li>
      <li className={admins.length?'done':'todo'}>{admins.length?`Administrador de negócios designado: ${admins.map(a=>a.display_name).join(', ')}.`:'Designe um administrador de negócios (formulário abaixo).'}</li>
      <li className={['business.view','business.monitor.create','business.monitor.update'].every(c=>(access.capabilities||[]).includes(c))?'done':'todo'}>
        O Master precisa receber, de outro usuário, as permissões de visualizar e de criar/editar monitoramentos
        ({['business.view','business.monitor.create','business.monitor.update'].filter(c=>!(access.capabilities||[]).includes(c)).join(', ')||'concedidas'}). O Master não pode concedê-las a si mesmo.</li>
    </ol>}
    {message==='ok' && <div role="status" className="info-banner">Administração concedida. Peça ao usuário indicado para entrar no Nexora: ele verá a tela de Permissões e poderá conceder acesso a você.</div>}
    {message && message!=='ok' && <div role="alert" className="bm-error">{message}</div>}
    {!access.enabled ? (
      <form className="nexora-form" onSubmit={enable}>
        <label className="field"><span>Motivo da ativação</span><input name="reason" required minLength="4" maxLength="500" placeholder="Ex.: piloto comercial aprovado"/></label>
        <div className="form-actions"><button className="primary" disabled={busy}>{busy?'Ativando...':'Ativar para esta Empresa'}</button></div>
      </form>
    ) : (
      <>
        <p className="info-banner">Produto já ativado para esta Empresa. Indique um usuário para ser o primeiro administrador de negócios (ele poderá conceder acesso a outros, inclusive a você).</p>
        <form className="nexora-form bm-bootstrap-form" onSubmit={grant}>
          <label className="field"><span>Usuário</span><select name="user_id" required defaultValue="">
            <option value="" disabled>Selecione</option>
            {users.map(u=><option key={u.id} value={u.id}>{u.display_name} — {u.email}</option>)}
          </select></label>
          <label className="field"><span>Motivo</span><input name="reason" required minLength="4" maxLength="500" placeholder="Ex.: responsável pelo módulo de negócios"/></label>
          <div className="form-actions"><button className="primary" disabled={busy || !users.length}>{busy?'Concedendo...':'Conceder administração de negócios'}</button></div>
        </form>
        {admins.length>0 && <div className="bm-admins"><h3>Administradores de negócios desta Empresa</h3>
          <ul>{admins.map(a=><li key={a.user_id}><strong>{a.display_name}</strong> — {a.email} <small>(desde {fmt(a.granted_at)})</small></li>)}</ul></div>}
      </>
    )}
  </section>;
}

/** Lets the first business administrator hand the technical Master the standard access in one step (3 individual grants).
 *  The backend still authorizes every grant: self-grant and non-administrators are refused there. */
function MasterQuickGrant({request, ui}) {
  const [master,setMaster]=useState(null),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[failed,setFailed]=useState(false);
  useEffect(()=>{request('/api/v1/business/eligible-users').then(rows=>setMaster(rows.find(r=>r.is_master)||null)).catch(()=>setMaster(null));},[]);
  if(!master) return null;
  const grant=async()=>{
    setBusy(true);setMessage('');setFailed(false);
    try{
      for(const capability of ['business.view','business.monitor.create','business.monitor.update'])
        await request('/api/v1/business/grants-scoped',{method:'PUT',body:JSON.stringify({user_id:master.id,capability,scope_type:'company',scope_id:null,active:true,
          reason:'Acesso padrão do Master técnico ao módulo de negócios'})});
      setMessage(`Acesso padrão concedido a ${master.display_name}.`);
    }catch(e){setFailed(true);setMessage(e.message||'Falha ao conceder');}finally{setBusy(false);}
  };
  return <section className="bm-glpi-form" aria-labelledby="bm-quick-grant">
    <h3 id="bm-quick-grant">Acesso padrão do Master técnico</h3>
    <p>Concede a <strong>{master.display_name}</strong> visualizar e criar/editar monitoramentos desta Empresa (escopo Empresa). Cada concessão fica em auditoria.</p>
    {message&&<p role={failed?'alert':'status'} className={failed?'bm-error':'info-banner'}>{message}</p>}
    <button type="button" className="primary" disabled={busy} onClick={grant}>{busy?'Concedendo...':'Conceder acesso padrão ao Master'}</button>
  </section>;
}

export default function BusinessMonitor({tenantId, request, access, isMaster, onAccessChange, ui}) {
  const {PageHeader, MetricGroup, ListPage, FilterBar, DataGrid, FormPage, FormSection, FormActions} = ui;
  const [page, setPage] = useState(routeName());
  const [indicators,setIndicators]=useState([]);
  const [health,setHealth]=useState(null);
  const [occurrences,setOccurrences]=useState([]);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [search,setSearch]=useState('');
  const [editing,setEditing]=useState(null);
  const requestRef=useRef(request); requestRef.current=request;
  const sequence=useRef(0);
  const [,setRouteTick]=useState(0);
  const navigate=(next) => {
    const target = `${ROOT}/${next}`;
    if (window.location.pathname !== target) window.history.pushState({},'',target);
    setPage(routeName());setMessage('');setRouteTick(t=>t+1);
  };
  const reload = async () => {
    const id=++sequence.current;
    setBusy(true);setMessage('');
    try {
      const [items,summary,events]=await Promise.all([
        requestRef.current('/api/v1/business/indicators'),
        requestRef.current('/api/v1/business/health'),
        requestRef.current('/api/v1/business/occurrences'),
      ]);
      if (id===sequence.current) {setIndicators(items);setHealth(summary);setOccurrences(events);}
    } catch (e) {if (id===sequence.current) setMessage(e.message||'Falha ao consultar dados de negócio');}
    finally {if (id===sequence.current) setBusy(false);}
  };
  useEffect(() => { if (tenantId && has(access.capabilities||[],'business.view')) reload();
    else {sequence.current++;setIndicators([]);setHealth(null);setOccurrences([]);} },[tenantId,access.capabilities?.join('|')]);
  useEffect(() => {const pop=()=>{setPage(routeName());setRouteTick(t=>t+1);};window.addEventListener('popstate',pop);return ()=>window.removeEventListener('popstate',pop);},[]);
  const allowed=access.capabilities||[];
  const governance=access.enabled&&isMaster;
  const queryPages=['consultas','workers','fontes','filas','atualizacoes'];
  if (governance && !has(allowed,'business.view')) {
    // The technical Master governs queries but holds no business data access: only governance pages, never indicators.
    const tabs=[['acesso','Primeiro acesso'],['consultas','Consultas'],['workers','Workers'],['fontes','Fontes'],['filas','Filas'],['atualizacoes','Atualizações'],
      ...(has(allowed,'business.grants.manage')?[['permissoes','Permissões']]:[])];
    const current=tabs.some(([key])=>key===page)?page:'consultas';
    return <div className="nexora-business">
      <nav className="bm-tabs" aria-label="Governança de consultas do Monitoramento de Negócios">{tabs.map(([key,text])=>
        <button key={key} type="button" aria-current={current===key?'page':undefined} className={current===key?'active':''} onClick={()=>navigate(key)}>{text}</button>)}</nav>
      {current==='acesso'?<BusinessBootstrap tenantId={tenantId} request={requestRef.current} access={access} isMaster={isMaster} onDone={onAccessChange}/>
        :current==='permissoes'?<BusinessP1 page="permissoes" tenantId={tenantId} access={access} request={requestRef.current} ui={ui}/>
        :<BusinessQueries page={current} tenantId={tenantId} request={requestRef.current} access={access} isMaster={isMaster} ui={ui} navigate={navigate}/>}
    </div>;
  }
  // A grant administrator without 'business.view' (e.g. the first administrator designated by the Master) can only
  // manage permissions: show that page alone, so the grant chain (Master -> administrator -> Master) is reachable.
  if (access.enabled && has(allowed,'business.grants.manage') && !has(allowed,'business.view'))
    return <div className="nexora-business">
      <p className="info-banner">Você administra os acessos do Monitoramento de Negócios desta Empresa. Conceda as permissões necessárias aos usuários; as demais telas dependem da permissão de visualização.</p>
      <MasterQuickGrant request={requestRef.current} />
      <BusinessP1 page="permissoes" tenantId={tenantId} access={access} request={requestRef.current} ui={ui} />
    </div>;
  if (!access.enabled || !has(allowed,'business.view'))
    return <BusinessBootstrap tenantId={tenantId} request={requestRef.current} access={access} isMaster={isMaster} onDone={onAccessChange} />;
  const openNew=()=>{setEditing(null);navigate('monitoramentos/novo');};
  const openEdit=(i)=>{setEditing(i);navigate(`monitoramentos/${i.id}/editar`);};
  const editId=window.location.pathname.match(/^\/negocios\/monitoramentos\/([^/]+)\/editar$/)?.[1];
  const indicator=editId ? indicators.find(x=>String(x.id)===decodeURIComponent(editId)) : null;
  const isForm=window.location.pathname==='/negocios/monitoramentos/novo'||Boolean(editId);
  const editable=Boolean(editId);
  const canWrite=has(allowed,editable?'business.monitor.update':'business.monitor.create');
  const save=async(ev)=>{
    ev.preventDefault();if(!canWrite||busy)return;
    const fd=new FormData(ev.currentTarget);
    const data={code:String(fd.get('code')).trim(),name:String(fd.get('name')).trim(),
      description:String(fd.get('description')||''),unit:String(fd.get('unit')||'unidades'),direction:String(fd.get('direction')||'above'),
      warning_value:fd.get('warning_value')!==''?String(fd.get('warning_value')):null,
      critical_value:fd.get('critical_value')!==''?String(fd.get('critical_value')):null,
      stale_after_seconds:Number(fd.get('stale_after_seconds'))};
    if(editable)data.version=indicator.version,data.active=fd.get('active')==='on';
    setBusy(true);setMessage('');
    try {
      await requestRef.current(editable?`/api/v1/business/indicators/${indicator.id}`:'/api/v1/business/indicators',
        {method:editable?'PUT':'POST',body:JSON.stringify(data)});
      navigate('monitoramentos');await reload();
    }catch(e){setMessage(e.message||'Falha ao salvar monitoramento');setBusy(false);}
  };
  const filtered=indicators.filter(x=>`${x.name} ${x.code}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="nexora-business">
    {!isForm && <nav className="bm-tabs" aria-label="Navegação do Monitoramento de Negócios">
      {[['visao-geral','Visão Geral'],['monitoramentos','Monitoramentos'],['ocorrencias','Ocorrências'],['processos','Processos'],['regras','Regras'],['conectores','Conectores'],['unidades','Unidades'],['notificacoes','Notificações'],['relatorios','Relatórios'],['consultas','Consultas'],['workers','Workers'],['fontes','Fontes'],['filas','Filas'],['atualizacoes','Atualizações'],['permissoes','Permissões']].filter(([key])=>!['workers','fontes','filas','atualizacoes'].includes(key)||isMaster).filter(([key])=>key!=='conectores'||has(allowed,'business.source.manage')).filter(([key])=>key!=='relatorios'||has(allowed,'business.export')).filter(([key])=>key!=='permissoes'||has(allowed,'business.grants.manage')).filter(([key])=>key!=='regras'||has(allowed,'business.view')).map(([key,label])=><button key={key} type="button" aria-current={page===key?'page':undefined} className={page===key?'active':''} onClick={()=>navigate(key)}>{label}</button>)}
    </nav>}
    {message==='ok' && <div role="status" className="info-banner">Administração concedida. Peça ao usuário indicado para entrar no Nexora: ele verá a tela de Permissões e poderá conceder acesso a você.</div>}
    {message && message!=='ok' && <div role="alert" className="bm-error">{message}</div>}
    {busy && <p role="status">Carregando ou salvando informações...</p>}
    {isForm ? (
      canWrite ? <FormPage title={editable?'Editar monitoramento':'Novo monitoramento de negócio'} description="Informações e limites serão avaliados apenas após uma fonte autorizada enviar dados reais." onBack={()=>navigate('monitoramentos')}>
        {(!editable||indicator) ? <form onSubmit={save} className="nexora-form" key={indicator?.id||'new'}>
          <FormSection title="Identificação do indicador">
            <label className="field"><span>Código</span><input name="code" pattern="[a-z][a-z0-9_]{2,95}" required defaultValue={indicator?.code||''}/></label>
            <label className="field"><span>Nome</span><input name="name" minLength="3" maxLength="180" required defaultValue={indicator?.name||''}/></label>
            <label className="field"><span>Descrição</span><input name="description" maxLength="3000" defaultValue={indicator?.description||''}/></label>
            <label className="field"><span>Unidade</span><input name="unit" required defaultValue={indicator?.unit||'unidades'}/></label>
          </FormSection>
          <FormSection title="Regra de limites">
            <label className="field"><span>Direção de risco</span><select name="direction" defaultValue={indicator?.direction||'above'}><option value="above">Acima do limite</option><option value="below">Abaixo do limite</option></select></label>
            <label className="field"><span>Início de atenção</span><input name="warning_value" inputMode="decimal" type="number" step="any" defaultValue={indicator?.warning_value??''}/></label>
            <label className="field"><span>Limite crítico</span><input name="critical_value" inputMode="decimal" type="number" step="any" defaultValue={indicator?.critical_value??''}/></label>
            <label className="field"><span>Validade da coleta (segundos)</span><input name="stale_after_seconds" type="number" min="30" max="2592000" required defaultValue={indicator?.stale_after_seconds||1800}/></label>
            {editable&&<label className="field"><span><input type="checkbox" name="active" defaultChecked={indicator?.active}/> Monitoramento ativo</span></label>}
          </FormSection>
          <FormActions onCancel={()=>navigate('monitoramentos')} submitting={busy} submitLabel={editable?'Salvar alterações':'Criar monitoramento'}/>
        </form> : <p>Registro não encontrado ou acesso insuficiente.</p>}
      </FormPage> : <p>Acesso negado para criar ou editar monitoramentos.</p>
    ) : queryPages.includes(page) ? <BusinessQueries page={page} tenantId={tenantId} request={requestRef.current} access={access} isMaster={isMaster} ui={ui} navigate={navigate}/> : ['processos','regras','conectores','unidades','notificacoes','relatorios','permissoes'].includes(page) ? <BusinessP1 page={page} tenantId={tenantId} access={access} request={requestRef.current} ui={ui} /> : page==='monitoramentos' ? <ListPage title="Monitoramentos de Negócios" eyebrow="NEXORA › BUSINESS MONITOR" description="Indicadores independentes de infraestrutura, com permissão por Empresa." actions={has(allowed,'business.monitor.create')?<button type="button" className="primary" onClick={openNew}>+ Novo monitoramento</button>:null}
      metrics={<MetricGroup items={[{label:'Cadastrados',value:indicators.length},{label:'Críticos',value:health?.counts?.critico??'—'},{label:'Sem dados',value:health?.counts?.sem_dados??'—'}]}/>}
      filters={<FilterBar><label>Pesquisar <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Código ou nome" /></label><button type="button" onClick={reload}>Atualizar</button></FilterBar>}>
      <DataGrid rows={filtered} columns={[{key:'name',label:'Indicador'},{key:'code',label:'Código'},{key:'unit',label:'Unidade'},
        {key:'state',label:'Situação',render:r=><span className={`bm-status ${r.state}`}>{statusLabels[r.state]||'—'}</span>},
        {key:'last_value',label:'Último valor',render:r=>val(r.last_value)},{key:'last_collected_at',label:'Coletado em',render:r=>fmt(r.last_collected_at)},
        {key:'actions',label:'Ações',render:r=>has(allowed,'business.monitor.update')?<button type="button" onClick={()=>openEdit(r)}>Editar</button>:'—'}]}/>
    </ListPage> : page==='ocorrencias' ? <ListPage title="Ocorrências de Negócios" eyebrow="NEXORA › BUSINESS MONITOR" description="Ocorrências operacionais são independentes dos chamados técnicos." actions={<button onClick={reload}>Atualizar</button>}
      metrics={<MetricGroup items={[{label:'Ocorrências',value:occurrences.length},{label:'Em acompanhamento',value:occurrences.filter(o=>['open','acknowledged'].includes(o.state)).length}]}/>}
      filters={null}>
      <DataGrid rows={occurrences} columns={[{key:'indicator_name',label:'Indicador'},{key:'severity',label:'Severidade'},{key:'state',label:'Situação'},
        {key:'opened_at',label:'Abertura',render:r=>fmt(r.opened_at)},
        {key:'glpi',label:'Chamado GLPI (Negócio)',render:r=>r.glpi_ticket_id?`#${r.glpi_ticket_id}`:
          ({pending:'Aguardando GLPI',failed:'Falha na sincronização'}[r.glpi_sync_status]||'—')},
        {key:'actions',label:'Ação',render:r=>has(allowed,'business.occurrence.manage')&&['open','acknowledged','normalized'].includes(r.state)?
          <button type="button" onClick={async()=>{setBusy(true);try{await requestRef.current(`/api/v1/business/occurrences/${r.id}/actions`,{method:'POST',body:JSON.stringify({action:r.state==='open'?'acknowledge':'close'})});await reload();}catch(e){setMessage(e.message);setBusy(false);}}}>{r.state==='open'?'Reconhecer':'Encerrar'}</button>:'—'}]}/>
    </ListPage> : <section className="panel list-page">
      <PageHeader eyebrow="NEXORA › BUSINESS MONITOR" title="Visão Geral de Negócios" description="Indicadores reais e autorizados da Empresa selecionada." actions={<button onClick={reload}>Atualizar</button>}/>
      <MetricGroup items={[{label:'Indicadores',value:health?.total??'—'},
        {label:'Normais',value:health?.counts?.normal??'—'},{label:'Em atenção',value:health?.counts?.atencao??'—'},
        {label:'Críticos',value:health?.counts?.critico??'—'},{label:'Sem dados',value:health?.counts?.sem_dados??'—'}]}/>
      <p className="bm-context">Exposição financeira: <b>Não calculada</b> · Última avaliação: {fmt(health?.as_of)}</p>
      <div className="bm-cards">{indicators.map(i=><article className="bm-card" key={i.id}><div><span className={`bm-status ${i.state}`}>{statusLabels[i.state]||'—'}</span><h3>{i.name}</h3><p>{i.code}</p></div><strong>{val(i.last_value)} <small>{i.unit}</small></strong><small>Coleta: {fmt(i.last_collected_at)}</small></article>)}</div>
      {!indicators.length&&!busy&&<p>Nenhum indicador de negócio cadastrado. Não foram criados dados demonstrativos.</p>}
    </section>}
  </div>;
}
