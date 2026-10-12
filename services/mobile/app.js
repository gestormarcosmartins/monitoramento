(() => {
const cfg = { clientId: "central-monitor", realm: `${location.origin}/auth/realms/central-monitor`, redirect: `${location.origin}/m/` };
const $ = (s) => document.querySelector(s), view = $("#view");
const ss = sessionStorage;
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const b64 = (b) => btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const rnd = () => b64(crypto.getRandomValues(new Uint8Array(32)));
const parse = (t) => JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
const u8 = (b) => Uint8Array.from(atob(b.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(b.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
const state = { me: null, tenant: ss.getItem("tenant") || "", tab: "home", busy: false };

async function token(req) {
  const r = await fetch(`${cfg.realm}/protocol/openid-connect/token`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ client_id: cfg.clientId, ...req }) });
  if (!r.ok) throw Error("Falha de autenticação");
  const t = await r.json();
  ss.setItem("access_token", t.access_token);
  if (t.refresh_token) ss.setItem("refresh_token", t.refresh_token);
  if (t.id_token) ss.setItem("id_token", t.id_token);
  return t;
}
async function access() {
  const cur = ss.getItem("access_token");
  if (!cur) throw Error("Sessão não iniciada");
  try { if (parse(cur).exp * 1000 > Date.now() + 60000) return cur; } catch {}
  const rt = ss.getItem("refresh_token");
  if (!rt) throw Error("Sessão expirada");
  return (await token({ grant_type: "refresh_token", refresh_token: rt })).access_token;
}
async function login() {
  const v = rnd(), s = rnd(), n = rnd();
  const c = b64(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(v))));
  ss.setItem("v", v); ss.setItem("s", s); ss.setItem("n", n);
  location.assign(`${cfg.realm}/protocol/openid-connect/auth?` + new URLSearchParams({ client_id: cfg.clientId, redirect_uri: cfg.redirect, response_type: "code", scope: "openid profile email", code_challenge: c, code_challenge_method: "S256", state: s, nonce: n }));
}
async function finish() {
  const q = new URLSearchParams(location.search), code = q.get("code");
  if (!code) return !!ss.getItem("access_token");
  if (q.get("state") !== ss.getItem("s")) throw Error("Estado de login inválido");
  const t = await token({ grant_type: "authorization_code", code, redirect_uri: cfg.redirect, code_verifier: ss.getItem("v") || "" });
  if (!t.id_token || parse(t.id_token).nonce !== ss.getItem("n")) { ss.clear(); throw Error("Nonce inválido"); }
  ["v", "s", "n"].forEach((k) => ss.removeItem(k));
  history.replaceState({}, "", "/m/");
  return true;
}
function logout() {
  const i = ss.getItem("id_token"); ss.clear();
  const q = new URLSearchParams({ client_id: cfg.clientId, post_logout_redirect_uri: cfg.redirect });
  if (i) q.set("id_token_hint", i);
  location.assign(`${cfg.realm}/protocol/openid-connect/logout?${q}`);
}
async function api(path, opt = {}) {
  const h = { Authorization: `Bearer ${await access()}`, ...(opt.body ? { "Content-Type": "application/json" } : {}) };
  if (opt.tenant) h["X-Tenant-ID"] = opt.tenant;
  const r = await fetch(path, { method: opt.method || "GET", headers: h, body: opt.body ? JSON.stringify(opt.body) : undefined });
  if (r.status === 401) { ss.clear(); throw Error("Sessão expirada"); }
  if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch {} throw Error(typeof d === "string" && d ? d : `Erro ${r.status}`); }
  return r.status === 204 ? null : r.json();
}
const role = () => (state.me.tenants.find((t) => String(t.id) === state.tenant) || {}).role;
const canAct = () => ["master", "admin", "operator"].includes(role());
const fmt = (d) => (d ? new Date(d).toLocaleString("pt-BR") : "-");
const PRI = { critical: "Crítica", high: "Alta", medium: "Média", low: "Baixa", disaster: "Desastre", average: "Média", warning: "Aviso", info: "Info" };
const ST = { new: "Novo", acknowledged: "Reconhecido", in_progress: "Em andamento", waiting_customer: "Aguard. cliente", waiting_supplier: "Aguard. fornecedor", resolved: "Resolvido", closed: "Fechado", ignored: "Ignorado", open: "Aberto" };
const loading = () => (view.innerHTML = '<p class="mut">Carregando…</p>');
const fail = (e) => { view.innerHTML = `<p class="err" role="alert">${esc(e.message)}</p><button class="primary" id="retry">Tentar de novo</button>`; $("#retry").onclick = render; };
const incItem = (i, when) => `<button class="item s-${esc(i.priority)}" data-inc="${esc(i.id)}"><b>${esc(i.title)}</b><small>${esc(PRI[i.priority] || i.priority)} · ${esc(ST[i.status] || i.status)} · ${fmt(when)}</small></button>`;
function bindInc() { view.querySelectorAll("[data-inc]").forEach((b) => (b.onclick = () => detail(b.dataset.inc))); }

