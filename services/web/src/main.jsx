import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import "./grid.css";
import "./nexora.css";
import "./home.css";
const authConfig = {
    clientId: "central-monitor",
    realmUrl: `${location.origin}/auth/realms/central-monitor`,
    redirectUri: `${location.origin}/`,
  },
  encode = (b) =>
    btoa(String.fromCharCode(...b))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, ""),
  randomValue = () => encode(crypto.getRandomValues(new Uint8Array(32))),
  parseToken = (t) =>
    JSON.parse(
      atob(
        t
          .split(".")[1]
          .replace(/-/g, "+")
          .replace(/_/g, "/")
          .padEnd(Math.ceil(t.split(".")[1].length / 4) * 4, "="),
      ),
    );
async function startLogin() {
  const v = randomValue(),
    c = encode(
      new Uint8Array(
        await crypto.subtle.digest("SHA-256", new TextEncoder().encode(v)),
      ),
    ),
    s = randomValue();
  sessionStorage.setItem("oidc_verifier", v);
  sessionStorage.setItem("oidc_state", s);
  location.assign(
    `${authConfig.realmUrl}/protocol/openid-connect/auth?${new URLSearchParams({ client_id: authConfig.clientId, redirect_uri: authConfig.redirectUri, response_type: "code", scope: "openid profile email", code_challenge: c, code_challenge_method: "S256", state: s })}`,
  );
}
async function finishLogin() {
  const q = new URLSearchParams(location.search),
    code = q.get("code");
  if (!code) return sessionStorage.getItem("access_token");
  if (q.get("state") !== sessionStorage.getItem("oidc_state"))
    throw Error("Estado de login inválido");
  const r = await fetch(
    `${authConfig.realmUrl}/protocol/openid-connect/token`,
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: authConfig.clientId,
        code,
        redirect_uri: authConfig.redirectUri,
        code_verifier: sessionStorage.getItem("oidc_verifier") || "",
      }),
    },
  );
  if (!r.ok) throw Error("Não foi possível concluir o login");
  const t = await r.json();
  sessionStorage.setItem("access_token", t.access_token);
  sessionStorage.setItem("id_token", t.id_token || "");
  history.replaceState({}, document.title, "/");
  return t.access_token;
}
function logout() {
  const i = sessionStorage.getItem("id_token");
  sessionStorage.clear();
  const q = new URLSearchParams({
    client_id: authConfig.clientId,
    post_logout_redirect_uri: authConfig.redirectUri,
  });
  if (i) q.set("id_token_hint", i);
  location.assign(`${authConfig.realmUrl}/protocol/openid-connect/logout?${q}`);
}
async function api(path, token, options = {}, tenant = "") {
  const r = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(tenant ? { "X-Tenant-ID": tenant } : {}),
      ...(options.headers || {}),
    },
  });
  if (r.status === 401) {
    sessionStorage.clear();
    throw Error("Sua sessão expirou");
  }
  if (r.status === 204) return null;
  const b = await r.json();
  if (!r.ok)
    throw Error(
      typeof b.detail === "string" ? b.detail : `Erro HTTP ${r.status}`,
    );
  return b;
}
const menu = [
    ["overview", "Visão geral", "⌂"],
    ["clients", "Empresas", "▤"],
    ["assets", "Ambientes", "◇"],
    ["monitors", "Serviços", "⌘"],
    ["databases", "Banco de dados", "▦"],
    ["alerts", "Alertas", "△"],
    ["incidents", "Incidentes", "!"],
    ["suppliers", "Terceiros", "↗"],
    ["sla", "SLA", "◷"],
    ["automations", "Automações", "⚙"],
    ["reports", "Relatórios", "▥"],
    ["settings", "Configurações", "⚒"],
    ["admin", "Administração", "♙"],
  ],
  labels = {
    new: "Novo",
    acknowledged: "Reconhecido",
    in_progress: "Em andamento",
    waiting_customer: "Aguardando cliente",
    waiting_supplier: "Aguardando fornecedor",
    resolved: "Resolvido",
    closed: "Fechado",
    ignored: "Ignorado",
    low: "Baixa",
    medium: "Média",
    high: "Alta",
    critical: "Crítica",
    on_track: "Dentro do prazo",
    at_risk: "Em risco",
    breached: "SLA vencido",
    synced: "Sincronizado",
    pending: "Pendente",
    failed: "Falhou",
  },
  translated = (value) =>
    labels[value] || String(value || "Desconhecido").replaceAll("_", " "),
  ask = (l, v = "") => prompt(l, v ?? ""),
  fmt = (v) => (v ? new Date(v).toLocaleString("pt-BR") : "—");
