import React,{useEffect,useRef,useState} from 'react';

const TABS={unidades:'Unidades',processos:'Processos',regras:'Regras de monitoramento',
  conectores:'Hub Studio · Conectores',notificacoes:'Notificações',relatorios:'Relatórios',permissoes:'Acessos de Negócios'};
const fmt=v=>v?new Date(v).toLocaleString('pt-BR'):'—';
const yes=(permissions,key)=>permissions.includes(key);
function Selector({name,label,rows,value,required=false}){return <label className="field"><span>{label}</span><select name={name} required={required} defaultValue={value||''}><option value="">Selecione</option>{rows.map(x=><option key={x.id} value={x.id}>{x.name||x.display_name||x.email}</option>)}</select></label>}
function Field({name,label,defaultValue='',required=false,type='text'}){return <label className="field"><span>{label}</span><input name={name} type={type} required={required} defaultValue={defaultValue}/></label>}
const postBody=(form)=>Object.fromEntries(new FormData(form).entries());
const number=(v, fallback)=>v===''||v===null||v===undefined?fallback:Number(v);
const apiBody=(method,data)=>({method,body:JSON.stringify(data)});

/** Separate P1 business pages; never mixes Infrastructure Monitor authorization. */
export default function BusinessP1({page,tenantId,access,request,ui}){
  const {ListPage,MetricGroup,FilterBar,DataGrid,FormPage,FormSection,FormActions}=ui;
  const [items,setItems]=useState([]),[aux,setAux]=useState([]),[indicators,setIndicators]=useState([]),
    [hubs,setHubs]=useState([]),[users,setUsers]=useState([]),[grants,setGrants]=useState([]),
    [selected,setSelected]=useState(null),[steps,setSteps]=useState([]),[form,setForm]=useState(''),
    [busy,setBusy]=useState(false),[error,setError]=useState(''),[updated,setUpdated]=useState(null),
    [glpi,setGlpi]=useState(null);
  const call=useRef(request);call.current=request;
  const perms=access.capabilities||[];
  const endpoint={unidades:'units',processos:'processes',regras:'rules',conectores:'collectors',
    notificacoes:'notifications',relatorios:'reports/indicators',permissoes:'grants'}[page];
  const refresh=async()=>{
    setError('');setBusy(true);
    try{
      const data=await call.current(`/api/v1/business/${endpoint}`);
      setItems(Array.isArray(data)?data:data.rows||[]);
      const extras=[];
      if(['regras','conectores','notificacoes','relatorios'].includes(page))extras.push(call.current('/api/v1/business/indicators'));
      else extras.push(Promise.resolve([]));
      if(['processos','conectores','permissoes'].includes(page))extras.push(call.current('/api/v1/business/units'));
      else extras.push(Promise.resolve([]));
      if(page==='conectores')extras.push(call.current('/api/v1/business/hubs'));
      else extras.push(Promise.resolve([]));
      if(page==='permissoes')extras.push(call.current('/api/v1/business/eligible-users'));
      else extras.push(Promise.resolve([]));
      const [inds,units,agents,eligible]=await Promise.all(extras);
      setIndicators(inds);setAux(units);setHubs(agents);setUsers(eligible);
      // GLPI panel is only for company-wide source managers; any refusal just hides it.
      if(page==='conectores')setGlpi(await call.current('/api/v1/business/glpi').catch(()=>null));
      setUpdated(new Date());
    }catch(e){setError(e.message||'Falha ao carregar informações');}
    finally{setBusy(false)}
  };
  useEffect(()=>{
    const path=window.location.pathname;
    const create=path===`/negocios/${page}/novo`;
    const match=path.match(new RegExp(`^/negocios/${page}/([^/]+)/(editar|etapas)$`));
    setForm(create?'new':match?(match[2]==='etapas'?'steps':'edit'):'');
    setSelected(null);
    refresh().then(async()=>{if(match){
      const list=await call.current(`/api/v1/business/${endpoint}`);
      const rows=Array.isArray(list)?list:list.rows||[];
      const row=rows.find(x=>String(x.id)===decodeURIComponent(match[1]));
      if(row){setSelected(row);if(match[2]==='etapas'){
        const data=await call.current(`/api/v1/business/processes/${row.id}/steps`);
        setSteps(data);
      }}
    }}).catch(e=>setError(e.message));
  },[page,tenantId]);
  const route=(mode,item=null)=>{
    setError('');setForm(mode);setSelected(item);
    const suffix=mode==='new'?'novo':mode==='edit'?`${item.id}/editar`:mode==='steps'?`${item.id}/etapas`:'visao-geral';
    window.history.pushState({},'',`/negocios/${page}/${suffix}`);
  };
  const back=()=>{setForm('');setSelected(null);window.history.pushState({},'',`/negocios/${page}`);refresh();};
  const run=async(fn)=>{setBusy(true);setError('');try{await fn();await back();}catch(e){setError(e.message||'Falha na operação');}finally{setBusy(false);}};
  const canCreate=page==='unidades'||page==='processos'?yes(perms,'business.process.manage'):
    page==='regras'?yes(perms,'business.rule.manage'):
    page==='conectores'?yes(perms,'business.source.manage'):
    page==='permissoes'?yes(perms,'business.grants.manage'):false;
  const openSteps=async(row)=>{route('steps',row);try{setSteps(await call.current(`/api/v1/business/processes/${row.id}/steps`));}catch(e){setError(e.message);}};
  const submit=async(event)=>{
    event.preventDefault();const data=postBody(event.currentTarget);
    if(page==='unidades')return run(()=>call.current('/api/v1/business/units',apiBody('POST',{name:data.name})));
    if(page==='processos')return run(()=>call.current('/api/v1/business/processes',apiBody('POST',{
      name:data.name,description:data.description,unit_id:data.unit_id||null,department_id:data.department_id||null
    })));
    if(page==='regras')return run(()=>call.current('/api/v1/business/rules',apiBody('PUT',{
      indicator_id:data.indicator_id,direction:data.direction,
      warning_value:data.warning_value===''?null:data.warning_value,
      critical_value:data.critical_value===''?null:data.critical_value,
      hysteresis:data.hysteresis||'0',cooldown_seconds:number(data.cooldown_seconds,0),
      stale_after_seconds:number(data.stale_after_seconds,1800),
      timezone:data.timezone||'America/Sao_Paulo',
      // Preserve the existing schedule when editing; never reset it silently.
      weekdays:Array.isArray(selected?.weekdays)&&selected.weekdays.length?selected.weekdays:[1,2,3,4,5,6,7],
      check_from:selected?.check_from?String(selected.check_from).slice(0,5):null,
      check_until:selected?.check_until?String(selected.check_until).slice(0,5):null,
      expected_version:selected?.version||null
    })));
    if(page==='conectores'){
      let config={poll_seconds:number(data.poll_seconds,300)};
      if(['sql_view','postgres_view'].includes(data.kind))config={...config,schema:data.schema,view:data.view,column:data.column};
      else if(data.kind==='rest_get')config={...config,url:data.url,json_property:data.json_property,max_bytes:65536};
      else if(data.kind==='csv')config={...config,path:data.path,column:data.column};
      else config={...config,path:data.path,sheet:data.sheet,cell:data.cell};
      return run(()=>call.current('/api/v1/business/collectors',apiBody('POST',{
        agent_id:data.agent_id,indicator_id:data.indicator_id,kind:data.kind,name:data.name,config
      })));
    }
    if(page==='permissoes')return run(()=>call.current('/api/v1/business/grants-scoped',apiBody('PUT',{
      user_id:data.user_id,capability:data.capability,scope_type:data.scope_type,
      scope_id:['company','own'].includes(data.scope_type)?null:(data.scope_id||null),
      active:data.active==='on',reason:data.reason
    })));
  };
  const props={submitting:busy,onCancel:back};
  if(form==='steps'&&page==='processos')return <section className="panel list-page">
    <header className="section-head"><div><h2>Etapas — {selected?.name}</h2><p>Dependências e progresso do processo.</p></div><button onClick={back}>Voltar aos processos</button></header>
    {error&&<p role="alert" className="bm-error">{error}</p>}
    <DataGrid rows={steps} columns={[{key:'ordinal',label:'Ordem'},{key:'name',label:'Etapa'},
      {key:'state',label:'Estado'},{key:'due_at',label:'Prazo',render:x=>fmt(x.due_at)},
      {key:'actions',label:'Ações',render:x=>yes(perms,'business.process.manage')&&['pending','running','blocked'].includes(x.state)?
        <button type="button" onClick={()=>run(async()=>{await call.current(`/api/v1/business/steps/${x.id}/state`,apiBody('POST',{
          expected_state:x.state,state:x.state==='pending'?'running':'done'}));})}>{x.state==='pending'?'Iniciar':'Concluir'}</button>:null}]}/>
    {yes(perms,'business.process.manage')&&<form className="nexora-form" onSubmit={e=>{e.preventDefault();const d=postBody(e.currentTarget);run(()=>call.current(`/api/v1/business/processes/${selected.id}/steps`,apiBody('POST',{
       step_code:d.step_code,name:d.name,ordinal:Number(d.ordinal),predecessor_id:d.predecessor_id||null
    })));}}><FormSection title="Nova etapa"><Field name="step_code" label="Código" required/>
      <Field name="name" label="Nome da etapa" required/><Field name="ordinal" label="Ordem" type="number" required/>
      <Selector name="predecessor_id" label="Etapa anterior (opcional)" rows={steps}/></FormSection>
      <FormActions {...props} submitLabel="Criar etapa" /></form>}
  </section>;
  if(form==='new'||form==='edit')return <FormPage title={`${form==='edit'?'Editar':'Novo'} — ${TABS[page]}`} description="Cadastro próprio de Monitoramento de Negócios" onBack={back}>
    {error&&<p role="alert" className="bm-error">{error}</p>}
    <form className="nexora-form" onSubmit={submit}>
      {page==='unidades'&&<FormSection title="Unidade"><Field name="name" label="Nome da unidade" required/></FormSection>}
      {page==='processos'&&<FormSection title="Processo"><Field name="name" label="Nome" required/>
        <Field name="description" label="Descrição"/><Selector name="unit_id" label="Unidade" rows={aux}/>
        <Field name="department_id" label="Departamento (UUID autorizado, opcional)"/></FormSection>}
      {page==='regras'&&<FormSection title="Política de monitoramento">
        <Selector name="indicator_id" label="Indicador" rows={indicators} value={selected?.indicator_id} required/>
        <label className="field"><span>Direção</span><select name="direction" defaultValue={selected?.direction||'above'}>
          <option value="above">Acima do limite</option><option value="below">Abaixo do limite</option></select></label>
        <Field name="warning_value" label="Limite de atenção" defaultValue={selected?.warning_value||''}/>
        <Field name="critical_value" label="Limite crítico" defaultValue={selected?.critical_value||''}/>
        <Field name="hysteresis" label="Histerese" defaultValue={selected?.hysteresis||'0'}/>
        <Field name="cooldown_seconds" label="Cooldown em segundos" type="number" defaultValue={selected?.cooldown_seconds||0}/>
        <Field name="stale_after_seconds" label="Validade (segundos)" type="number" defaultValue={selected?.stale_after_seconds||1800}/>
        <Field name="timezone" label="Fuso horário" defaultValue={selected?.timezone||'America/Sao_Paulo'}/></FormSection>}
      {page==='conectores'&&<><FormSection title="Origem e destino">
        <Field name="name" label="Nome da coleta" required/>
        <Selector name="agent_id" label="Nexora HUB" rows={hubs} required/>
        <Selector name="indicator_id" label="Indicador" rows={indicators} required/>
        <label className="field"><span>Conector</span><select name="kind" defaultValue="sql_view">
          <option value="sql_view">SQL Server — view</option><option value="postgres_view">PostgreSQL — view</option>
          <option value="rest_get">REST HTTPS — GET</option><option value="csv">CSV</option><option value="xlsx">Excel XLSX</option></select></label>
        <Field name="poll_seconds" label="Intervalo (segundos)" defaultValue="300" type="number" required/>
      </FormSection><FormSection title="Configuração do conector">
        <p className="bm-context">Preencha apenas os campos aplicáveis. As credenciais permanecem no HUB local.</p>
        <Field name="schema" label="Schema (SQL)" defaultValue="dbo"/><Field name="view" label="View autorizada (vw_nexora_*)"/>
        <Field name="column" label="Coluna numérica (SQL/CSV)"/><Field name="url" label="URL HTTPS (REST)"/>
        <Field name="json_property" label="Campo JSON (REST)"/><Field name="path" label="Arquivo relativo (CSV/XLSX)"/>
        <Field name="sheet" label="Aba (XLSX)"/><Field name="cell" label="Célula (XLSX)"/>
      </FormSection></>}
      {page==='permissoes'&&<FormSection title="Concessão individual">
        <Selector name="user_id" label="Usuário" rows={users} required/>
        <label className="field"><span>Permissão</span><select name="capability">
          {['business.view','business.monitor.create','business.monitor.update','business.monitor.inactivate',
            'business.occurrence.manage','business.source.manage','business.rule.manage','business.process.manage',
            'business.collector.test','business.collector.publish','business.export','business.notifications.manage',
            'business.grants.manage'].map(c=><option key={c}>{c}</option>)}
        </select></label>
        <label className="field"><span>Escopo</span><select name="scope_type">
          {['company','own','unit','department','process'].map(c=><option key={c}>{c}</option>)}</select></label>
        <Field name="scope_id" label="UUID do escopo (para unidade, departamento ou processo)"/>
        <Field name="reason" label="Motivo da concessão" required/>
        <label className="field"><span><input type="checkbox" name="active" defaultChecked/> Concessão ativa</span></label>
      </FormSection>}
      <FormActions {...props} submitLabel="Salvar"/>
    </form>
  </FormPage>;
  const columns=({
    unidades:[{key:'name',label:'Unidade'},{key:'active',label:'Situação',render:x=>x.active?'Ativa':'Inativa'}],
    processos:[{key:'name',label:'Processo'},{key:'unit_name',label:'Unidade'},
      {key:'department_name',label:'Área'},{key:'step_count',label:'Etapas'},
      {key:'actions',label:'Ações',render:r=><button onClick={()=>openSteps(r)}>Ver etapas</button>}],
    regras:[{key:'indicator_name',label:'Indicador'},{key:'direction',label:'Direção'},
      {key:'warning_value',label:'Atenção'},{key:'critical_value',label:'Crítico'},{key:'version',label:'Versão'},
      {key:'actions',label:'Ação',render:r=>yes(perms,'business.rule.manage')?<button onClick={()=>route('edit',r)}>Editar regra</button>:'—'}],
    conectores:[{key:'name',label:'Coleta'},{key:'kind',label:'Tipo'},
      {key:'indicator_name',label:'Indicador'},{key:'state',label:'Estado'},
      {key:'current_revision',label:'Versão'},{key:'actions',label:'Ações',render:r=><div className="bm-row-actions">
      {yes(perms,'business.collector.test')&&<button onClick={()=>run(()=>call.current(`/api/v1/business/collectors/${r.id}/test`,apiBody('POST',{})))}>Validar</button>}
      {yes(perms,'business.collector.publish')&&<button onClick={()=>run(()=>call.current(`/api/v1/business/collectors/${r.id}/publish`,apiBody('POST',
        {expected_revision:r.current_revision,reason:'Publicação técnica aprovada'})))}>Publicar</button>}
      {yes(perms,'business.collector.publish')&&r.current_revision>1&&<button onClick={()=>run(()=>call.current(`/api/v1/business/collectors/${r.id}/rollback`,apiBody('POST',
        {expected_revision:r.current_revision,reason:'Rollback técnico aprovado'})))}>Reverter</button>}
      </div>}],
    notificacoes:[{key:'title',label:'Evento'},{key:'message',label:'Mensagem'},
      {key:'created_at',label:'Data',render:x=>fmt(x.created_at)}],
    relatorios:[{key:'name',label:'Indicador'},{key:'code',label:'Código'},{key:'state',label:'Estado'},
      {key:'last_value',label:'Último valor'},{key:'last_collected_at',label:'Última coleta',render:x=>fmt(x.last_collected_at)}],
    permissoes:[{key:'display_name',label:'Usuário'},{key:'capability',label:'Permissão'},
      {key:'scope_type',label:'Escopo'},{key:'active',label:'Ativa',render:x=>x.active?'Sim':'Não'}]
  })[page]||[];
  return <ListPage title={TABS[page]||page} eyebrow="NEXORA › MONITORAMENTO DE NEGÓCIOS"
     description="Dados próprios deste módulo, filtrados por Empresa e permissões."
     actions={canCreate?<button className="primary" onClick={()=>route('new')}>+ Novo</button>:null}
     metrics={<MetricGroup items={[{label:'Registros autorizados',value:busy?'—':items.length},
       {label:'Atualizado',value:fmt(updated)}]}/>}
     filters={<FilterBar><button type="button" onClick={refresh} disabled={busy}>Atualizar</button></FilterBar>}>
     {error&&<p role="alert" className="bm-error">{error}</p>}
     {busy&&<p role="status">Carregando...</p>}
     {page==='conectores'&&glpi&&<form className="bm-glpi-form" aria-labelledby="bm-glpi-title" onSubmit={e=>{
       e.preventDefault();const reason=new FormData(e.currentTarget).get('reason');
       setBusy(true);setError('');
       call.current('/api/v1/business/glpi',apiBody('PUT',{enabled:!glpi.enabled,reason}))
         .then(()=>refresh()).catch(err=>setError(err.message||'Falha ao alterar a integração com o GLPI'))
         .finally(()=>setBusy(false));
     }}>
       <h3 id="bm-glpi-title">Integração com o GLPI — categoria “{glpi.category}”</h3>
       <p>{glpi.enabled?'Ativa: ocorrências de negócio abrem chamados na categoria separada do GLPI. Valores e detalhes permanecem no Nexora.':
         'Desativada: nenhuma ocorrência de negócio é enviada ao GLPI.'}</p>
       <p>Chamados confirmados: {glpi.synced??0} · Aguardando: {glpi.pending??0} · Com falha: {glpi.failed??0}</p>
       <Field name="reason" label="Motivo (registrado em auditoria)" required/>
       <button type="submit" className={glpi.enabled?'':'primary'} disabled={busy}>
         {glpi.enabled?'Desativar integração com o GLPI':'Ativar integração com o GLPI'}</button></form>}
     {page==='notificacoes'&&<form className="bm-subscription-form" onSubmit={e=>{
       e.preventDefault();const selected=new FormData(e.currentTarget).get('indicator_id');
       run(()=>call.current('/api/v1/business/notifications/subscriptions',
         apiBody('PUT',{indicator_id:selected,enabled:true})));
     }}><Selector name="indicator_id" label="Acompanhar indicador" rows={indicators} required/>
       <button type="submit" disabled={busy}>Inscrever-me para alertas</button></form>}
     <DataGrid rows={items} columns={columns} stateKey={`business:${tenantId}:${page}`}/>
  </ListPage>;
}