async function home() {
  const d = await api(`/api/v1/dashboard?tenant_ids=${encodeURIComponent(state.tenant)}`);
  const t = d.totals;
  view.innerHTML = `<div class="grid">
    <div class="card"><div class="n err">${t.critical_alerts}</div><div class="l">Alertas críticos</div></div>
    <div class="card"><div class="n">${t.attention_alerts}</div><div class="l">Alertas de atenção</div></div>
    <div class="card"><div class="n">${t.open_incidents}</div><div class="l">Incidentes abertos</div></div>
    <div class="card"><div class="n">${t.availability}%</div><div class="l">Disponibilidade</div></div></div>
    <h2>Serviços</h2><p class="mut">${t.services_active} ativos · ${t.services_failed} com falha · ${t.services_normalizing} normalizando</p>
    <h2>Incidentes recentes</h2>${d.recent_incidents.map((i) => incItem(i, i.created_at)).join("") || '<p class="mut">Nenhum incidente.</p>'}`;
  bindInc();
}
async function alerts() {
  const l = await api("/api/v1/alerts", { tenant: state.tenant });
  view.innerHTML = l.map((a) => `<div class="item s-${esc(a.severity)}"><b>${esc(a.title)}</b><small>${esc(PRI[a.severity] || a.severity)} · ${esc(ST[a.status] || a.status)} · ${fmt(a.opened_at)}</small></div>`).join("") || '<p class="mut">Nenhum alerta.</p>';
}
async function incidents() {
  const l = await api("/api/v1/incidents", { tenant: state.tenant });
  view.innerHTML = l.map((i) => incItem(i, i.updated_at)).join("") || '<p class="mut">Nenhum incidente.</p>';
  bindInc();
}
async function detail(id) {
  loading();
  try {
    const r = await api(`/api/v1/incidents/${id}/detail`, { tenant: state.tenant });
    const d = r.incident, closed = ["resolved", "closed", "ignored"].includes(d.status);
    view.innerHTML = `<button class="ghost" id="back">← Voltar</button><h2>${esc(d.title)}</h2>
      <p><span class="badge">${esc(PRI[d.priority] || d.priority)}</span><span class="badge">${esc(ST[d.status] || d.status)}</span><span class="badge">SLA: ${esc(d.sla_status)}</span></p>
      <p class="mut">${esc(d.description || d.message || "")}</p>
      <p class="mut">Responsável: ${esc(d.assignee_name || d.team_name || "—")} · Prazo: ${fmt(d.resolution_due_at)}</p>
      ${canAct() && !closed ? `<div class="row">${d.status === "new" ? '<button class="primary" data-st="acknowledged">Reconhecer</button>' : ""}<button data-st="in_progress">Em andamento</button><button data-st="resolved">Resolver</button></div>` : ""}
      <p id="msg" class="err" role="alert"></p>
      <h3>Comentários</h3>${(r.comments || []).map((c) => `<div class="card"><small class="mut">${esc(c.author_name || "")} · ${fmt(c.created_at)}</small><div>${esc(c.body)}</div></div>`).join("") || '<p class="mut">Sem comentários.</p>'}
      ${canAct() ? '<textarea id="cm" maxlength="10000" placeholder="Novo comentário"></textarea><button class="primary" id="send">Comentar</button>' : ""}`;
    $("#back").onclick = render;
    view.querySelectorAll("[data-st]").forEach((b) => (b.onclick = async () => {
      if (state.busy) return; state.busy = true; b.disabled = true;
      try { await api(`/api/v1/incidents/${id}`, { method: "PATCH", tenant: state.tenant, body: { status: b.dataset.st, version: d.version } }); state.busy = false; await detail(id); }
      catch (e) { $("#msg").textContent = e.message; b.disabled = false; } finally { state.busy = false; }
    }));
    if ($("#send")) $("#send").onclick = async () => {
      const body = $("#cm").value.trim(); if (!body || state.busy) return; state.busy = true;
      try { await api(`/api/v1/incidents/${id}/comments`, { method: "POST", tenant: state.tenant, body: { body } }); state.busy = false; await detail(id); }
      catch (e) { $("#msg").textContent = e.message; } finally { state.busy = false; }
    };
  } catch (e) { fail(e); }
}
const pushOK = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
async function pushSub() { const reg = await navigator.serviceWorker.ready; return reg.pushManager.getSubscription(); }
async function pushRefresh() {
  const b = $("#push"); b.hidden = !pushOK; if (!pushOK) return;
  const s = await pushSub().catch(() => null);
  b.textContent = s ? "🔔 Ativas" : "🔕 Ativar"; b.dataset.on = s ? "1" : "";
}
async function pushToggle() {
  const b = $("#push"); b.disabled = true;
  try {
    const cur = await pushSub();
    if (cur) {
      await api("/api/v1/push/subscriptions/delete", { method: "POST", body: { endpoint: cur.endpoint } });
      await cur.unsubscribe();
    } else {
      if ((await Notification.requestPermission()) !== "granted") throw Error("Permissão de notificação negada no navegador");
      const { public_key } = await api("/api/v1/push/public-key");
      const reg = await navigator.serviceWorker.ready;
      const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: u8(public_key) });
      const j = s.toJSON();
      await api("/api/v1/push/subscriptions", { method: "POST", body: { endpoint: j.endpoint, keys: j.keys } });
    }
  } catch (e) { alert(e.message); }
  b.disabled = false; pushRefresh();
}
async function render() {
  loading();
  try { await ({ home, alerts, incidents }[state.tab])(); } catch (e) { fail(e); }
}
async function start() {
  try {
    if (!(await finish())) {
      view.innerHTML = '<div class="card"><h2>Acesso</h2><p class="mut">Entre com sua conta corporativa.</p><button class="primary" id="in">Entrar</button></div>';
      $("#in").onclick = login; return;
    }
    state.me = await api("/api/v1/auth/me");
    const ts = state.me.tenants;
    $("#logout").hidden = false; $("#logout").onclick = logout;
    if (!ts.length) { view.innerHTML = '<p class="mut">Nenhuma empresa liberada para o seu usuário.</p>'; return; }
    if (!ts.some((t) => String(t.id) === state.tenant)) state.tenant = String(ts[0].id);
    const sel = $("#company");
    sel.innerHTML = ts.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
    sel.value = state.tenant; sel.hidden = ts.length < 2;
    sel.onchange = () => { state.tenant = sel.value; ss.setItem("tenant", state.tenant); render(); };
    $("#push").onclick = pushToggle; pushRefresh();
    $("#tabs").hidden = false;
    $("#tabs").onclick = (e) => {
      const t = e.target.dataset.tab; if (!t) return;
      state.tab = t; document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === e.target)); render();
    };
    const dl = new URLSearchParams(location.search);
    if (dl.get("incident")) { if (dl.get("tenant") && ts.some((t) => String(t.id) === dl.get("tenant"))) { state.tenant = dl.get("tenant"); sel.value = state.tenant; } history.replaceState({}, "", "/m/"); detail(dl.get("incident")); } else render();
  } catch (e) { ss.clear(); view.innerHTML = `<p class="err" role="alert">${esc(e.message)}</p><button class="primary" id="in">Entrar</button>`; $("#in").onclick = login; }
}
if ("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", (e) => { if (e.data && e.data.open) location.assign("/m/" + e.data.open); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" }).catch(() => {});
start();
})();