function LogoMark() {
  return (
    <svg className="nexora-mark" viewBox="0 0 64 52" aria-hidden="true">
      <defs>
        <linearGradient
          id="nexora-gradient"
          x1="4"
          y1="46"
          x2="58"
          y2="4"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#06b6d4" />
          <stop offset=".52" stopColor="#2563ff" />
          <stop offset="1" stopColor="#35d5ff" />
        </linearGradient>
      </defs>
      <path
        d="M8 10v30c0 4 5 6 8 3l16-24 16 24c3 4 8 1 8-3V10"
        fill="none"
        stroke="url(#nexora-gradient)"
        strokeWidth="9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="5" fill="#22d3ee" />
      <circle cx="56" cy="8" r="5" fill="#38bdf8" />
    </svg>
  );
}
function Login({ error }) {
  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark">
          <LogoMark />
        </div>
        <small>OPERAÇÃO INTELIGENTE EM ESCALA</small>
        <h1>Nexora</h1>
        <p>Acesse a central de operações e atendimento.</p>
        {error && <p className="error">{error}</p>}
        <button className="primary wide" onClick={startLogin}>
          Entrar
        </button>
      </section>
    </main>
  );
}
function Empty({ children }) {
  return <div className="empty">{children}</div>;
}
function Status({ value }) {
  return (
    <span
      className={`status ${String(value || "unknown").replaceAll("_", "-")}`}
    >
      {translated(value)}
    </span>
  );
}
function DataGrid({
  columns,
  rows = [],
  actions,
  empty = "Nenhum registro encontrado.",
  onRow,
}) {
  const [sort, setSort] = useState({ key: columns[0]?.key, direction: "asc" }),
    ordered = useMemo(
      () =>
        [...rows].sort(
          (a, b) =>
            String(a[sort.key] ?? "").localeCompare(
              String(b[sort.key] ?? ""),
              "pt-BR",
              { numeric: true },
            ) * (sort.direction === "asc" ? 1 : -1),
        ),
      [rows, sort],
    );
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div
      className="grid-wrap"
      role="region"
      aria-label="Tabela de dados"
      tabIndex="0"
    >
      <table className="data-grid">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                aria-sort={
                  sort.key === c.key
                    ? sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                }
              >
                <button
                  className="sort-button"
                  onClick={() =>
                    setSort((s) => ({
                      key: c.key,
                      direction:
                        s.key === c.key && s.direction === "asc"
                          ? "desc"
                          : "asc",
                    }))
                  }
                >
                  {c.label}
                  <span aria-hidden="true">
                    {sort.key === c.key
                      ? sort.direction === "asc"
                        ? "▲"
                        : "▼"
                      : "↕"}
                  </span>
                </button>
              </th>
            ))}
            {actions && <th className="actions-heading">Ações</th>}
          </tr>
        </thead>
        <tbody>
          {ordered.map((r) => (
            <tr
              key={r.id}
              className={onRow ? "selectable-row" : undefined}
              onClick={() => onRow?.(r)}
              tabIndex={onRow ? 0 : undefined}
              onKeyDown={(e) => {
                if (onRow && (e.key === "Enter" || e.key === " ")) onRow(r);
              }}
            >
              {columns.map((c) => (
                <td key={c.key}>
                  {c.render ? c.render(r) : (r[c.key] ?? "—")}
                </td>
              ))}
              {actions && (
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="row-actions">{actions(r)}</div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const Input = ({ label, ...props }) => (
  <label className="field">
    <span>{label}</span>
    <input {...props} />
  </label>
);
function Toast({ notice, onClose }) {
  if (!notice) return null;
  return (
    <div
      className={`notice-toast notice-toast--${notice.type}`}
      role={notice.type === "error" ? "alert" : "status"}
    >
      <span aria-hidden="true">{notice.type === "error" ? "!" : "✓"}</span>
      <div>
        <strong>
          {notice.type === "error" ? "Não foi possível concluir" : "Tudo certo"}
        </strong>
        <p>{notice.text}</p>
      </div>
      <button type="button" onClick={onClose} aria-label="Fechar mensagem">
        ×
      </button>
    </div>
  );
}
function NotificationChannels({
  settings,
  tenantName,
  tenantId,
  token,
  submit,
  refresh,
  run,
}) {
  const formPayload = (form) => {
    const f = new FormData(form);
    return {
      smtp_enabled: f.get("smtp_enabled") === "on",
      smtp_host: f.get("smtp_host") || null,
      smtp_port: Number(f.get("smtp_port") || 587),
      smtp_username: f.get("smtp_username") || null,
      smtp_password: f.get("smtp_password") || null,
      smtp_from_address: f.get("smtp_from_address") || null,
      smtp_tls: f.get("smtp_tls") === "on",
      evolution_enabled: f.get("evolution_enabled") === "on",
      evolution_api_url: f.get("evolution_api_url") || null,
      evolution_api_key: f.get("evolution_api_key") || null,
      evolution_instance: f.get("evolution_instance") || null,
    };
  };
  const testChannel = (button, channel) => {
    const target = ask(
      channel === "email"
        ? "E-mail para receber o teste"
        : "WhatsApp para receber o teste (país + DDD + número)",
    );
    if (!target) return;
    run(
      async () => {
        await api(
          "/api/v1/notification-settings",
          token,
          { method: "PUT", body: JSON.stringify(formPayload(button.form)) },
          tenantId,
        );
        await api(
          `/api/v1/notification-settings/test-${channel}`,
          token,
          { method: "POST", body: JSON.stringify({ target }) },
          tenantId,
        );
        await refresh();
      },
      channel === "email"
        ? "Configuração salva e e-mail de teste enviado."
        : "Configuração salva e WhatsApp de teste enviado.",
    );
  };
  return (
    <section className="panel notification-channels">
      <div className="section-head">
        <div>
          <h2>Canais de notificação</h2>
          <p>
            Parametrização exclusiva de {tenantName}. Senhas e chaves permanecem
            protegidas.
          </p>
        </div>
      </div>
      <form
        key={settings.updated_at || tenantId}
        className="channel-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit(
            "notification-settings",
            formPayload(e.currentTarget),
            refresh,
            "PUT",
          );
        }}
      >
        <fieldset>
          <legend>E-mail (SMTP)</legend>
          <label className="switch">
            <input
              name="smtp_enabled"
              type="checkbox"
              defaultChecked={settings.smtp_enabled}
            />{" "}
            Habilitar envio por e-mail
          </label>
          <Input
            name="smtp_host"
            label="Servidor SMTP"
            defaultValue={settings.smtp_host}
          />
          <Input
            name="smtp_port"
            label="Porta"
            type="number"
            min="1"
            max="65535"
            defaultValue={settings.smtp_port || 587}
          />
          <Input
            name="smtp_username"
            label="Usuário"
            defaultValue={settings.smtp_username}
          />
          <Input
            name="smtp_password"
            label="Senha"
            type="password"
            placeholder={
              settings.smtp_password_configured
                ? "Configurada — deixe vazio para manter"
                : "Informe a senha"
            }
          />
          <p className="field-help">
            Gmail exige uma senha de app com 16 caracteres; a senha normal da
            conta não é aceita pelo SMTP.
          </p>
          <Input
            name="smtp_from_address"
            label="E-mail remetente"
            type="email"
            defaultValue={settings.smtp_from_address}
          />
          <label className="switch">
            <input
              name="smtp_tls"
              type="checkbox"
              defaultChecked={settings.smtp_tls}
            />{" "}
            Usar TLS/STARTTLS
          </label>
          <button
            type="button"
            className="secondary"
            onClick={(e) => testChannel(e.currentTarget, "email")}
          >
            Enviar teste
          </button>
        </fieldset>
        <fieldset>
          <legend>WhatsApp (Evolution API)</legend>
          <label className="switch">
            <input
              name="evolution_enabled"
              type="checkbox"
              defaultChecked={settings.evolution_enabled}
            />{" "}
            Habilitar envio por WhatsApp
          </label>
          <Input
            name="evolution_api_url"
            label="URL da Evolution API"
            type="url"
            placeholder="https://evolution.seudominio.com"
            defaultValue={settings.evolution_api_url}
          />
          <Input
            name="evolution_instance"
            label="Instância"
            placeholder="nexora"
            defaultValue={settings.evolution_instance}
          />
          <Input
            name="evolution_api_key"
            label="API Key"
            type="password"
            placeholder={
              settings.evolution_api_key_configured
                ? "Configurada — deixe vazio para manter"
                : "Informe a API Key"
            }
          />
          <p className="field-help">
            Use números com país e DDD, por exemplo: 5511999999999.
          </p>
          <button
            type="button"
            className="secondary"
            onClick={(e) => testChannel(e.currentTarget, "whatsapp")}
          >
            Enviar teste
          </button>
        </fieldset>
        <div className="channel-actions">
          <button className="primary">Salvar configurações</button>
        </div>
      </form>
    </section>
  );
}
function App() {
  const [token, setToken] = useState(""),
    [ready, setReady] = useState(false),
    [view, setView] = useState("overview"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(null),
    [tenantId, setTenantId] = useState("");
  const [tenants, setTenants] = useState([]),
    [assets, setAssets] = useState([]),
    [monitors, setMonitors] = useState([]),
    [alerts, setAlerts] = useState([]),
    [incidents, setIncidents] = useState([]),
    [dashboard, setDashboard] = useState({ totals: {}, companies: [] });
  const [suppliers, setSuppliers] = useState([]),
    [slas, setSlas] = useState([]),
    [holidays, setHolidays] = useState([]),
    [teams, setTeams] = useState([]),
    [rules, setRules] = useState([]),
    [notificationSettings, setNotificationSettings] = useState({
      smtp_enabled: false,
      smtp_host: "",
      smtp_port: 587,
      smtp_username: "",
      smtp_from_address: "",
      smtp_tls: true,
      evolution_enabled: false,
      evolution_api_url: "",
      evolution_instance: "",
    }),
    [maintenance, setMaintenance] = useState([]),
    [users, setUsers] = useState([]),
    [audit, setAudit] = useState([]),
    [report, setReport] = useState({
      totals: {},
      by_priority: [],
      suppliers: [],
    }),
    [actions, setActions] = useState({
      definitions: [],
      grants: [],
      executions: [],
    }),
    [benner, setBenner] = useState({ snapshot: null, checks: [] }),
    [detail, setDetail] = useState(null),
    [selected, setSelected] = useState(null),
    [editingTeam, setEditingTeam] = useState(null),
    [showCompanyForm, setShowCompanyForm] = useState(false),
    [companySearch, setCompanySearch] = useState("");
  const identity = useMemo(() => (token ? parseToken(token) : null), [token]),
    tenant = tenants.find((t) => t.id === tenantId),
    run = async (fn, success = "Operação realizada com sucesso.") => {
      try {
        await fn();
        setError("");
        if (success) setNotice({ type: "success", text: success });
      } catch (e) {
        setError(e.message);
        setNotice({ type: "error", text: e.message });
      }
    };
  useEffect(() => {
    finishLogin()
      .then((t) => {
        setToken(t || "");
        setReady(true);
      })
      .catch((e) => {
        setError(e.message);
        setReady(true);
      });
  }, []);
  const refreshTenants = () =>
      api("/api/v1/tenants", token).then((d) => {
        setTenants(d);
        setTenantId((v) => {
          const saved = localStorage.getItem("nexora_tenant");
          return d.some((t) => t.id === v)
            ? v
            : d.some((t) => t.id === saved)
              ? saved
              : d.find((t) => t.slug === "morada")?.id ||
                d.find((t) => t.active)?.id ||
                d[0]?.id ||
                "";
        });
      }),
    refreshDashboard = () => api("/api/v1/dashboard", token).then(setDashboard);
  const refresh = () =>
    tenantId &&
    Promise.all(
      [
        "assets",
        "monitors",
        "alerts",
        "incidents",
        "suppliers",
        "sla-policies",
        "holidays",
        "teams",
        "notification-rules",
        "notification-settings",
        "maintenance-windows",
        "users",
        "audit-log",
        "reports/summary",
        "actions/catalog",
        "benner/latest",
      ].map((p) => api(`/api/v1/${p}`, token, {}, tenantId)),
    ).then(([a, m, al, i, s, sl, h, t, r, ns, mw, u, au, re, ac, b]) => {
      setAssets(a);
      setMonitors(m);
      setAlerts(al);
      setIncidents(i);
      setSuppliers(s);
      setSlas(sl);
      setHolidays(h);
      setTeams(t);
      setRules(r);
      setNotificationSettings(ns);
      setMaintenance(mw);
      setUsers(u);
      setAudit(au);
      setReport(re);
      setActions(ac);
      setBenner(b);
    });
  useEffect(() => {
    if (token) {
      refreshTenants();
      refreshDashboard();
    }
  }, [token]);
  useEffect(() => {
    if (token && tenantId) refresh().catch((e) => setError(e.message));
  }, [token, tenantId]);
  const submit = (path, body, after = refresh, method = "POST") =>
      run(
        async () => {
          await api(
            `/api/v1/${path}`,
            token,
            { method, body: JSON.stringify(body) },
            tenantId,
          );
          await after();
        },
        method === "PUT"
          ? "Configurações salvas com sucesso."
          : "Registro salvo com sucesso.",
      ),
    remove = (path, id, label) =>
      confirm(`Excluir ${label}?`) &&
      run(async () => {
        await api(
          `/api/v1/${path}/${id}`,
          token,
          { method: "DELETE" },
          tenantId,
        );
        await refresh();
      }, "Registro excluído com sucesso."),
    loadDetail = (i) =>
      run(async () => {
        setSelected(i);
        setDetail(
          await api(`/api/v1/incidents/${i.id}/detail`, token, {}, tenantId),
        );
      }, "");
  const updateIncident = (i, status, extra = {}) =>
      submit(
        `incidents/${i.id}`,
        { status, version: i.version, ...extra },
        async () => {
          await refresh();
          await loadDetail({ ...i, version: i.version + 1 });
        },
        "PATCH",
      ),
    download = async (f) => {
      const r = await fetch(`/api/v1/reports/export/${f}`, {
        headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenantId },
      });
      if (!r.ok) throw Error("Falha ao gerar relatório");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await r.blob());
      a.download = `relatorio-incidentes.${f}`;
      a.click();
    };
  const supplierBody = (s, x = {}) => ({
      name: s.name,
      contact_name: s.contact_name,
      email: s.email,
      phone: s.phone,
      services: s.services,
      contract_reference: s.contract_reference,
      response_minutes: s.response_minutes,
      resolution_minutes: s.resolution_minutes,
      active: s.active,
      ...x,
    }),
    slaBody = (s, x = {}) => ({
      name: s.name,
      priority: s.priority,
      response_minutes: s.response_minutes,
      resolution_minutes: s.resolution_minutes,
      coverage: s.coverage,
      pause_on_waiting: s.pause_on_waiting,
      active: s.active,
      ...x,
    }),
    ruleBody = (r, x = {}) => ({
      name: r.name,
      channel: r.channel,
      target: r.target,
      minimum_priority: r.minimum_priority,
      events: r.events,
      active: r.active,
      ...x,
    }),
    maintenanceBody = (m, x = {}) => ({
      name: m.name,
      asset_id: m.asset_id,
      starts_at: m.starts_at,
      ends_at: m.ends_at,
      reason: m.reason,
      active: m.active,
      ...x,
    });
  if (!ready)
    return (
      <main className="login-shell">
        <section className="login-card">Carregando...</section>
      </main>
    );
  if (!token) return <Login error={error} />;
  const active = incidents.filter(
      (i) => !["resolved", "closed", "ignored"].includes(i.status),
    ),
    visibleCompanies = tenants.filter((t) =>
      `${t.name} ${t.slug}`.toLowerCase().includes(companySearch.toLowerCase()),
    ),
    incidentCols = [
      { key: "title", label: "Incidente" },
      {
        key: "glpi_ticket_id",
        label: "Chamado",
        render: (r) =>
          r.glpi_ticket_id ? `#${r.glpi_ticket_id}` : "Aguardando",
      },
      {
        key: "priority",
        label: "Prioridade",
        render: (r) => <Status value={r.priority} />,
      },
      {
        key: "status",
        label: "Estado",
        render: (r) => <Status value={r.status} />,
      },
      {
        key: "resolution_due_at",
        label: "Prazo",
        render: (r) => fmt(r.resolution_due_at),
      },
      {
        key: "sync_status",
        label: "GLPI",
        render: (r) => <Status value={r.sync_status} />,
      },
    ];
  return (
    <div className="app-shell">
      <aside>
        <div className="logo">
          <span>
            <LogoMark />
          </span>
          <div>
            <strong>Nexora</strong>
            <small>OPERAÇÃO INTELIGENTE</small>
          </div>
        </div>
        <nav aria-label="Navegação principal">
          {menu.map(([k, l, i]) => (
            <button
              key={k}
              className={view === k ? "active" : ""}
              aria-current={view === k ? "page" : undefined}
              onClick={() => setView(k)}
            >
              <i aria-hidden="true">{i}</i>
              {l}
            </button>
          ))}
        </nav>
        <div className="account">
          <span>{identity?.name || identity?.preferred_username}</span>
          <small>Acesso multiempresa</small>
          <button onClick={logout}>Sair</button>
        </div>
      </aside>
      <div className="workspace">
        <div className="topbar">
          <div className="breadcrumb">
            Nexora <b>›</b>
            <strong>{menu.find((x) => x[0] === view)?.[1]}</strong>
          </div>
          <input aria-label="Pesquisa global" placeholder="Pesquisar..." />
          <select
            aria-label="Selecionar empresa"
            value={tenantId || "all"}
            onChange={(e) => {
              const value = e.target.value === "all" ? "" : e.target.value;
              setTenantId(value);
              value
                ? localStorage.setItem("nexora_tenant", value)
                : localStorage.removeItem("nexora_tenant");
            }}
          >
            <option value="all">
              Todas as empresas ({tenants.filter((t) => t.active).length})
            </option>
            <optgroup label="Empresas cadastradas">
              {tenants
                .filter((t) => t.active)
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </optgroup>
          </select>
        </div>
        <main className="content">
          <header>
            <div>
              <h1>{menu.find((x) => x[0] === view)?.[1]}</h1>
              <p>{tenant?.name || "Visão consolidada da operação"}</p>
            </div>
            <button
              onClick={() => {
                refreshTenants();
                refreshDashboard();
                refresh();
              }}
            >
              ↻&nbsp;&nbsp;Atualizar
            </button>
          </header>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {view === "overview" && (
            <>
              <section className="home-kpis">
                <article className="critical">
                  <i>●</i>
                  <strong>{dashboard.totals.companies_with_alerts || 0}</strong>
                  <div>
                    <b>Empresas com alertas</b>
                    <span>Exigem ação</span>
                  </div>
                </article>
                <article className="critical">
                  <i>●</i>
                  <strong>{dashboard.totals.critical_alerts || 0}</strong>
                  <div>
                    <b>Alertas críticos</b>
                    <span>Ação imediata</span>
                  </div>
                </article>
                <article className="attention">
                  <i>●</i>
                  <strong>{dashboard.totals.attention_alerts || 0}</strong>
                  <div>
                    <b>Em atenção</b>
                    <span>Acompanhar</span>
                  </div>
                </article>
                <article className="info">
                  <i>●</i>
                  <strong>{dashboard.totals.open_incidents || 0}</strong>
                  <div>
                    <b>Incidentes abertos</b>
                    <span>Em atendimento</span>
                  </div>
                </article>
                <article className="sla">
                  <i>●</i>
                  <strong>{report.totals?.breached || 0}</strong>
                  <div>
                    <b>SLA vencido</b>
                    <span>Escalonar</span>
                  </div>
                </article>
              </section>
              <section className="panel">
                <h2>Incidentes prioritários</h2>
                <DataGrid
                  columns={incidentCols}
                  rows={active.slice(0, 10)}
                  actions={(i) => (
                    <button
                      onClick={() => {
                        loadDetail(i);
                        setView("incidents");
                      }}
                    >
                      Tratar
                    </button>
                  )}
                />
              </section>
            </>
          )}
          {view === "clients" && (
            <section className="panel">
              <div className="section-head">
                <div>
                  <h2>Empresas monitoradas</h2>
                  <p>Segregação completa por organização.</p>
                </div>
                <button onClick={() => setShowCompanyForm(!showCompanyForm)}>
                  ＋ Nova empresa
                </button>
              </div>
              {showCompanyForm && (
                <form
                  className="form-row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    submit(
                      "tenants",
                      { name: f.get("name"), slug: f.get("slug") },
                      refreshTenants,
                    );
                    e.currentTarget.reset();
                  }}
                >
                  <input name="name" placeholder="Nome" required />
                  <input name="slug" placeholder="identificador" required />
                  <button>Criar</button>
                </form>
              )}
              <input
                value={companySearch}
                onChange={(e) => setCompanySearch(e.target.value)}
                placeholder="Buscar empresa"
              />
              <div className="company-cards">
                {visibleCompanies.map((t) => (
                  <article key={t.id}>
                    <div className="company-card-head">
                      <i>{t.name[0]}</i>
                      <div>
                        <h3>{t.name}</h3>
                        <small>{t.slug}</small>
                      </div>
                      <Status value={t.active ? "ativa" : "inativa"} />
                    </div>
                    <div className="company-actions">
                      <button
                        onClick={() => {
                          setTenantId(t.id);
                          setView("overview");
                        }}
                      >
                        Visualizar →
                      </button>
                      <div>
                        <button
                          onClick={() => {
                            const name = ask("Nome", t.name);
                            if (name)
                              submit(
                                `tenants/${t.id}`,
                                {
                                  name,
                                  slug: t.slug,
                                  timezone: t.timezone,
                                  active: t.active,
                                },
                                refreshTenants,
                                "PUT",
                              );
                          }}
                        >
                          Editar
                        </button>
                        <button
                          onClick={() =>
                            submit(
                              `tenants/${t.id}`,
                              {
                                name: t.name,
                                slug: t.slug,
                                timezone: t.timezone,
                                active: !t.active,
                              },
                              refreshTenants,
                              "PUT",
                            )
                          }
                        >
                          {t.active ? "Inativar" : "Ativar"}
                        </button>
                        <button
                          className="danger"
                          onClick={() => remove("tenants", t.id, t.name)}
                        >
                          Excluir
                        </button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}
          {view === "assets" && (
            <section className="panel">
              <h2>Ambientes e ativos</h2>
              <form
                className="form-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  submit("assets", {
                    name: f.get("name"),
                    kind: f.get("kind"),
                    address: f.get("address"),
                  });
                  e.currentTarget.reset();
                }}
              >
                <input name="name" placeholder="Nome" required />
                <select name="kind">
                  <option value="website">Website</option>
                  <option value="api">API</option>
                  <option value="linux">Linux</option>
                  <option value="windows">Windows</option>
                  <option value="database">Banco</option>
                  <option value="network">Rede</option>
                </select>
                <input name="address" placeholder="Endereço" />
                <button>Cadastrar</button>
              </form>
              <DataGrid
                columns={[
                  { key: "name", label: "Nome" },
                  { key: "kind", label: "Tipo" },
                  { key: "address", label: "Endereço" },
                  { key: "criticality", label: "Criticidade" },
                  {
                    key: "active",
                    label: "Estado",
                    render: (r) => (
                      <Status value={r.active ? "ativo" : "inativo"} />
                    ),
                  },
                ]}
                rows={assets}
                actions={(a) => (
                  <>
                    <button
                      onClick={() => {
                        const name = ask("Nome", a.name);
                        if (name)
                          submit(
                            `assets/${a.id}`,
                            {
                              name,
                              kind: a.kind,
                              address: a.address,
                              criticality: a.criticality,
                              metadata: a.metadata || {},
                              active: a.active,
                            },
                            refresh,
                            "PUT",
                          );
                      }}
                    >
                      Editar
                    </button>
                    <button
                      onClick={() =>
                        submit(
                          `assets/${a.id}`,
                          {
                            name: a.name,
                            kind: a.kind,
                            address: a.address,
                            criticality: a.criticality,
                            metadata: a.metadata || {},
                            active: !a.active,
                          },
                          refresh,
                          "PUT",
                        )
                      }
                    >
                      {a.active ? "Inativar" : "Ativar"}
                    </button>
                    <button
                      className="danger"
                      onClick={() => remove("assets", a.id, a.name)}
                    >
                      Excluir
                    </button>
                  </>
                )}
              />
            </section>
          )}
          {view === "monitors" && (
            <section className="panel">
              <h2>Serviços monitorados</h2>
              <p>Monitores HTTP são provisionados automaticamente no Zabbix.</p>
              <form
                className="form-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget),
                    type = f.get("type");
                  submit("monitors", {
                    asset_id: f.get("asset"),
                    name: f.get("name"),
                    monitor_type: type,
                    configuration:
                      type === "http"
                        ? { url: f.get("target") }
                        : {
                            host: f.get("target"),
                            port: Number(f.get("port")),
                          },
                    interval_seconds: 60,
                    severity: f.get("severity"),
                    responsible_team_id: f.get("responsible_team") || null,
                    responsible_user_id: f.get("responsible_user") || null,
                    notify_on_failure: f.get("notify_failure") === "on",
                    notify_on_recovery: f.get("notify_recovery") === "on",
                    auto_resolve_incident: f.get("auto_resolve") === "on",
                  });
                  e.currentTarget.reset();
                }}
              >
                <select name="asset" required>
                  <option value="">Ativo</option>
                  {assets.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <input name="name" placeholder="Nome" required />
                <select name="type">
                  <option value="http">HTTP</option>
                  <option value="tcp">TCP</option>
                </select>
                <input name="target" placeholder="URL ou host" required />
                <input name="port" type="number" placeholder="Porta" />
                <select name="severity">
                  <option value="warning">Atenção</option>
                  <option value="high">Alta</option>
                  <option value="disaster">Crítica</option>
                </select>
                <select name="responsible_team">
                  <option value="">Equipe responsável</option>
                  {teams.filter((t) => t.active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <select name="responsible_user">
                  <option value="">Técnico específico (opcional)</option>
                  {users.filter((u) => u.active).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
                </select>
                <label><input name="notify_failure" type="checkbox" defaultChecked /> Avisar na falha</label>
                <label><input name="notify_recovery" type="checkbox" defaultChecked /> Avisar ao normalizar</label>
                <label><input name="auto_resolve" type="checkbox" defaultChecked /> Encerrar chamado ao normalizar</label>
                <button>Criar</button>
              </form>
              <DataGrid
                columns={[
                  { key: "name", label: "Serviço" },
                  { key: "asset_name", label: "Ativo" },
                  { key: "monitor_type", label: "Tipo" },
                  { key: "responsible_team_name", label: "Equipe", render: (m) => (
                    <select value={m.responsible_team_id || ""} onChange={(e) => submit(`monitors/${m.id}`, {
                      asset_id:m.asset_id,name:m.name,monitor_type:m.monitor_type,configuration:m.configuration,
                      interval_seconds:m.interval_seconds,severity:m.severity,active:m.active,
                      responsible_team_id:e.target.value || null,responsible_user_id:m.responsible_user_id,
                      notify_on_failure:m.notify_on_failure,notify_on_recovery:m.notify_on_recovery,
                      auto_resolve_incident:m.auto_resolve_incident,
                    },refresh,"PUT")}>
                      <option value="">Sem equipe</option>{teams.filter((t) => t.active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                  )},
                  { key: "responsible_user_name", label: "Técnico", render: (m) => (
                    <select value={m.responsible_user_id || ""} onChange={(e) => submit(`monitors/${m.id}`, {
                      asset_id:m.asset_id,name:m.name,monitor_type:m.monitor_type,configuration:m.configuration,
                      interval_seconds:m.interval_seconds,severity:m.severity,active:m.active,
                      responsible_team_id:m.responsible_team_id,responsible_user_id:e.target.value || null,
                      notify_on_failure:m.notify_on_failure,notify_on_recovery:m.notify_on_recovery,
                      auto_resolve_incident:m.auto_resolve_incident,
                    },refresh,"PUT")}>
                      <option value="">Sem técnico</option>{users.filter((u) => u.active).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
                    </select>
                  )},
                  {
                    key: "active",
                    label: "Cadastro",
                    render: (r) => (
                      <Status value={r.active ? "ativo" : "inativo"} />
                    ),
                  },
                  {
                    key: "last_status",
                    label: "Estado",
                    render: (r) => <Status value={r.last_status} />,
                  },
                  { key: "last_message", label: "Última coleta" },
                ]}
                rows={monitors}
                actions={(m) => (
                  <>
                    <button
                      onClick={() => {
                        const name = ask("Nome", m.name);
                        if (name)
                          submit(
                            `monitors/${m.id}`,
                            {
                              asset_id: m.asset_id,
                              name,
                              monitor_type: m.monitor_type,
                              configuration: m.configuration,
                              interval_seconds: m.interval_seconds,
                              severity: m.severity,
                              active: m.active,
                              responsible_team_id: m.responsible_team_id,
                              responsible_user_id: m.responsible_user_id,
                              notify_on_failure: m.notify_on_failure,
                              notify_on_recovery: m.notify_on_recovery,
                              auto_resolve_incident: m.auto_resolve_incident,
                            },
                            refresh,
                            "PUT",
                          );
                      }}
                    >
                      Editar
                    </button>
                    <button
                      onClick={() =>
                        submit(
                          `monitors/${m.id}`,
                          {
                            asset_id: m.asset_id,
                            name: m.name,
                            monitor_type: m.monitor_type,
                            configuration: m.configuration,
                            interval_seconds: m.interval_seconds,
                            severity: m.severity,
                            active: !m.active,
                            responsible_team_id: m.responsible_team_id,
                            responsible_user_id: m.responsible_user_id,
                            notify_on_failure: m.notify_on_failure,
                            notify_on_recovery: m.notify_on_recovery,
                            auto_resolve_incident: m.auto_resolve_incident,
                          },
                          refresh,
                          "PUT",
                        )
                      }
                    >
                      {m.active ? "Inativar" : "Ativar"}
                    </button>
                    <button
                      className="danger"
                      onClick={() => remove("monitors", m.id, m.name)}
                    >
                      Excluir
                    </button>
                  </>
                )}
              />
            </section>
          )}
          {view === "databases" && (
            <section className="database-workspace">
              <div className="section-head database-heading">
                <div>
                  <h2>Monitoramento SQL Server</h2>
                  <p>
                    Morada • Diagnóstico, Dash, Histórico e evolução, Desempenho
                    e scripts corretivos.
                  </p>
                </div>
                <a
                  className="button-link"
                  href="/db-monitor/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Abrir em tela cheia
                </a>
              </div>
              {tenantId && tenant?.slug !== "morada" ? (
                <section className="panel">
                  <Empty>
                    Esta empresa ainda não possui um coletor SQL Server
                    configurado. Selecione Morada ou Todas as empresas.
                  </Empty>
                </section>
              ) : (
                <iframe
                  className="database-frame"
                  src="/db-monitor/"
                  title="Monitoramento SQL Server da Morada"
                />
              )}
            </section>
          )}
          {view === "alerts" && (
            <section className="panel">
              <h2>Alertas</h2>
              <DataGrid
                columns={[
                  { key: "title", label: "Título" },
                  {
                    key: "severity",
                    label: "Severidade",
                    render: (r) => <Status value={r.severity} />,
                  },
                  {
                    key: "status",
                    label: "Estado",
                    render: (r) => <Status value={r.status} />,
                  },
                  {
                    key: "opened_at",
                    label: "Abertura",
                    render: (r) => fmt(r.opened_at),
                  },
                ]}
                rows={alerts}
                actions={(a) => (
                  <>
                    <button
                      onClick={() => {
                        const title = ask("Título", a.title);
                        if (title)
                          submit(
                            `alerts/${a.id}`,
                            { title, severity: a.severity, status: a.status },
                            refresh,
                            "PUT",
                          );
                      }}
                    >
                      Editar
                    </button>
                    <button
                      onClick={() =>
                        submit(
                          `alerts/${a.id}`,
                          {
                            title: a.title,
                            severity: a.severity,
                            status: a.status === "ignored" ? "open" : "ignored",
                          },
                          refresh,
                          "PUT",
                        )
                      }
                    >
                      {a.status === "ignored" ? "Reativar" : "Ignorar"}
                    </button>
                    <button
                      className="danger"
                      onClick={() => remove("alerts", a.id, a.title)}
                    >
                      Excluir
                    </button>
                  </>
                )}
              />
            </section>
          )}
          {view === "incidents" && (
            <div className="incident-screen">
              {!detail ? (
              <section className="panel">
                <h2>Incidentes</h2>
                <p>Selecione uma linha para abrir a tela de atendimento do incidente.</p>
                <DataGrid
                  columns={incidentCols}
                  rows={incidents}
                  onRow={loadDetail}
                  actions={(i) => (
                    <>
                      <button onClick={() => loadDetail(i)}>Detalhes</button>
                      <button onClick={() => updateIncident(i, "in_progress")}>
                        Atender
                      </button>
                    </>
                  )}
                />
              </section>
              ) : (
                <section className="panel incident-detail-page">
                  <div className="section-head">
                    <div>
                      <button className="back-button" onClick={() => { setDetail(null); setSelected(null); }}>← Voltar para incidentes</button>
                      <h2>{detail.incident.title}</h2>
                      <p>Atendimento, responsáveis, prazos e histórico do incidente.</p>
                    </div>
                  </div>
                  <div className="detail-grid">
                    <span>
                      Estado
                      <b>
                        <Status value={detail.incident.status} />
                      </b>
                    </span>
                    <span>
                      Prioridade<b>{detail.incident.priority}</b>
                    </span>
                    <span>
                      SLA
                      <b>
                        <Status value={detail.incident.sla_status} />
                      </b>
                    </span>
                    <span>
                      GLPI<b>#{detail.incident.glpi_ticket_id || "—"}</b>
                    </span>
                    <span>
                      Prazo resposta
                      <b>{fmt(detail.incident.response_due_at)}</b>
                    </span>
                    <span>
                      Prazo solução
                      <b>{fmt(detail.incident.resolution_due_at)}</b>
                    </span>
                  </div>
                  <div className="incident-controls">
                    <label className="field">
                      <span>Estado</span>
                      <select
                        defaultValue={detail.incident.status}
                        onChange={(e) =>
                          updateIncident(detail.incident, e.target.value)
                        }
                      >
                        {[
                          "new",
                          "acknowledged",
                          "in_progress",
                          "waiting_customer",
                          "waiting_supplier",
                          "resolved",
                          "closed",
                          "ignored",
                        ].map((s) => (
                          <option key={s} value={s}>
                            {translated(s)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Prioridade</span>
                      <select
                        defaultValue={detail.incident.priority}
                        onChange={(e) =>
                          updateIncident(
                            detail.incident,
                            detail.incident.status,
                            { priority: e.target.value },
                          )
                        }
                      >
                        {["low", "medium", "high", "critical"].map((s) => (
                          <option key={s} value={s}>
                            {translated(s)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Equipe responsável</span>
                      <select
                        defaultValue={detail.incident.assignee_team_id || ""}
                        onChange={(e) =>
                          updateIncident(
                            detail.incident,
                            detail.incident.status,
                            { assignee_team_id: e.target.value || null },
                          )
                        }
                      >
                        <option value="">Selecione uma equipe</option>
                        {teams
                          .filter((t) => t.active)
                          .map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.name}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Usuário responsável</span>
                      <select
                        defaultValue={detail.incident.assignee_user_id || ""}
                        onChange={(e) =>
                          updateIncident(
                            detail.incident,
                            detail.incident.status,
                            { assignee_user_id: e.target.value || null },
                          )
                        }
                      >
                        <option value="">Selecione um usuário</option>
                        {users
                          .filter((u) => u.active)
                          .map((u) => (
                            <option key={u.id} value={u.id}>
                              {u.display_name || u.email}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Fornecedor</span>
                      <select
                        defaultValue={detail.incident.supplier_id || ""}
                        onChange={(e) =>
                          e.target.value &&
                          updateIncident(detail.incident, "waiting_supplier", {
                            supplier_id: e.target.value,
                          })
                        }
                      >
                        <option value="">Selecione um fornecedor</option>
                        {suppliers
                          .filter((s) => s.active)
                          .map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  </div>
                  <h3>Dados do incidente</h3>
                  <form
                    key={`incident-data-${detail.incident.id}-${detail.incident.version}`}
                    className="incident-data-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const f = new FormData(e.currentTarget);
                      updateIncident(detail.incident, detail.incident.status, {
                        title: f.get("title"),
                        description: f.get("description") || null,
                        category: f.get("category") || null,
                        supplier_protocol: f.get("supplier_protocol") || null,
                      });
                    }}
                  >
                    <Input name="title" label="Título" defaultValue={detail.incident.title} required />
                    <Input name="category" label="Categoria" defaultValue={detail.incident.category || ""} />
                    <Input name="supplier_protocol" label="Protocolo do fornecedor" defaultValue={detail.incident.supplier_protocol || ""} />
                    <label className="field incident-description">
                      <span>Descrição detalhada</span>
                      <textarea name="description" defaultValue={detail.incident.description || ""} rows="5" />
                    </label>
                    <div className="incident-form-actions"><button>Salvar detalhes</button></div>
                  </form>
                  <h3>Comentários</h3>
                  {detail.comments.map((c) => (
                    <article className="timeline" key={c.id}>
                      <b>{c.author_name || "Sistema"}</b>
                      <small>
                        {fmt(c.created_at)} {c.is_internal ? "• Interno" : ""}
                      </small>
                      <p>{c.body}</p>
                    </article>
                  ))}
                  <form
                    className="form-row"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const f = new FormData(e.currentTarget);
                      submit(
                        `incidents/${selected.id}/comments`,
                        {
                          body: f.get("body"),
                          is_internal: f.get("internal") === "on",
                        },
                        () => loadDetail(selected),
                      );
                      e.currentTarget.reset();
                    }}
                  >
                    <input
                      name="body"
                      placeholder="Adicionar comentário"
                      required
                    />
                    <label>
                      <input name="internal" type="checkbox" /> Interno
                    </label>
                    <button>Enviar</button>
                  </form>
                  <h3>Histórico</h3>
                  {detail.history.map((h) => (
                    <article className="timeline" key={h.id}>
                      <b>{h.event_type}</b>
                      <small>
                        {fmt(h.created_at)} • {h.actor_name || "Sistema"}
                      </small>
                    </article>
                  ))}
                </section>
              )}
            </div>
          )}
          {view === "suppliers" && (
            <section className="panel">
              <h2>Fornecedores e terceiros</h2>
              <form
                className="form-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  submit("suppliers", {
                    name: f.get("name"),
                    contact_name: f.get("contact"),
                    email: f.get("email") || null,
                    phone: f.get("phone"),
                    services: f.get("services"),
                    response_minutes: Number(f.get("response")),
                    resolution_minutes: Number(f.get("resolution")),
                  });
                  e.currentTarget.reset();
                }}
              >
                <input name="name" placeholder="Fornecedor" required />
                <input name="contact" placeholder="Contato" />
                <input name="email" type="email" placeholder="E-mail" />
                <input name="phone" placeholder="Telefone" />
                <input name="services" placeholder="Serviços" />
                <input name="response" type="number" defaultValue="240" />
                <input name="resolution" type="number" defaultValue="1440" />
                <button>Cadastrar</button>
              </form>
              <DataGrid
                columns={[
                  { key: "name", label: "Fornecedor" },
                  { key: "contact_name", label: "Contato" },
                  { key: "services", label: "Serviços" },
                  { key: "resolution_minutes", label: "SLA solução (min)" },
                  {
                    key: "active",
                    label: "Estado",
                    render: (r) => (
                      <Status value={r.active ? "ativo" : "inativo"} />
                    ),
                  },
                ]}
                rows={suppliers}
                actions={(s) => (
                  <>
                    <button
                      onClick={() => {
                        const name = ask("Nome", s.name);
                        if (name)
                          submit(
                            `suppliers/${s.id}`,
                            supplierBody(s, { name }),
                            refresh,
                            "PUT",
                          );
                      }}
                    >
                      Editar
                    </button>
                    <button
                      onClick={() =>
                        submit(
                          `suppliers/${s.id}`,
                          supplierBody(s, { active: !s.active }),
                          refresh,
                          "PUT",
                        )
                      }
                    >
                      {s.active ? "Inativar" : "Ativar"}
                    </button>
                    <button
                      className="danger"
                      onClick={() => remove("suppliers", s.id, s.name)}
                    >
                      Excluir
                    </button>
                  </>
                )}
              />
            </section>
          )}
          {view === "sla" && (
            <section className="panel">
              <h2>Políticas de SLA</h2>
              <form
                className="form-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  submit("sla-policies", {
                    name: f.get("name"),
                    priority: f.get("priority"),
                    response_minutes: Number(f.get("response")),
                    resolution_minutes: Number(f.get("resolution")),
                    coverage: f.get("coverage"),
                  });
                  e.currentTarget.reset();
                }}
              >
                <input name="name" placeholder="Nome" required />
                <select name="priority">
                  {["low", "medium", "high", "critical"].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
                <input
                  name="response"
                  type="number"
                  placeholder="Resposta (min)"
                  required
                />
                <input
                  name="resolution"
                  type="number"
                  placeholder="Solução (min)"
                  required
                />
                <select name="coverage">
                  <option value="24x7">24×7</option>
                  <option value="business_hours">Horário comercial</option>
                </select>
                <button>Salvar</button>
              </form>
              <DataGrid
                columns={[
                  { key: "name", label: "Política" },
                  {
                    key: "priority",
                    label: "Prioridade",
                    render: (r) => <Status value={r.priority} />,
                  },
                  { key: "response_minutes", label: "Resposta" },
                  { key: "resolution_minutes", label: "Solução" },
                  { key: "coverage", label: "Cobertura" },
                  {
                    key: "active",
                    label: "Estado",
                    render: (r) => (
                      <Status value={r.active ? "ativo" : "inativo"} />
                    ),
                  },
                ]}
                rows={slas}
                actions={(s) => (
                  <>
                    <button
                      onClick={() => {
                        const name = ask("Nome", s.name);
                        if (name)
                          submit(
                            `sla-policies/${s.id}`,
                            slaBody(s, { name }),
                            refresh,
                            "PUT",
                          );
                      }}
                    >
                      Editar
                    </button>
                    <button
                      onClick={() =>
                        submit(
                          `sla-policies/${s.id}`,
                          slaBody(s, { active: !s.active }),
                          refresh,
                          "PUT",
                        )
                      }
                    >
                      {s.active ? "Inativar" : "Ativar"}
                    </button>
                    <button
                      className="danger"
                      onClick={() => remove("sla-policies", s.id, s.name)}
                    >
                      Excluir
                    </button>
                  </>
                )}
              />
            </section>
          )}
          {view === "automations" && (
            <section className="panel">
              <h2>Automações homologadas</h2>
              <p>Permissão por ativo, limite horário e cooldown.</p>
              <form
                className="form-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  submit("actions/grants", {
                    asset_id: f.get("asset"),
                    action_code: f.get("action"),
                    enabled: true,
                    max_per_hour: Number(f.get("limit")),
                    cooldown_seconds: Number(f.get("cooldown")),
                  });
                }}
              >
                <select name="asset">
                  {assets.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <select name="action">
                  {actions.definitions.map((a) => (
                    <option key={a.code} value={a.code}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <input name="limit" type="number" defaultValue="1" />
                <input name="cooldown" type="number" defaultValue="1800" />
                <button>Autorizar</button>
              </form>
              <DataGrid
                columns={[
                  { key: "asset_name", label: "Ativo" },
                  { key: "name", label: "Ação" },
                  {
                    key: "enabled",
                    label: "Autorizada",
                    render: (r) => (
                      <Status value={r.enabled ? "ativo" : "inativo"} />
                    ),
                  },
                  { key: "max_per_hour", label: "Limite/h" },
                ]}
                rows={actions.grants}
                actions={(g) => (
                  <>
                    <button
                      onClick={() =>
                        submit("actions/execute", {
                          asset_id: g.asset_id,
                          action_code: g.code,
                          idempotency_key: crypto.randomUUID(),
                        })
                      }
                    >
                      Executar
                    </button>
                    <button
                      className="danger"
                      onClick={() => remove("actions/grants", g.id, g.name)}
                    >
                      Excluir
                    </button>
                  </>
                )}
              />
              <h3>Execuções</h3>
              <DataGrid
                columns={[
                  {
                    key: "created_at",
                    label: "Data",
                    render: (r) => fmt(r.created_at),
                  },
                  { key: "asset_name", label: "Ativo" },
                  { key: "name", label: "Ação" },
                  {
                    key: "status",
                    label: "Estado",
                    render: (r) => <Status value={r.status} />,
                  },
                  { key: "requested_by_name", label: "Solicitante" },
                ]}
                rows={actions.executions}
              />
            </section>
          )}
          {view === "reports" && (
            <section className="panel">
              <div className="section-head">
                <div>
                  <h2>Indicadores operacionais</h2>
                  <p>Atendimento, SLA e terceiros.</p>
                </div>
                <div className="row-actions">
                  <button onClick={() => run(() => download("pdf"))}>
                    Exportar PDF
                  </button>
                  <button onClick={() => run(() => download("xlsx"))}>
                    Exportar Excel
                  </button>
                </div>
              </div>
              <section className="company-kpis">
                <article>
                  <strong>{report.totals?.total || 0}</strong>
                  <span>Incidentes</span>
                </article>
                <article>
                  <strong>{report.totals?.open || 0}</strong>
                  <span>Em aberto</span>
                </article>
                <article>
                  <strong>{report.totals?.mtta_minutes || "—"}</strong>
                  <span>MTTA (min)</span>
                </article>
                <article>
                  <strong>{report.totals?.mttr_minutes || "—"}</strong>
                  <span>MTTR (min)</span>
                </article>
              </section>
              <h3>Por prioridade</h3>
              <DataGrid
                columns={[
                  {
                    key: "priority",
                    label: "Prioridade",
                    render: (r) => <Status value={r.priority} />,
                  },
                  { key: "total", label: "Total" },
                ]}
                rows={report.by_priority.map((x, i) => ({ ...x, id: i }))}
              />
              <h3>Fornecedores</h3>
              <DataGrid
                columns={[
                  { key: "name", label: "Fornecedor" },
                  { key: "incidents", label: "Incidentes" },
                  { key: "breached", label: "SLA vencido" },
                ]}
                rows={report.suppliers.map((x, i) => ({ ...x, id: i }))}
              />
            </section>
          )}
          {view === "settings" && (
            <div className="ops-grid">
              <section className="panel">
                <h2>Equipes</h2>
                <form
                  key={editingTeam?.id || "new-team"}
                  className="form-row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    const split = (value) => String(value || "").split(/[\n,;]+/).map((v) => v.trim()).filter(Boolean);
                    submit(editingTeam ? `teams/${editingTeam.id}` : "teams", {
                      name: f.get("name"),
                      emails: split(f.get("emails")),
                      whatsapp_numbers: split(f.get("whatsapp_numbers")),
                      whatsapp_group_id: f.get("whatsapp_group_id") || null,
                      member_user_ids: Array.from(e.currentTarget.elements.members.selectedOptions).map((o) => o.value),
                      active: editingTeam?.active ?? true,
                    }, () => { setEditingTeam(null); refresh(); }, editingTeam ? "PUT" : "POST");
                    e.currentTarget.reset();
                  }}
                >
                  <input name="name" placeholder="Nome da equipe" defaultValue={editingTeam?.name || ""} required />
                  <textarea name="emails" placeholder="E-mails (um por linha)" defaultValue={(editingTeam?.emails || []).join("\n")} />
                  <textarea name="whatsapp_numbers" placeholder="WhatsApp com país e DDD (um por linha)" defaultValue={(editingTeam?.whatsapp_numbers || []).join("\n")} />
                  <input name="whatsapp_group_id" placeholder="ID do grupo WhatsApp (ex.: ...@g.us)" defaultValue={editingTeam?.whatsapp_group_id || ""} />
                  <label className="field"><span>Técnicos da equipe</span><select name="members" multiple defaultValue={(editingTeam?.member_user_ids || []).map(String)}>
                    {users.filter((u) => u.active).map((u) => <option key={u.id} value={u.id}>{u.display_name} — {u.email}</option>)}
                  </select></label>
                  <button>{editingTeam ? "Salvar" : "Criar"}</button>
                  {editingTeam && <button type="button" className="secondary" onClick={() => setEditingTeam(null)}>Cancelar</button>}
                </form>
                <DataGrid
                  columns={[
                    { key: "name", label: "Equipe" },
                    { key: "emails", label: "E-mails", render: (r) => (r.emails || []).join(", ") || "—" },
                    { key: "whatsapp_numbers", label: "WhatsApp", render: (r) => (r.whatsapp_numbers || []).join(", ") || "—" },
                    { key: "whatsapp_group_id", label: "Grupo WhatsApp", render: (r) => r.whatsapp_group_id || "—" },
                    { key: "member_names", label: "Técnicos", render: (r) => r.member_names || "—" },
                    {
                      key: "active",
                      label: "Estado",
                      render: (r) => (
                        <Status value={r.active ? "ativo" : "inativo"} />
                      ),
                    },
                  ]}
                  rows={teams}
                  actions={(t) => (
                    <>
                      <button
                        onClick={() => setEditingTeam(t)}
                      >
                        Editar
                      </button>
                      <button
                        onClick={() =>
                          submit(
                            `teams/${t.id}`,
                            { name: t.name, emails: t.emails || [], whatsapp_numbers: t.whatsapp_numbers || [], whatsapp_group_id: t.whatsapp_group_id, member_user_ids: t.member_user_ids || [], active: !t.active },
                            refresh,
                            "PUT",
                          )
                        }
                      >
                        {t.active ? "Inativar" : "Ativar"}
                      </button>
                      <button
                        className="danger"
                        onClick={() => remove("teams", t.id, t.name)}
                      >
                        Excluir
                      </button>
                    </>
                  )}
                />
              </section>
              <section className="panel">
                <h2>Notificações</h2>
                <form
                  className="stack-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    submit("notification-rules", {
                      name: f.get("name"),
                      channel: f.get("channel"),
                      target: f.get("target"),
                      minimum_priority: f.get("priority"),
                      events: [
                        "incident.created",
                        "incident.resolved",
                        "incident.in_progress",
                      ],
                    });
                    e.currentTarget.reset();
                  }}
                >
                  <Input name="name" label="Nome" required />
                  <label className="field">
                    <span>Canal</span>
                    <select name="channel">
                      <option value="email">E-mail</option>
                      <option value="webhook">Webhook</option>
                    </select>
                  </label>
                  <Input name="target" label="Destino" required />
                  <label className="field">
                    <span>Prioridade mínima</span>
                    <select name="priority">
                      <option>low</option>
                      <option>medium</option>
                      <option>high</option>
                      <option>critical</option>
                    </select>
                  </label>
                  <button>Salvar</button>
                </form>
                <DataGrid
                  columns={[
                    { key: "name", label: "Regra" },
                    { key: "channel", label: "Canal" },
                    { key: "target", label: "Destino" },
                    { key: "minimum_priority", label: "Prioridade" },
                    {
                      key: "active",
                      label: "Estado",
                      render: (r) => (
                        <Status value={r.active ? "ativo" : "inativo"} />
                      ),
                    },
                  ]}
                  rows={rules}
                  actions={(r) => (
                    <>
                      <button
                        onClick={() => {
                          const name = ask("Nome", r.name);
                          if (name)
                            submit(
                              `notification-rules/${r.id}`,
                              ruleBody(r, { name }),
                              refresh,
                              "PUT",
                            );
                        }}
                      >
                        Editar
                      </button>
                      <button
                        onClick={() =>
                          submit(
                            `notification-rules/${r.id}`,
                            ruleBody(r, { active: !r.active }),
                            refresh,
                            "PUT",
                          )
                        }
                      >
                        {r.active ? "Inativar" : "Ativar"}
                      </button>
                      <button
                        className="danger"
                        onClick={() =>
                          remove("notification-rules", r.id, r.name)
                        }
                      >
                        Excluir
                      </button>
                    </>
                  )}
                />
              </section>
              <section className="panel">
                <h2>Janelas de manutenção</h2>
                <form
                  className="stack-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    submit("maintenance-windows", {
                      name: f.get("name"),
                      asset_id: f.get("asset") || null,
                      starts_at: new Date(f.get("start")).toISOString(),
                      ends_at: new Date(f.get("end")).toISOString(),
                      reason: f.get("reason"),
                    });
                    e.currentTarget.reset();
                  }}
                >
                  <Input name="name" label="Nome" required />
                  <label className="field">
                    <span>Ativo</span>
                    <select name="asset">
                      <option value="">Todos</option>
                      {assets.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Input
                    name="start"
                    label="Início"
                    type="datetime-local"
                    required
                  />
                  <Input
                    name="end"
                    label="Fim"
                    type="datetime-local"
                    required
                  />
                  <Input name="reason" label="Motivo" />
                  <button>Agendar</button>
                </form>
                <DataGrid
                  columns={[
                    { key: "name", label: "Janela" },
                    { key: "asset_name", label: "Ativo" },
                    {
                      key: "starts_at",
                      label: "Início",
                      render: (r) => fmt(r.starts_at),
                    },
                    {
                      key: "ends_at",
                      label: "Fim",
                      render: (r) => fmt(r.ends_at),
                    },
                    {
                      key: "active",
                      label: "Estado",
                      render: (r) => (
                        <Status value={r.active ? "ativo" : "inativo"} />
                      ),
                    },
                  ]}
                  rows={maintenance}
                  actions={(m) => (
                    <>
                      <button
                        onClick={() => {
                          const name = ask("Nome", m.name);
                          if (name)
                            submit(
                              `maintenance-windows/${m.id}`,
                              maintenanceBody(m, { name }),
                              refresh,
                              "PUT",
                            );
                        }}
                      >
                        Editar
                      </button>
                      <button
                        onClick={() =>
                          submit(
                            `maintenance-windows/${m.id}`,
                            maintenanceBody(m, { active: !m.active }),
                            refresh,
                            "PUT",
                          )
                        }
                      >
                        {m.active ? "Inativar" : "Ativar"}
                      </button>
                      <button
                        className="danger"
                        onClick={() =>
                          remove("maintenance-windows", m.id, m.name)
                        }
                      >
                        Excluir
                      </button>
                    </>
                  )}
                />
              </section>
            </div>
          )}
          {view === "admin" && (
            <div className="ops-grid">
              <section className="panel">
                <h2>Usuários e perfis</h2>
                <DataGrid
                  columns={[
                    { key: "display_name", label: "Usuário" },
                    { key: "email", label: "E-mail" },
                    { key: "whatsapp_number", label: "WhatsApp", render: (r) => r.whatsapp_number || "—" },
                    {
                      key: "role",
                      label: "Perfil",
                      render: (r) => <Status value={r.role} />,
                    },
                    {
                      key: "active",
                      label: "Estado",
                      render: (r) => (
                        <Status value={r.active ? "ativo" : "inativo"} />
                      ),
                    },
                  ]}
                  rows={users}
                  actions={(u) => (
                    <>
                      <button
                        onClick={() => {
                          const whatsapp_number = ask("WhatsApp com país e DDD (deixe vazio para remover)", u.whatsapp_number || "");
                          if (whatsapp_number !== null) submit(`users/${u.id}/notifications`, { whatsapp_number: whatsapp_number || null }, refresh, "PUT");
                        }}
                      >
                        Contato
                      </button>
                      <button
                        onClick={() => {
                          const role = ask(
                            "Perfil: admin, manager, operator, viewer, client ou auditor",
                            u.role,
                          );
                          if (role)
                            submit("users/membership", { user_id: u.id, role });
                        }}
                      >
                        Alterar perfil
                      </button>
                      <button
                        onClick={() =>
                          submit(
                            `users/${u.id}/active?active=${!u.active}`,
                            {},
                            refresh,
                            "PATCH",
                          )
                        }
                      >
                        {u.active ? "Inativar" : "Ativar"}
                      </button>
                    </>
                  )}
                />
              </section>
              <section className="panel">
                <h2>Motores operacionais</h2>
                <div className="admin-grid">
                  <article>
                    <span>Zabbix</span>
                    <strong>Interno</strong>
                    <small>Coleta e eventos</small>
                  </article>
                  <article>
                    <span>GLPI</span>
                    <strong>Interno</strong>
                    <small>Chamados e SLA</small>
                  </article>
                  <article>
                    <span>Segurança</span>
                    <strong>Keycloak</strong>
                    <small>OIDC e isolamento</small>
                  </article>
                </div>
              </section>
              <section className="panel wide-panel">
                <h2>Auditoria</h2>
                <DataGrid
                  columns={[
                    {
                      key: "created_at",
                      label: "Data",
                      render: (r) => fmt(r.created_at),
                    },
                    { key: "user_name", label: "Usuário" },
                    { key: "action", label: "Ação" },
                    { key: "object_type", label: "Objeto" },
                    { key: "object_id", label: "Identificador" },
                  ]}
                  rows={audit}
                />
              </section>
            </div>
          )}
          {view === "settings" &&
            (tenantId ? (
              <NotificationChannels
                settings={notificationSettings}
                tenantName={tenant?.name}
                tenantId={tenantId}
                token={token}
                submit={submit}
                refresh={refresh}
                run={run}
              />
            ) : (
              <section className="panel">
                <Empty>
                  Selecione uma empresa para parametrizar os canais de
                  notificação.
                </Empty>
              </section>
            ))}{" "}
        </main>
      </div>
      <Toast notice={notice} onClose={() => setNotice(null)} />
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
