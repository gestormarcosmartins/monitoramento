import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const authConfig = {
  clientId: "central-monitor",
  realmUrl: `${window.location.origin}/auth/realms/central-monitor`,
  redirectUri: `${window.location.origin}/`,
};

const encode = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const randomValue = () => encode(crypto.getRandomValues(new Uint8Array(32)));
const parseToken = (token) => {
  const value = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(value.padEnd(Math.ceil(value.length / 4) * 4, "=")));
};

async function startLogin() {
  const verifier = randomValue();
  const challenge = encode(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const state = randomValue();
  sessionStorage.setItem("oidc_verifier", verifier);
  sessionStorage.setItem("oidc_state", state);
  const query = new URLSearchParams({client_id:authConfig.clientId, redirect_uri:authConfig.redirectUri, response_type:"code", scope:"openid profile email", code_challenge:challenge, code_challenge_method:"S256", state});
  window.location.assign(`${authConfig.realmUrl}/protocol/openid-connect/auth?${query}`);
}

async function finishLogin() {
  const query = new URLSearchParams(window.location.search);
  const code = query.get("code");
  if (!code) return sessionStorage.getItem("access_token");
  if (query.get("state") !== sessionStorage.getItem("oidc_state")) throw new Error("Estado de login invalido");
  const body = new URLSearchParams({grant_type:"authorization_code", client_id:authConfig.clientId, code, redirect_uri:authConfig.redirectUri, code_verifier:sessionStorage.getItem("oidc_verifier") || ""});
  const response = await fetch(`${authConfig.realmUrl}/protocol/openid-connect/token`, {method:"POST", headers:{"Content-Type":"application/x-www-form-urlencoded"}, body});
  if (!response.ok) throw new Error("Nao foi possivel concluir o login");
  const tokens = await response.json();
  sessionStorage.setItem("access_token", tokens.access_token);
  sessionStorage.setItem("id_token", tokens.id_token || "");
  sessionStorage.removeItem("oidc_verifier"); sessionStorage.removeItem("oidc_state");
  window.history.replaceState({}, document.title, "/");
  return tokens.access_token;
}

function logout() {
  const idToken = sessionStorage.getItem("id_token");
  sessionStorage.clear();
  const query = new URLSearchParams({client_id:authConfig.clientId, post_logout_redirect_uri:authConfig.redirectUri});
  if (idToken) query.set("id_token_hint", idToken);
  window.location.assign(`${authConfig.realmUrl}/protocol/openid-connect/logout?${query}`);
}

async function api(path, token, options = {}, tenantId = "") {
  const response = await fetch(path, {...options, headers:{"Content-Type":"application/json", Authorization:`Bearer ${token}`, ...(tenantId ? {"X-Tenant-ID":tenantId} : {}), ...(options.headers || {})}});
  if (response.status === 401) { sessionStorage.clear(); throw new Error("Sua sessao expirou. Entre novamente."); }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `Erro HTTP ${response.status}`);
  return body;
}

const menu = [["overview","Visao geral"],["clients","Clientes"],["assets","Ativos"],["monitors","Monitores"],["alerts","Alertas"],["incidents","Incidentes"],["admin","Administracao"]];

function Login({error}) {
  return <main className="login-shell"><section className="login-card"><div className="brand-mark">CM</div><small>OPERACOES</small><h1>Central Monitor</h1><p>Entre com sua conta para acessar os clientes e seus monitoramentos.</p>{error && <p className="error">{error}</p>}<button className="primary wide" onClick={startLogin}>Entrar</button></section></main>;
}

function Empty({children}) { return <div className="empty">{children}</div>; }
function Status({value}) { return <span className={`status ${value}`}>{value || "unknown"}</span>; }
function MonitorTable({monitors}) { return <table><thead><tr><th>Monitor</th><th>Ativo</th><th>Tipo</th><th>Estado</th><th>Mensagem</th></tr></thead><tbody>{monitors.map(m=><tr key={m.id}><td>{m.name}</td><td>{m.asset_name}</td><td>{m.monitor_type}</td><td><Status value={m.last_status}/></td><td>{m.last_message || "Aguardando coleta"}</td></tr>)}</tbody></table>; }

