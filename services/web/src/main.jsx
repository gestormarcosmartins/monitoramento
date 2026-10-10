import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const headers = (tenantId) => ({
  "Content-Type": "application/json",
  "X-Dev-User": "local-admin",
  "X-Dev-Email": "admin@central-monitor.local",
  ...(tenantId ? { "X-Tenant-ID": tenantId } : {}),
});

async function api(path, options = {}, tenantId = "") {
  const response = await fetch(path, {...options, headers: {...headers(tenantId), ...(options.headers || {})}});
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `Erro HTTP ${response.status}`);
  return body;
}

function App() {
  const [tenants, setTenants] = useState([]);
  const [tenantId, setTenantId] = useState("");
  const [assets, setAssets] = useState([]);
  const [monitors, setMonitors] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [error, setError] = useState("");

  const refreshTenants = () => api("/api/v1/tenants").then((data) => {
    setTenants(data);
    if (!tenantId && data.length) setTenantId(data[0].id);
  }).catch((e) => setError(String(e.message)));

  const refresh = () => {
    if (!tenantId) return;
    Promise.all([
      api("/api/v1/assets", {}, tenantId), api("/api/v1/monitors", {}, tenantId),
      api("/api/v1/alerts", {}, tenantId), api("/api/v1/incidents", {}, tenantId),
    ]).then(([a,m,al,i]) => { setAssets(a); setMonitors(m); setAlerts(al); setIncidents(i); setError(""); })
      .catch((e) => setError(String(e.message)));
  };

  useEffect(refreshTenants, []);
  useEffect(refresh, [tenantId]);

  const createTenant = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await api("/api/v1/tenants", {method:"POST", body:JSON.stringify({name:form.get("name"), slug:form.get("slug")})});
    event.currentTarget.reset(); refreshTenants();
  };

  const createAsset = async (event) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await api("/api/v1/assets", {method:"POST", body:JSON.stringify({name:form.get("name"), kind:form.get("kind"), address:form.get("address")})}, tenantId);
    event.currentTarget.reset(); refresh();
  };

  const createMonitor = async (event) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const type=form.get("type");
    const configuration = type === "http" ? {url:form.get("target")} : {host:form.get("target"), port:Number(form.get("port"))};
    await api("/api/v1/monitors", {method:"POST", body:JSON.stringify({asset_id:form.get("asset"), name:form.get("name"), monitor_type:type, configuration, interval_seconds:60})}, tenantId);
    event.currentTarget.reset(); refresh();
  };

  return <main>
    <header><div><small>OPERAÇÕES</small><h1>Central Monitor</h1></div><button onClick={refresh}>Atualizar</button></header>
    {error && <p className="error">{error}</p>}
    <section className="toolbar">
      <label>Cliente<select value={tenantId} onChange={(e)=>setTenantId(e.target.value)}><option value="">Selecione</option>{tenants.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
      <form onSubmit={createTenant}><input name="name" placeholder="Novo cliente" required/><input name="slug" placeholder="slug-do-cliente" required/><button>Criar</button></form>
    </section>
    <section className="metrics">
      <article><span>Ativos</span><strong>{assets.length}</strong></article><article><span>Monitores</span><strong>{monitors.length}</strong></article>
      <article><span>Alertas abertos</span><strong>{alerts.filter(a=>a.status==='open').length}</strong></article><article><span>Incidentes</span><strong>{incidents.filter(i=>!['resolved','ignored'].includes(i.status)).length}</strong></article>
    </section>
    {tenantId && <section className="grid">
      <article><h2>Novo ativo</h2><form onSubmit={createAsset}><input name="name" placeholder="Nome" required/><select name="kind"><option value="website">Website</option><option value="api">API</option><option value="linux">Linux</option><option value="windows">Windows</option><option value="database">Banco</option></select><input name="address" placeholder="Endereco"/><button>Cadastrar</button></form></article>
      <article><h2>Novo monitor</h2><form onSubmit={createMonitor}><select name="asset" required><option value="">Ativo</option>{assets.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select><input name="name" placeholder="Nome do monitor" required/><select name="type"><option value="http">HTTP</option><option value="tcp">TCP</option></select><input name="target" placeholder="URL ou host" required/><input name="port" type="number" placeholder="Porta para TCP"/><button>Criar monitor</button></form></article>
    </section>}
    <section><h2>Estado dos monitores</h2><table><thead><tr><th>Monitor</th><th>Ativo</th><th>Tipo</th><th>Estado</th><th>Mensagem</th></tr></thead><tbody>{monitors.map(m=><tr key={m.id}><td>{m.name}</td><td>{m.asset_name}</td><td>{m.monitor_type}</td><td><span className={`status ${m.last_status}`}>{m.last_status}</span></td><td>{m.last_message || "Aguardando coleta"}</td></tr>)}</tbody></table></section>
    <section><h2>Incidentes recentes</h2><table><thead><tr><th>Titulo</th><th>Estado</th><th>Atualizacao</th></tr></thead><tbody>{incidents.map(i=><tr key={i.id}><td>{i.title}</td><td>{i.status}</td><td>{new Date(i.updated_at).toLocaleString()}</td></tr>)}</tbody></table></section>
  </main>;
}

createRoot(document.getElementById("root")).render(<App />);