function App() {
  const [token,setToken] = useState(""); const [authReady,setAuthReady] = useState(false); const [view,setView] = useState("overview");
  const [tenants,setTenants] = useState([]); const [tenantId,setTenantId] = useState(""); const [assets,setAssets] = useState([]); const [monitors,setMonitors] = useState([]); const [alerts,setAlerts] = useState([]); const [incidents,setIncidents] = useState([]); const [error,setError] = useState("");
  const identity = useMemo(() => token ? parseToken(token) : null, [token]);
  const tenant = tenants.find(item => item.id === tenantId);

  useEffect(() => { finishLogin().then(value => { setToken(value || ""); setAuthReady(true); }).catch(e => { setError(e.message); setAuthReady(true); }); }, []);
  const refreshTenants = () => api("/api/v1/tenants",token).then(data => { setTenants(data); setTenantId(current => current || data[0]?.id || ""); }).catch(e => setError(e.message));
  const refresh = () => { if (!tenantId || !token) return; Promise.all([api("/api/v1/assets",token,{},tenantId),api("/api/v1/monitors",token,{},tenantId),api("/api/v1/alerts",token,{},tenantId),api("/api/v1/incidents",token,{},tenantId)]).then(([a,m,al,i]) => { setAssets(a); setMonitors(m); setAlerts(al); setIncidents(i); setError(""); }).catch(e => setError(e.message)); };
  useEffect(() => { if (token) refreshTenants(); }, [token]); useEffect(refresh,[tenantId,token]);

  const createTenant = async event => { event.preventDefault(); const form=new FormData(event.currentTarget); await api("/api/v1/tenants",token,{method:"POST",body:JSON.stringify({name:form.get("name"),slug:form.get("slug")})}); event.currentTarget.reset(); refreshTenants(); };
  const createAsset = async event => { event.preventDefault(); const form=new FormData(event.currentTarget); await api("/api/v1/assets",token,{method:"POST",body:JSON.stringify({name:form.get("name"),kind:form.get("kind"),address:form.get("address")})},tenantId); event.currentTarget.reset(); refresh(); };
  const createMonitor = async event => { event.preventDefault(); const form=new FormData(event.currentTarget); const type=form.get("type"); const configuration=type==="http"?{url:form.get("target")}:{host:form.get("target"),port:Number(form.get("port"))}; await api("/api/v1/monitors",token,{method:"POST",body:JSON.stringify({asset_id:form.get("asset"),name:form.get("name"),monitor_type:type,configuration,interval_seconds:60})},tenantId); event.currentTarget.reset(); refresh(); };

  if (!authReady) return <main className="login-shell"><section className="login-card"><p>Carregando...</p></section></main>;
  if (!token) return <Login error={error}/>;
  const openAlerts=alerts.filter(item=>item.status==="open"); const activeIncidents=incidents.filter(item=>!["resolved","ignored"].includes(item.status));

  return <div className="app-shell"><aside><div className="logo"><span>CM</span><div><small>OPERACOES</small><strong>Central Monitor</strong></div></div><nav>{menu.map(([key,label])=><button key={key} className={view===key?"active":""} onClick={()=>setView(key)}>{label}</button>)}</nav><div className="account"><span>{identity?.name || identity?.preferred_username || "Usuario"}</span><small>{identity?.email || ""}</small><button onClick={logout}>Sair</button></div></aside>
    <main className="content"><header><div><small>{tenant?.name || "CENTRAL"}</small><h1>{menu.find(([key])=>key===view)?.[1]}</h1></div><button onClick={refresh}>Atualizar</button></header>{error&&<p className="error">{error}</p>}<section className="tenant-bar"><label>Cliente<select value={tenantId} onChange={e=>setTenantId(e.target.value)}><option value="">Selecione</option>{tenants.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label></section>
      {view==="overview"&&<><section className="metrics"><article><span>Ativos</span><strong>{assets.length}</strong></article><article><span>Monitores</span><strong>{monitors.length}</strong></article><article><span>Alertas abertos</span><strong>{openAlerts.length}</strong></article><article><span>Incidentes</span><strong>{activeIncidents.length}</strong></article></section><section className="panel"><h2>Estado dos monitores</h2>{monitors.length?<MonitorTable monitors={monitors}/>:<Empty>Nenhum monitor cadastrado.</Empty>}</section></>}
      {view==="clients"&&<section className="panel"><div className="panel-title"><div><h2>Clientes</h2><p>Separe ativos, monitores e permissoes por empresa.</p></div></div><form className="form-row" onSubmit={createTenant}><input name="name" placeholder="Nome do cliente" required/><input name="slug" placeholder="slug-do-cliente" required/><button>Criar cliente</button></form><div className="cards-list">{tenants.map(t=><button key={t.id} className={`client-card ${tenantId===t.id?"selected":""}`} onClick={()=>setTenantId(t.id)}><strong>{t.name}</strong><span>{t.slug}</span></button>)}</div></section>}
      {view==="assets"&&<section className="panel"><h2>Ativos</h2><form className="form-row" onSubmit={createAsset}><input name="name" placeholder="Nome" required/><select name="kind"><option value="website">Website</option><option value="api">API</option><option value="linux">Linux</option><option value="windows">Windows</option><option value="database">Banco</option></select><input name="address" placeholder="Endereco"/><button>Cadastrar ativo</button></form>{assets.length?<table><thead><tr><th>Nome</th><th>Tipo</th><th>Endereco</th><th>Criticidade</th></tr></thead><tbody>{assets.map(a=><tr key={a.id}><td>{a.name}</td><td>{a.kind}</td><td>{a.address||"-"}</td><td>{a.criticality}</td></tr>)}</tbody></table>:<Empty>Nenhum ativo cadastrado.</Empty>}</section>}
      {view==="monitors"&&<section className="panel"><h2>Monitores</h2><form className="form-row" onSubmit={createMonitor}><select name="asset" required><option value="">Ativo</option>{assets.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select><input name="name" placeholder="Nome do monitor" required/><select name="type"><option value="http">HTTP</option><option value="tcp">TCP</option></select><input name="target" placeholder="URL ou host" required/><input name="port" type="number" placeholder="Porta TCP"/><button>Criar monitor</button></form>{monitors.length?<MonitorTable monitors={monitors}/>:<Empty>Nenhum monitor cadastrado.</Empty>}</section>}
      {view==="alerts"&&<section className="panel"><h2>Alertas</h2>{alerts.length?<table><thead><tr><th>Titulo</th><th>Severidade</th><th>Estado</th><th>Abertura</th></tr></thead><tbody>{alerts.map(a=><tr key={a.id}><td>{a.title}</td><td>{a.severity}</td><td><Status value={a.status}/></td><td>{new Date(a.opened_at).toLocaleString()}</td></tr>)}</tbody></table>:<Empty>Nenhum alerta registrado.</Empty>}</section>}
      {view==="incidents"&&<section className="panel"><h2>Incidentes</h2>{incidents.length?<table><thead><tr><th>Titulo</th><th>Estado</th><th>Atualizacao</th></tr></thead><tbody>{incidents.map(i=><tr key={i.id}><td>{i.title}</td><td><Status value={i.status}/></td><td>{new Date(i.updated_at).toLocaleString()}</td></tr>)}</tbody></table>:<Empty>Nenhum incidente registrado.</Empty>}</section>}
      {view==="admin"&&<section className="panel"><h2>Administracao</h2><div className="admin-grid"><article><span>Usuario conectado</span><strong>{identity?.name||identity?.preferred_username}</strong><small>{identity?.email}</small></article><article><span>Perfil</span><strong>Administrador</strong><small>Usuarios e perfis sao geridos pelo Keycloak.</small></article><article><span>Zabbix</span><strong>Console tecnico</strong><a href="/zabbix/" target="_blank">Abrir Zabbix</a></article></div></section>}
    </main></div>;
}

createRoot(document.getElementById("root")).render(<App/>);
