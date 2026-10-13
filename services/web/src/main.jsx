import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import "./grid.css";
import "./nexora.css";
import "./home.css";
import { isTenantResponseCurrent } from "./tenant-request-guard.js";
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
let refreshPromise = null;
function storeTokens(tokens) {
  sessionStorage.setItem("access_token", tokens.access_token);
  if (tokens.refresh_token) sessionStorage.setItem("refresh_token", tokens.refresh_token);
  if (tokens.id_token) sessionStorage.setItem("id_token", tokens.id_token);
  return tokens.access_token;
}
async function refreshAccessToken() {
  if (refreshPromise) return refreshPromise;
  const refreshToken = sessionStorage.getItem("refresh_token");
  if (!refreshToken) throw Error("Sessão sem credencial de renovação");
  refreshPromise = fetch(`${authConfig.realmUrl}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: authConfig.clientId,
      refresh_token: refreshToken,
    }),
  })
    .then(async (response) => {
      if (!response.ok) throw Error("Não foi possível renovar a sessão");
      return storeTokens(await response.json());
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}
async function validAccessToken(force = false) {
  const current = sessionStorage.getItem("access_token");
  if (!current) throw Error("Sessão não iniciada");
  try {
    const expiresAt = Number(parseToken(current).exp || 0) * 1000;
    if (!force && expiresAt > Date.now() + 120000) return current;
  } catch {
    if (!force) return refreshAccessToken();
  }
  return refreshAccessToken();
}
async function startLogin() {
  const v = randomValue(),
    c = encode(
      new Uint8Array(
        await crypto.subtle.digest("SHA-256", new TextEncoder().encode(v)),
      ),
    ),
    s = randomValue(),
    n = randomValue();
  sessionStorage.setItem("oidc_verifier", v);
  sessionStorage.setItem("oidc_state", s);
  sessionStorage.setItem("oidc_nonce", n);
  location.assign(
    `${authConfig.realmUrl}/protocol/openid-connect/auth?${new URLSearchParams({ client_id: authConfig.clientId, redirect_uri: authConfig.redirectUri, response_type: "code", scope: "openid profile email", code_challenge: c, code_challenge_method: "S256", state: s, nonce: n })}`,
  );
}
async function finishLogin() {
  const q = new URLSearchParams(location.search),
    code = q.get("code");
  if (!code) {
    const existing = sessionStorage.getItem("access_token");
    if (existing && !sessionStorage.getItem("refresh_token")) {
      sessionStorage.removeItem("access_token");
      sessionStorage.removeItem("id_token");
      return null;
    }
    return existing ? validAccessToken() : null;
  }
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
  const expectedNonce = sessionStorage.getItem("oidc_nonce");
  if (!t.id_token || !expectedNonce || parseToken(t.id_token).nonce !== expectedNonce) {
    sessionStorage.removeItem("oidc_verifier");
    sessionStorage.removeItem("oidc_state");
    sessionStorage.removeItem("oidc_nonce");
    throw Error("Nonce de login inválido");
  }
  storeTokens(t);
  sessionStorage.removeItem("oidc_verifier");
  sessionStorage.removeItem("oidc_state");
  sessionStorage.removeItem("oidc_nonce");
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
class ApiError extends Error {
  constructor(message, { status = 0, code = "request_failed", correlationId = "", retryable = false, invalidFields = [] } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
    this.retryable = retryable;
    this.invalidFields = invalidFields;
  }
}
async function api(path, token, options = {}, tenant = "") {
  const request = (accessToken) => fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
      ...(tenant ? { "X-Tenant-ID": tenant } : {}),
      ...(options.headers || {}),
    },
  });
  let r = await request(await validAccessToken());
  if (r.status === 401) {
    try {
      r = await request(await validAccessToken(true));
    } catch {
      throw new ApiError("Sua sessão expirou. Entre novamente para continuar.", { status: 401, code: "session_expired" });
    }
  }
  if (r.status === 401) throw new ApiError("Sua sessão expirou. Entre novamente para continuar.", { status: 401, code: "session_expired" });
  if (r.status === 204) return null;
  const responseText = await r.text();
  let b = null;
  if (responseText) {
    try {
      b = JSON.parse(responseText);
    } catch {
      b = responseText;
    }
  }
  if (!r.ok) {
    const message = typeof b?.message === "string"
      ? `${b.message}${b.correlation_id ? ` (correlação: ${b.correlation_id})` : ""}`
      : typeof b?.detail === "string"
      ? b.detail
      : typeof b === "string" && b !== "Internal Server Error"
        ? b
        : `Não foi possível concluir a operação (erro ${r.status}).`;
    throw new ApiError(message, {
      status: r.status,
      code: b?.code || `http_${r.status}`,
      correlationId: b?.correlation_id || r.headers.get("X-Correlation-ID") || "",
      retryable: Boolean(b?.retryable),
      invalidFields: Array.isArray(b?.invalid_fields) ? b.invalid_fields : [],
    });
  }
  return b;
}
const menu = [
    ["dashboard", "Dashboard", "home"],
    ["portal", "Portal", "catalog"],
    ["clients", "Empresas", "building"],
    ["monitoring", "Monitoramento", "activity"],
    ["incidents", "Incidentes", "alert"],
    ["operations", "Operação", "automation"],
    ["reports", "Relatórios", "report"],
    ["settings", "Configurações", "settings"],
    ["admin", "Administração", "admin"],
  ],
  dashboardMenu = [
    ["overview", "Visão Geral", "home"],
    ["noc", "NOC", "activity"],
    ["databases", "Monitoramento Banco de Dados", "database"],
  ],
  portalMenu = [
    ["portal", "Catálogo disponível", "catalog"],
    ["portal-requests", "Minhas solicitações", "inbox"],
  ],
  monitoringMenu = [
    ["assets", "Ambientes", "server"],
    ["asset-topology", "Ativos e topologia", "server"],
    ["monitors", "Serviços", "activity"],
    ["alerts", "Alertas", "bell"],
    ["event-quarantine", "Quarentena de eventos", "alert"],
  ],
  operationsMenu = [
    ["queues", "Filas de atendimento", "inbox"],
    ["my-queue", "Minha fila", "inbox"],
    ["workload", "Carga e agenda", "team"],
    ["time-entries", "Apontamentos", "clock"],
    ["contracts", "Contratos", "contract"],
    ["consumption", "Consumo", "report"],
    ["billing", "Pré-faturamento", "billing"],
    ["quality", "Qualidade", "quality"],
    ["profitability", "Rentabilidade", "profit"],
    ["major-incidents", "Incidentes maiores", "alert"],
    ["knowledge", "Conhecimento", "catalog"],
    ["problems", "Problemas", "alert"],
    ["changes", "Mudanças", "calendar"],
    ["runbooks", "Runbooks", "automation"],
    ["catalog", "Catálogo de serviços", "catalog"],
    ["suppliers", "Terceiros", "users"],
    ["sla", "SLA", "clock"],
    ["automations", "Automações", "automation"],
  ],
  settingsMenu = [
    ["settings-notifications", "Notificações", "bell"],
    ["settings-email", "E-mail SMTP", "mail"],
    ["settings-whatsapp", "WhatsApp", "message"],
  ],
  adminMenu = [
    ["admin-users", "Usuários", "users"],
    ["admin-teams", "Equipes", "team"],
    ["admin-agents", "Agentes de coleta", "server"],
    ["admin-maintenance", "Janelas de manutenção", "calendar"],
    ["admin-integrations", "Integrações", "automation"],
    ["admin-pending", "Operações pendentes", "inbox"],
    ["admin-release", "Liberação gradual", "audit"],
    ["admin", "Logs de auditoria", "audit"],
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
    unknown: "Sem dados",
  },
  translated = (value) =>
    labels[value] || String(value || "Desconhecido").replaceAll("_", " "),
  fmt = (v) => (v ? new Date(v).toLocaleString("pt-BR") : "—"),
  databaseLabel = (value) => typeof value === "string" ? value.replace(/^Benner SQL(?:\s*-)?/i, "Banco de Dados") : value;
const navigationSubmenus = {
    dashboard: dashboardMenu,
    portal: portalMenu,
    monitoring: monitoringMenu,
    operations: operationsMenu,
    settings: settingsMenu,
    admin: adminMenu,
  },
  allNavigationItems = [...menu, ...dashboardMenu, ...portalMenu, ...monitoringMenu, ...operationsMenu, ...settingsMenu, ...adminMenu];
const viewPermissionRequirements = {
  clients: "company.read",
  incidents: "ticket.read",
  portal: "ticket.read",
  "portal-requests": "ticket.read",
  contracts: "contract.read",
  consumption: "contract.read",
  billing: "billing.approve",
  profitability: "profitability.read",
  automations: "automation.execute",
  "admin-users": "grant.manage",
  "admin-teams": "grant.manage",
  "admin-agents": "grant.manage",
  "admin-maintenance": "grant.manage",
  "admin-release": "grant.manage",
  "admin-integrations": "audit.read",
  "admin-pending": "audit.read",
  admin: "audit.read",
};
const featureModeAllowsTraffic = (mode) => mode == null || mode === "write_pilot" || mode === "enabled";
const viewPaths = {
  overview: "/visao-geral",
  databases: "/monitoramento/bancos",
  clients: "/empresas",
  assets: "/monitoramento/ambientes",
  monitors: "/monitoramento/servicos",
  "monitors-legacy": "/monitoramento/servicos-legado",
  alerts: "/monitoramento/alertas",
  "asset-topology": "/monitoramento/topologia",
  "event-quarantine": "/monitoramento/quarentena",
  portal: "/portal",
  "portal-requests": "/portal/solicitacoes",
  catalog: "/operacao/catalogo",
  incidents: "/incidentes",
  suppliers: "/operacao/terceiros",
  queues: "/operacao/filas",
  "my-queue": "/operacao/minha-fila",
  workload: "/operacao/carga-agenda",
  "time-entries": "/operacao/apontamentos",
  contracts: "/operacao/contratos",
  consumption: "/operacao/consumo",
  billing: "/operacao/pre-faturamento",
  quality: "/operacao/qualidade",
  profitability: "/operacao/rentabilidade",
  "major-incidents": "/operacao/incidentes-maiores",
  knowledge: "/operacao/conhecimento",
  problems: "/operacao/problemas",
  changes: "/operacao/mudancas",
  runbooks: "/operacao/runbooks",
  sla: "/operacao/sla",
  automations: "/operacao/automacoes",
  reports: "/relatorios",
  "settings-notifications": "/configuracoes/notificacoes",
  "settings-email": "/configuracoes/email-smtp",
  "settings-whatsapp": "/configuracoes/whatsapp",
  "admin-users": "/administracao/usuarios",
  "admin-teams": "/administracao/equipes",
  "admin-agents": "/administracao/agentes",
  "admin-maintenance": "/administracao/janelas-manutencao",
  "admin-integrations": "/administracao/integracoes",
  "admin-pending": "/administracao/operacoes-pendentes",
  "admin-release": "/administracao/liberacao",
  admin: "/administracao/auditoria",
};
const routeFromLocation = () => {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/administracao/equipes/novo") return { view: "admin-teams", teamMode: "new" };
  const editTeam = path.match(/^\/administracao\/equipes\/([^/]+)\/editar$/);
  if (editTeam) return { view: "admin-teams", teamMode: "edit", teamId: decodeURIComponent(editTeam[1]) };
  if (path === "/monitoramento/servicos/novo") return { view: "monitors", monitorMode: "new" };
  const editMonitor = path.match(/^\/monitoramento\/servicos\/([^/]+)\/editar$/);
  if (editMonitor) return { view: "monitors", monitorMode: "edit", monitorId: decodeURIComponent(editMonitor[1]) };
  if (path === "/monitoramento/ambientes/novo") return { view: "assets", assetMode: "new" };
  const editAsset = path.match(/^\/monitoramento\/ambientes\/([^/]+)\/editar$/);
  if (editAsset) return { view: "assets", assetMode: "edit", assetId: decodeURIComponent(editAsset[1]) };
  const editUser = path.match(/^\/administracao\/usuarios\/([^/]+)\/editar$/);
  if (editUser) return { view: "admin-users", userMode: "edit", userId: decodeURIComponent(editUser[1]) };
  const confirmBilling = path.match(/^\/operacao\/pre-faturamento\/([^/]+)\/confirmar-erp$/);
  if (confirmBilling) return { view: "billing", billingMode: "erp-confirm", billingCycleId: decodeURIComponent(confirmBilling[1]) };
  const mapQuarantine = path.match(/^\/monitoramento\/quarentena\/([^/]+)\/mapear$/);
  if (mapQuarantine) return { view: "event-quarantine", quarantineMode: "map", quarantineId: decodeURIComponent(mapQuarantine[1]) };
  if (path === "/operacao/fornecedores/novo") return { view: "suppliers", supplierMode: "new" };
  const editSupplier = path.match(/^\/operacao\/fornecedores\/([^/]+)\/editar$/);
  if (editSupplier) return { view: "suppliers", supplierMode: "edit", supplierId: decodeURIComponent(editSupplier[1]) };
  if (path === "/operacao/sla/novo") return { view: "sla", slaMode: "new" };
  const editSla = path.match(/^\/operacao\/sla\/([^/]+)\/editar$/);
  if (editSla) return { view: "sla", slaMode: "edit", slaId: decodeURIComponent(editSla[1]) };
  if (path === "/operacao/filas/nova") return { view: "queues", queueMode: "new" };
  const editQueue = path.match(/^\/operacao\/filas\/([^/]+)\/editar$/);
  if (editQueue) return { view: "queues", queueMode: "edit", queueId: decodeURIComponent(editQueue[1]) };
  if (path === "/administracao/agentes/novo") return { view: "admin-agents", agentMode: "new" };
  const editAgent = path.match(/^\/administracao\/agentes\/([^/]+)\/editar$/);
  if (editAgent) return { view: "admin-agents", agentMode: "edit", agentId: decodeURIComponent(editAgent[1]) };
  if (path === "/configuracoes/notificacoes/nova") return { view: "settings-notifications", ruleMode: "new" };
  const editRule = path.match(/^\/configuracoes\/notificacoes\/([^/]+)\/editar$/);
  if (editRule) return { view: "settings-notifications", ruleMode: "edit", ruleId: decodeURIComponent(editRule[1]) };
  if (path === "/administracao/janelas-manutencao/nova") return { view: "admin-maintenance", maintenanceMode: "new" };
  const editMaintenance = path.match(/^\/administracao\/janelas-manutencao\/([^/]+)\/editar$/);
  if (editMaintenance) return { view: "admin-maintenance", maintenanceMode: "edit", maintenanceId: decodeURIComponent(editMaintenance[1]) };
  if (path === "/empresas/nova") return { view: "clients", tenantMode: "new" };
  const editTenant = path.match(/^\/empresas\/([^/]+)\/editar$/);
  if (editTenant) return { view: "clients", tenantMode: "edit", tenantEditId: decodeURIComponent(editTenant[1]) };
  const editAlert = path.match(/^\/monitoramento\/alertas\/([^/]+)\/editar$/);
  if (editAlert) return { view: "alerts", alertMode: "edit", alertId: decodeURIComponent(editAlert[1]) };
  if (path === "/operacao/catalogo/novo") return { view: "catalog", catalogMode: "new" };
  const catalogVersion = path.match(/^\/operacao\/catalogo\/([^/]+)\/nova-versao$/);
  if (catalogVersion) return { view: "catalog", catalogMode: "version", catalogId: decodeURIComponent(catalogVersion[1]) };
  if (path === "/administracao/integracoes/nova") return { view: "admin-integrations", integrationMode: "new" };
  if (path === "/operacao/chamados/novo") return { view: "incidents", operationalMode: "ticket" };
  const editTicket = path.match(/^\/operacao\/chamados\/([^/]+)\/editar$/);
  if (editTicket) return { view: "incidents", ticketMode: "edit", ticketId: decodeURIComponent(editTicket[1]) };
  const detailTicket = path.match(/^\/operacao\/chamados\/([^/]+)$/);
  if (detailTicket) return { view: "incidents", ticketMode: "detail", ticketId: decodeURIComponent(detailTicket[1]) };
  const portalTicket = path.match(/^\/portal\/solicitacoes\/([^/]+)$/);
  if (portalTicket) return { view: "portal-requests", portalTicketId: decodeURIComponent(portalTicket[1]) };
  if (path === "/monitoramento/topologia/novo-vinculo") return { view: "asset-topology", operationalMode: "asset-identity" };
  if (path === "/administracao/relacoes-servico/nova") return { view: "incidents", operationalMode: "service-engagement" };
  if (path === "/administracao/concessoes/nova") return { view: "incidents", operationalMode: "access-grant" };
  if (path === "/administracao/liberacao/nova") return { view: "admin-release", operationalMode: "feature-flag" };
  const createRoutes = {
    "/operacao/incidentes-maiores/novo": "major-incidents",
    "/operacao/conhecimento/novo": "knowledge",
    "/operacao/problemas/novo": "problems",
    "/operacao/mudancas/nova": "changes",
    "/operacao/runbooks/novo": "runbooks",
    "/operacao/apontamentos/novo": "time-entries",
    "/operacao/contratos/novo": "contracts",
    "/operacao/consumo/nova-revisao": "consumption",
    "/operacao/pre-faturamento/nova-competencia": "billing",
  };
  if (createRoutes[path]) return { view: createRoutes[path], operationalMode: createRoutes[path] };
  const matched = Object.entries(viewPaths).find(([, route]) => route === path);
  return { view: matched?.[0] || "overview" };
};
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
function NavIcon({ name }) {
  const paths = {
    home: "M3 11.5 12 4l9 7.5M5 10v10h14V10M9 20v-6h6v6",
    building: "M4 21V5l8-3 8 3v16M8 8h2m4 0h2M8 12h2m4 0h2M8 16h2m4 0h2",
    activity: "M3 12h4l2-6 4 12 2-6h6",
    alert: "M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3h.01",
    automation: "M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6 7 7m10 10 1.4 1.4m0-12.8L17 7M7 17l-1.4 1.4M9 12a3 3 0 1 0 6 0 3 3 0 0 0-6 0Z",
    report: "M5 3h14v18H5V3Zm4 13v-4m3 4V8m3 8v-6",
    settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0-5v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4m0-12.8L17 7M7 17l-1.4 1.4",
    admin: "M12 3 4 6v5c0 5 3.4 8.2 8 10 4.6-1.8 8-5 8-10V6l-8-3Zm-3 9 2 2 4-4",
    server: "M4 5h16v6H4V5Zm0 8h16v6H4v-6Zm3-5h.01M7 16h.01",
    database: "M4 6c0-2 3.6-3 8-3s8 1 8 3-3.6 3-8 3-8-1-8-3Zm0 0v6c0 2 3.6 3 8 3s8-1 8-3V6m-16 6v6c0 2 3.6 3 8 3s8-1 8-3v-6",
    bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Zm-8 12h4",
    users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m7-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm13 10v-2a4 4 0 0 0-3-3.9m-2-11.9a4 4 0 0 1 0 7.7",
    team: "M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm8 2a3 3 0 1 0 0-6m-8 8c-4 0-6 2-6 5h12c0-3-2-5-6-5Zm8 0c3 0 5 2 5 5h-5",
    clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 5v5l3 2",
    mail: "M3 5h18v14H3V5Zm0 1 9 7 9-7",
    message: "M4 4h16v12H8l-4 4V4Z",
    calendar: "M4 5h16v16H4V5Zm4-3v6m8-6v6M4 10h16",
    audit: "M5 3h14v18H5V3Zm4 5h6m-6 4h6m-6 4h4",
    catalog: "M4 4h16v16H4V4Zm4 4h8M8 12h8M8 16h5",
    inbox: "M3 4h18v16H3V4Zm0 11h5l2 3h4l2-3h5",
    contract: "M6 3h12v18H6V3Zm3 5h6m-6 4h6m-6 4h4",
    billing: "M4 6h16v12H4V6Zm0 4h16m-12 4h3",
    quality: "m12 3 2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4-3.9-3.8 5.4-.8L12 3Z",
    profit: "M4 18 9 13l3 3 8-10M15 6h5v5",
    menu: "M4 6h16M4 12h16M4 18h16",
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name] || paths.report} /></svg>;
}
function ActionIcon({ name }) {
  const paths = {
    edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4L16.5 3.5Z",
    view: "M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
    pause: "M8 5v14M16 5v14",
    play: "m8 5 11 7-11 7V5Z",
    trash: "M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5",
    copy: "M8 8h11v11H8V8Zm-3 8H4V4h12v1",
    message: "M4 4h16v12H8l-4 4V4Z",
    key: "M15 7a5 5 0 1 1-2 4l8-8m-3 3 3 3m-6 0 3 3",
    add: "M12 5v14M5 12h14",
    refresh: "M20 6v5h-5M4 18v-5h5m9.5-5A7 7 0 0 0 6 6.5L4 11m16 2-2 4.5A7 7 0 0 1 5.5 16",
    filter: "M3 5h18l-7 8v6l-4 2v-8L3 5Z",
    download: "M12 3v12m-5-5 5 5 5-5M5 21h14",
    columns: "M4 5h16v14H4V5Zm5 0v14m6-14v14",
    more: "M5 12h.01M12 12h.01M19 12h.01",
    "chevron-down": "m6 9 6 6 6-6",
    "chevron-up": "m18 15-6-6-6 6",
  };
  return <svg className="action-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.more} /></svg>;
}
const actionText = (children) =>
  React.Children.toArray(children)
    .map((child) => (typeof child === "string" || typeof child === "number" ? String(child) : ""))
    .join(" ")
    .replace("✎", "")
    .trim();
const actionIconName = (label) => {
  const value = label.toLocaleLowerCase("pt-BR");
  if (value.includes("editar")) return "edit";
  if (value.includes("visualizar") || value.includes("detalhe")) return "view";
  if (value.includes("excluir") || value.includes("remover")) return "trash";
  if (value.includes("inativar") || value.includes("ignorar") || value.includes("desativar")) return "pause";
  if (value.includes("ativar") || value.includes("reativar") || value.includes("atender") || value.includes("executar")) return "play";
  if (value.includes("publicar")) return "play";
  if (value.includes("revisar")) return "edit";
  if (value.includes("criar versão")) return "add";
  if (value.includes("copiar")) return "copy";
  if (value.includes("contato") || value.includes("whatsapp")) return "message";
  if (value.includes("acesso") || value.includes("perfil") || value.includes("senha")) return "key";
  if (value.includes("adicionar") || value.includes("novo") || value.includes("nova")) return "add";
  return "more";
};
function decorateGridActions(children, row) {
  return React.Children.map(children, (child) => {
    if (!React.isValidElement(child)) return child;
    if (child.type === React.Fragment) return React.cloneElement(child, {}, decorateGridActions(child.props.children, row));
    if (child.type !== "button") return child;
    const label = actionText(child.props.children) || child.props.title || "Ação";
    const icon = actionIconName(label);
    const record = row?.name || row?.display_name || row?.title || row?.email;
    return React.cloneElement(child, {
      className: ["grid-action", `grid-action-${icon}`, child.props.className].filter(Boolean).join(" "),
      title: child.props.title || label,
      "aria-label": child.props["aria-label"] || `${label}${record ? ` ${record}` : ""}`,
    }, <><ActionIcon name={icon} /><span>{label}</span></>);
  });
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
function readSessionValue(key, fallback) {
  try {
    const saved = sessionStorage.getItem(key);
    return saved == null ? fallback : JSON.parse(saved);
  } catch {
    return fallback;
  }
}
function useSessionState(key, initialValue) {
  const [value, setValue] = useState(() => readSessionValue(key, initialValue));
  const activeKey = useRef(key);
  useEffect(() => {
    if (activeKey.current !== key) {
      activeKey.current = key;
      setValue(readSessionValue(key, initialValue));
      return;
    }
    sessionStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);
  return [value, setValue];
}
function StateView({ kind = "empty", title, description, actionLabel, onAction }) {
  return <section className={`state-view state-view--${kind}`} role={kind === "unavailable" || kind === "conflict" ? "alert" : "status"}>
    <span className="state-view-icon" aria-hidden="true">{kind === "loading" ? "…" : kind === "forbidden" ? "!" : kind === "conflict" ? "↔" : kind === "unavailable" ? "×" : "○"}</span>
    <div><strong>{title}</strong>{description && <p>{description}</p>}</div>
    {actionLabel && onAction && <button type="button" className="secondary" onClick={onAction}>{actionLabel}</button>}
  </section>;
}
function Loading({ title = "Carregando...", description }) { return <StateView kind="loading" title={title} description={description} />; }
function NoResults({ title = "Nenhum resultado encontrado.", description }) { return <StateView kind="no-results" title={title} description={description} />; }
function Unavailable({ title = "Conteúdo indisponível.", description, onRetry }) { return <StateView kind="unavailable" title={title} description={description} actionLabel={onRetry ? "Tentar novamente" : null} onAction={onRetry} />; }
function Conflict({ title = "Há um conflito que precisa ser resolvido.", description, onReview }) { return <StateView kind="conflict" title={title} description={description} actionLabel={onReview ? "Revisar" : null} onAction={onReview} />; }
function Forbidden({ title = "Acesso não autorizado.", description = "Seu perfil não possui permissão para esta operação." }) { return <StateView kind="forbidden" title={title} description={description} />; }
function SessionExpired({ onLogin }) { return <StateView kind="session-expired" title="Sessão expirada." description="Entre novamente para continuar com segurança." actionLabel="Entrar novamente" onAction={onLogin} />; }
function AppShell({ children }) { return <div className="app-shell">{children}</div>; }
function PageHeader({ title, description, eyebrow, actions }) {
  return <div className="shared-page-header"><div>{eyebrow && <small>{eyebrow}</small>}<h2>{title}</h2>{description && <p>{description}</p>}</div>{actions && <div className="shared-page-actions">{actions}</div>}</div>;
}
function MetricGroup({ items = [] }) {
  return <section className="metric-strip" aria-label="Indicadores">{items.map((item) => <article key={item.label}><strong>{item.value}</strong><span>{item.label}</span></article>)}</section>;
}
function FilterBar({ title = "Filtros", actions, children }) {
  return <section className="shared-filter-bar" aria-label={title}><div className="shared-filter-heading"><strong>{title}</strong>{actions}</div><div className="shared-filter-fields">{children}</div></section>;
}
function ListPage({ title, description, eyebrow, actions, metrics, filters, children }) {
  return <section className="panel list-page"><PageHeader title={title} description={description} eyebrow={eyebrow} actions={actions} />{metrics}{filters}{children}</section>;
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
  showEmptyGrid = false,
  selectedIds = null,
  onSelectionChange,
  stateKey = "",
}) {
  const gridRef = useRef(null);
  const sortStorageKey = stateKey ? `nexora:grid:${stateKey}:sort` : "";
  const [sort, setSort] = useState(() => stateKey ? readSessionValue(sortStorageKey, { key: columns[0]?.key, direction: "asc" }) : { key: columns[0]?.key, direction: "asc" }),
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
  useEffect(() => {
    if (sortStorageKey) sessionStorage.setItem(sortStorageKey, JSON.stringify(sort));
  }, [sortStorageKey, sort]);
  useEffect(() => {
    if (!stateKey) return undefined;
    const gridElement = gridRef.current;
    const scrollKey = `nexora:grid:${stateKey}:scroll`;
    const rememberPosition = () => sessionStorage.setItem(scrollKey, JSON.stringify({ window: Math.max(0, Math.round(window.scrollY)), grid: Math.max(0, Math.round(gridElement?.scrollTop || 0)) }));
    window.addEventListener("scroll", rememberPosition, { passive: true });
    gridElement?.addEventListener("scroll", rememberPosition, { passive: true });
    return () => {
      window.removeEventListener("scroll", rememberPosition);
      gridElement?.removeEventListener("scroll", rememberPosition);
    };
  }, [stateKey]);
  useEffect(() => {
    if (!stateKey || !rows.length) return undefined;
    const gridElement = gridRef.current;
    const savedPosition = readSessionValue(`nexora:grid:${stateKey}:scroll`, { window: 0, grid: 0 });
    let innerFrame;
    const outerFrame = requestAnimationFrame(() => {
      innerFrame = requestAnimationFrame(() => {
        window.scrollTo({ top: Number(savedPosition.window || 0), behavior: "auto" });
        if (gridElement) gridElement.scrollTop = Number(savedPosition.grid || 0);
      });
    });
    return () => {
      cancelAnimationFrame(outerFrame);
      if (innerFrame) cancelAnimationFrame(innerFrame);
    };
  }, [stateKey, rows.length]);
  if (!rows.length && !showEmptyGrid) return <NoResults title={empty} />;
  return (
    <div
      ref={gridRef}
      className="grid-wrap"
      role="region"
      aria-label="Tabela de dados"
      tabIndex="0"
      onPointerDownCapture={() => {
        if (!stateKey) return;
        sessionStorage.setItem(`nexora:grid:${stateKey}:scroll`, JSON.stringify({ window: Math.max(0, Math.round(window.scrollY)), grid: Math.max(0, Math.round(gridRef.current?.scrollTop || 0)) }));
      }}
    >
      <table className="data-grid">
        <thead>
          <tr>
            {selectedIds && <th className="selection-cell"><input type="checkbox" aria-label="Selecionar todos os registros exibidos" checked={ordered.length > 0 && ordered.every((row) => selectedIds.includes(row.id))} onChange={(e) => onSelectionChange?.(e.target.checked ? [...new Set([...selectedIds, ...ordered.map((row) => row.id)])] : selectedIds.filter((id) => !ordered.some((row) => row.id === id)))} /></th>}
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
          {!ordered.length && (
            <tr>
              <td colSpan={columns.length + (actions ? 1 : 0) + (selectedIds ? 1 : 0)}>
                <Empty>{empty}</Empty>
              </td>
            </tr>
          )}
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
              {selectedIds && <td className="selection-cell" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Selecionar ${r.name || "registro"}`} checked={selectedIds.includes(r.id)} onChange={(e) => onSelectionChange?.(e.target.checked ? [...selectedIds, r.id] : selectedIds.filter((id) => id !== r.id))} /></td>}
              {columns.map((c, columnIndex) => (
                <td key={c.key}>
                  {columnIndex === 0 && ["name", "display_name", "title"].includes(c.key) ? (
                    <span className="grid-record">
                      <span className="grid-record-icon"><NavIcon name={c.icon || "report"} /></span>
                      <span>{c.render ? c.render(r) : (databaseLabel(r[c.key]) ?? "—")}</span>
                    </span>
                  ) : (c.render ? c.render(r) : (databaseLabel(r[c.key]) ?? "—"))}
                </td>
              ))}
              {actions && (
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="row-actions">{decorateGridActions(actions(r), r)}</div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function FormPage({ title, description, onBack, children }) {
  return (
    <section className="form-page" aria-labelledby="form-page-title">
      <div className="form-page-heading">
        <button type="button" className="back-button" onClick={onBack}>← Voltar</button>
        <div><h2 id="form-page-title">{title}</h2><p>{description}</p></div>
      </div>
      {children}
    </section>
  );
}
function FormSection({ title, description, children }) {
  return <fieldset className="form-section"><legend>{title}</legend>{description && <p>{description}</p>}<div className="form-section-grid">{children}</div></fieldset>;
}
function FormActions({ onCancel, submitting = false, submitLabel = "Salvar" }) {
  return <div className="form-actions"><button type="button" className="secondary" onClick={onCancel} disabled={submitting}>Cancelar</button><button className="primary" type="submit" disabled={submitting}>{submitting ? "Salvando..." : submitLabel}</button></div>;
}
const Input = ({ label, ...props }) => (
  <label className="field">
    <span>{label}</span>
    <input {...props} />
  </label>
);
function CollectionField({ label, items, onChange, placeholder, options }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (value && !items.includes(value)) onChange([...items, value]);
    setDraft("");
  };
  const itemLabel = (value) => options?.find((o) => o.value === value)?.label || value;
  return (
    <div className="collection-field">
      <span>{label}</span>
      <div className="collection-entry">
        {options ? (
          <select value={draft} onChange={(e) => setDraft(e.target.value)}>
            <option value="">Selecione...</option>
            {options.filter((o) => !items.includes(o.value)).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ) : (
          <input value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
        )}
        <button type="button" className="secondary" onClick={add}>Adicionar</button>
      </div>
      <div className="collection-list">
        {items.length ? items.map((item) => (
          <span className="collection-chip" key={item}>{itemLabel(item)}<button type="button" aria-label={`Remover ${itemLabel(item)}`} onClick={() => onChange(items.filter((x) => x !== item))}>×</button></span>
        )) : <small>Nenhum item adicionado.</small>}
      </div>
    </div>
  );
}
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
  channel,
  settings,
  tenantId,
  token,
  submit,
  refresh,
  run,
}) {
  const [whatsappConnection, setWhatsappConnection] = useState(null);
  const [loadingQr, setLoadingQr] = useState(false);
  const [testTarget, setTestTarget] = useState("");
  const formPayload = (form) => {
    const f = new FormData(form);
    const has = (name) => f.has(name);
    return {
      smtp_enabled: channel === "email" ? f.get("smtp_enabled") === "on" : settings.smtp_enabled,
      smtp_host: has("smtp_host") ? f.get("smtp_host") || null : settings.smtp_host,
      smtp_port: has("smtp_port") ? Number(f.get("smtp_port") || 587) : settings.smtp_port,
      smtp_username: has("smtp_username") ? f.get("smtp_username") || null : settings.smtp_username,
      smtp_password: has("smtp_password") ? f.get("smtp_password") || null : null,
      smtp_from_address: has("smtp_from_address") ? f.get("smtp_from_address") || null : settings.smtp_from_address,
      smtp_tls: channel === "email" ? f.get("smtp_tls") === "on" : settings.smtp_tls,
      evolution_enabled: channel === "whatsapp" ? f.get("evolution_enabled") === "on" : settings.evolution_enabled,
      evolution_api_url: has("evolution_api_url") ? f.get("evolution_api_url") || null : settings.evolution_api_url,
      evolution_api_key: has("evolution_api_key") ? f.get("evolution_api_key") || null : null,
      evolution_instance: has("evolution_instance") ? f.get("evolution_instance") || null : settings.evolution_instance,
    };
  };
  const testChannel = (button, channel) => {
    const target = testTarget.trim();
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
  const loadWhatsappConnection = async (requestQr = false) => {
    setLoadingQr(true);
    try {
      const result = await api(
        `/api/v1/notification-settings/whatsapp-connection${requestQr ? "?qr=true" : ""}`,
        token,
        {},
        tenantId,
      );
      setWhatsappConnection(result);
    } finally {
      setLoadingQr(false);
    }
  };
  return (
    <section className="panel notification-channels">
      <div className="section-head">
        <div>
          <h2>{channel === "email" ? "E-mail SMTP" : "WhatsApp"}</h2>
          <p>
            Parametrização única da plataforma, compartilhada por todas as empresas. {channel === "email" ? "Configure o servidor responsável pelos envios de e-mail." : "Configure a integração de mensagens pela Evolution API."} Senhas e chaves permanecem protegidas.
          </p>
        </div>
      </div>
      <form
        key={`${channel}-${settings.updated_at || "default"}`}
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
        {channel === "email" && <fieldset>
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
          <Input label="Destino do teste" type="email" value={testTarget} onChange={(event)=>setTestTarget(event.target.value)} placeholder="destino@empresa.com" />
          <button
            type="button"
            className="secondary"
            onClick={(e) => testChannel(e.currentTarget, "email")}
          >
            Enviar teste
          </button>
        </fieldset>}
        {channel === "whatsapp" && <fieldset>
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
          <Input label="Destino do teste" type="tel" value={testTarget} onChange={(event)=>setTestTarget(event.target.value)} placeholder="5511999999999" />
          <div className="whatsapp-actions">
            <button type="button" className="secondary" disabled={loadingQr} onClick={() => run(() => loadWhatsappConnection(true), "QR Code atualizado.")}>
              {loadingQr ? "Consultando..." : "Exibir ou atualizar QR Code"}
            </button>
            <button type="button" className="secondary" disabled={loadingQr} onClick={() => run(() => loadWhatsappConnection(false), "Estado da conexão atualizado.")}>
              Verificar conexão
            </button>
            <button
              type="button"
              className="secondary"
              onClick={(e) => testChannel(e.currentTarget, "whatsapp")}
            >
              Enviar teste
            </button>
          </div>
          {whatsappConnection && (
            <section className={`whatsapp-connection ${whatsappConnection.connected ? "connected" : "disconnected"}`} aria-live="polite">
              <div>
                <strong>{whatsappConnection.connected ? "WhatsApp conectado" : "Aguardando leitura do QR Code"}</strong>
                <p>{whatsappConnection.connected ? "A instância está pronta para enviar alertas." : "Abra o WhatsApp no celular, acesse Aparelhos conectados e leia o código abaixo."}</p>
              </div>
              {whatsappConnection.qr_code && <img src={whatsappConnection.qr_code} alt="QR Code para conectar o WhatsApp à Evolution API" />}
              {whatsappConnection.pairing_code && <p className="pairing-code">Código de pareamento: <b>{whatsappConnection.pairing_code}</b></p>}
            </section>
          )}
        </fieldset>}
        <div className="channel-actions">
          <button className="primary">Salvar {channel === "email" ? "SMTP" : "WhatsApp"}</button>
        </div>
      </form>
    </section>
  );
}

function PerformanceTable({ title, description, rows = [], columns, scriptLabel, buildScript, empty = "Nenhum dado encontrado." }) {
  const [selected, setSelected] = useState([]), [sort, setSort] = useState({ key: columns[0]?.key, direction: "desc" });
  const visible = useMemo(() => [...rows].sort((a, b) => {
    const left = a[sort.key], right = b[sort.key], direction = sort.direction === "asc" ? 1 : -1;
    if (left == null) return 1; if (right == null) return -1;
    return (typeof left === "number" ? left - Number(right) : String(left).localeCompare(String(right), "pt-BR", { numeric: true })) * direction;
  }), [rows, sort]);
  useEffect(() => setSelected([]), [rows]);
  const selectedRows = selected.map((index) => rows[index]).filter(Boolean);
  const generate = () => {
    const script = buildScript(selectedRows), blob = new Blob([script], { type: "text/sql;charset=utf-8" }), link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = `${scriptLabel}_${Date.now()}.sql`; link.click(); URL.revokeObjectURL(link.href);
  };
  return <section className="perf-section">
    <div className="perf-section-head"><div><h4>{title}</h4><p>{description}</p></div><div><button className="secondary" onClick={() => setSelected(visible.map((row) => rows.indexOf(row)))}>Selecionar visíveis</button><button className="secondary" onClick={() => setSelected([])}>Limpar seleção</button><button className="primary" disabled={!selected.length} onClick={generate}>Gerar script{selected.length ? ` (${selected.length})` : ""}</button></div></div>
    <div className="perf-table-wrap"><table><thead><tr><th><input type="checkbox" checked={visible.length > 0 && visible.every((row) => selected.includes(rows.indexOf(row)))} onChange={(e) => setSelected(e.target.checked ? visible.map((row) => rows.indexOf(row)) : [])} /></th>{columns.map((column) => <th key={column.key} onClick={() => setSort((current) => ({ key: column.key, direction: current.key === column.key && current.direction === "desc" ? "asc" : "desc" }))}>{column.label}{sort.key === column.key ? (sort.direction === "asc" ? " ▲" : " ▼") : " ↕"}</th>)}</tr></thead><tbody>{visible.map((row) => { const index = rows.indexOf(row); return <tr key={index} className={selected.includes(index) ? "selected" : ""}><td><input type="checkbox" checked={selected.includes(index)} onChange={() => setSelected((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index])} /></td>{columns.map((column) => <td key={column.key} title={column.title ? column.title(row) : undefined}>{column.render ? column.render(row) : String(row[column.key] ?? "—")}</td>)}</tr>; })}</tbody></table>{!visible.length && <Empty>{empty}</Empty>}</div>
  </section>;
}

function DatabasePerformance() {
  const [data, setData] = useState(null), [loading, setLoading] = useState(false), [error, setError] = useState(""), [queryKind, setQueryKind] = useState("cpu"), [fragmentation, setFragmentation] = useState(null), [measuring, setMeasuring] = useState(false), [topTables, setTopTables] = useState(10);
  const load = async () => { setLoading(true); setError(""); try { const response = await fetch("/db-monitor/api/perf"), body = await response.json(); if (!response.ok) throw Error(body.detail || "Falha ao consultar o SQL Server."); setData(body); } catch (exception) { setError(exception.message); } finally { setLoading(false); } };
  useEffect(() => { load(); }, []);
  const header = () => `-- Gerado pelo Nexora em ${new Date().toLocaleString("pt-BR")}\n-- Servidor: ${data?.info?.servidor || "?"} | Banco: ${data?.database || "?"}\n-- O Nexora NÃO executou este script. Revise e aprove antes de executar no SSMS.\n\nUSE [${data?.database || ""}];\nGO\n\n`;
  const copy = (text) => navigator.clipboard.writeText(text || "");
  const measure = async () => { setMeasuring(true); setError(""); try { const response = await fetch(`/db-monitor/api/perf/fragmentation?top=${topTables}`, { method: "POST" }), body = await response.json(); if (!response.ok) throw Error(body.detail || "Falha na medição."); setFragmentation(body); } catch (exception) { setError(exception.message); } finally { setMeasuring(false); } };
  if (loading && !data) return <Loading title="Carregando análise de desempenho do SQL Server..." />;
  return <div className="database-performance-full"><div className="perf-overview"><div><h4>Análise e scripts corretivos</h4><p>Dados em tempo real do monitor de banco. Os scripts são gerados para revisão; nada é executado automaticamente.</p></div><button className="primary" onClick={load} disabled={loading}>{loading ? "Atualizando..." : "Atualizar análise"}</button></div>{error && <Unavailable title="Não foi possível carregar a análise." description={error} onRetry={load} />}{data && <>
    <div className="perf-context"><b>{data.database}</b><span>Servidor {data.info?.servidor || "—"}</span><span>Estado {data.info?.estado || "—"}</span><span>SQL ativo há {data.info?.horas_ativo ?? "—"} h</span><span>Análise em {data.duration_s ?? "—"} s</span></div>
    <PerformanceTable title="Índices sugeridos" description="Sugestões registradas pelo SQL Server com benefício e impacto estimados." rows={data.missing_indexes || []} scriptLabel="criar_indices" empty="Nenhuma sugestão de índice disponível." columns={[{ key: "tabela", label: "Tabela" }, { key: "beneficio", label: "Benefício" }, { key: "impacto_pct", label: "Impacto %" }, { key: "user_seeks", label: "Seeks" }, { key: "user_scans", label: "Scans" }, { key: "create_index", label: "Comando", title: (row) => row.create_index, render: (row) => <button className="table-copy" onClick={() => copy(row.create_index)}>Copiar SQL</button> }]} buildScript={(rows) => header() + "-- ATENÇÃO: valide espaço, impacto e janela de manutenção.\n\n" + rows.map((row) => `-- ${row.tabela} | benefício ${row.beneficio} | impacto ${row.impacto_pct}%\n${row.create_index}\nGO`).join("\n\n")} />
    <section className="perf-query-tabs"><div><h4>Queries de maior impacto</h4><p>Selecione a perspectiva, marque as consultas e gere um roteiro para análise no SSMS.</p></div><nav>{[["cpu","CPU"],["reads","Leituras"],["slow","Mais lentas"]].map(([key,label]) => <button className={queryKind === key ? "active" : ""} onClick={() => setQueryKind(key)} key={key}>{label}</button>)}</nav></section>
    <PerformanceTable title={`Ranking por ${queryKind === "cpu" ? "CPU" : queryKind === "reads" ? "leituras" : "tempo médio"}`} description="Inclui STATISTICS IO/TIME e observações para análise." rows={data.top_queries?.[queryKind] || []} scriptLabel="queries_analise" columns={[{ key: "execution_count", label: "Execuções" }, { key: "total_cpu_ms", label: "CPU total (ms)" }, { key: "media_ms", label: "Tempo médio (ms)" }, { key: "leituras_media", label: "Leituras médias" }, { key: "tabelas", label: "Tabelas", render: (row) => (row.tabelas || []).join(", ") || "—" }, { key: "query", label: "Query", title: (row) => row.query, render: (row) => <span className="query-preview">{String(row.query || "").replace(/\s+/g, " ").slice(0, 150)} <button className="table-copy" onClick={() => copy(row.query)}>Copiar</button></span> }]} buildScript={(rows) => header() + "SET STATISTICS IO ON;\nSET STATISTICS TIME ON;\nGO\n-- Ative o plano de execução real (Ctrl+M).\n\n" + rows.map((row, index) => `-- #${index + 1} | execuções ${row.execution_count} | CPU média ${row.media_cpu_ms} ms | tempo médio ${row.media_ms} ms\n-- Tabelas: ${(row.tabelas || []).join(", ") || "não identificadas"}\n${String(row.query || "").trim()}\nGO`).join("\n\n")} />
    <PerformanceTable title="Índices sem uso" description="Candidatos para análise. Observe um ciclo operacional completo antes de remover." rows={data.unused_indexes || []} scriptLabel="remover_indices" empty="Nenhum índice sem uso encontrado." columns={[{ key: "tabela", label: "Tabela", render: (row) => `${row.esquema}.${row.tabela}` }, { key: "indice", label: "Índice" }, { key: "tamanho_mb", label: "Tamanho (MB)" }, { key: "gravacoes", label: "Gravações" }, { key: "drop_index", label: "Comando", render: (row) => <button className="table-copy" onClick={() => copy(row.drop_index)}>Copiar SQL</button> }]} buildScript={(rows) => header() + "-- ATENÇÃO: preserve o script de recriação e valide rotinas mensais.\n\n" + rows.map((row) => `-- ${row.esquema}.${row.tabela} | ${row.indice} | ${row.tamanho_mb} MB\n${row.drop_index}\nGO`).join("\n\n")} />
    <section className="perf-fragmentation-control"><div><h4>Fragmentação de índices</h4><p>Medição sob demanda em modo LIMITED.</p></div><label>Maiores tabelas <select value={topTables} onChange={(event) => setTopTables(Number(event.target.value))}><option>5</option><option>10</option><option>20</option><option>30</option></select></label><button className="primary" onClick={measure} disabled={measuring}>{measuring ? "Medindo..." : "Medir fragmentação"}</button></section>
    {fragmentation && <PerformanceTable title="Manutenção de índices" description={`${fragmentation.tabelas_medidas} tabela(s) medidas em ${fragmentation.duration_s}s. REORGANIZE é online; REBUILD pode bloquear e gerar log.`} rows={fragmentation.indices || []} scriptLabel="manutencao_indices" columns={[{ key: "tabela", label: "Tabela", render: (row) => `${row.esquema}.${row.tabela}` }, { key: "indice", label: "Índice" }, { key: "tamanho_mb", label: "Tamanho (MB)" }, { key: "fragmentacao_pct", label: "Fragmentação %" }, { key: "acao", label: "Ação", render: (row) => <Status value={row.acao === "REBUILD" ? "critical" : row.acao === "REORGANIZE" ? "attention" : "healthy"} /> }, { key: "comando", label: "Comando", render: (row) => row.comando ? <button className="table-copy" onClick={() => copy(row.comando)}>Copiar SQL</button> : "—" }]} buildScript={(rows) => header() + "-- Execute uma tabela por vez e acompanhe bloqueios e crescimento do log.\n\n" + rows.filter((row) => row.comando).map((row) => `-- ${row.esquema}.${row.tabela} | ${row.fragmentacao_pct}% | ${row.acao}\n${row.comando}\nGO`).join("\n\n")} />}
  </>}</div>;
}

const nocStatusLabels = { healthy: "Saudável", attention: "Atenção", critical: "Crítico", unknown: "Desconhecido", maintenance: "Em manutenção" },
  nocServiceLabels = { ok: "OK", stopped: "Parado", alert: "Alerta", unknown: "Sem dados", inactive: "Inativo" },
  nocCriticalityLabels = { critical: "Crítica", high: "Alta", medium: "Média", low: "Baixa" };
function NocDashboard({ data, loading, onRetry }) {
  const [search, setSearch] = useState(""), [company, setCompany] = useState(""), [status, setStatus] = useState(""), [criticality, setCriticality] = useState(""), [onlyProblems, setOnlyProblems] = useState(false), [mode, setMode] = useState("cards");
  const [expanded, setExpanded] = useState(() => new Set());
  const toggleExpanded = (assetId) => setExpanded((prev) => {
    const next = new Set(prev);
    next.has(assetId) ? next.delete(assetId) : next.add(assetId);
    return next;
  });
  if (loading && !data) return <Loading title="Carregando o painel NOC..." />;
  if (!data) return <Unavailable title="Não foi possível carregar o painel NOC." onRetry={onRetry} />;
  const environments = data.environments || [], summary = data.summary || {};
  const companies = [...new Map(environments.map((item) => [item.company_id, item.company])).entries()];
  const term = search.trim().toLowerCase();
  const rows = environments.filter((item) => (!term || `${item.environment} ${item.company}`.toLowerCase().includes(term))
    && (!company || item.company_id === company) && (!status || item.status === status)
    && (!criticality || item.criticality === criticality) && (!onlyProblems || item.status !== "healthy"));
  const filters = [search && ["Busca", search, () => setSearch("")], company && ["Empresa", companies.find(([id]) => id === company)?.[1], () => setCompany("")], status && ["Status", nocStatusLabels[status], () => setStatus("")], criticality && ["Criticidade", nocCriticalityLabels[criticality], () => setCriticality("")], onlyProblems && ["Filtro", "Somente com problemas", () => setOnlyProblems(false)]].filter(Boolean);
  const clear = () => { setSearch(""); setCompany(""); setStatus(""); setCriticality(""); setOnlyProblems(false); };
  const fmtTime = (value) => value ? new Date(value).toLocaleString("pt-BR") : "—";
  return <section className="noc-page">
    <MetricGroup items={[
      { label: "Empresas monitoradas", value: summary.companies ?? "—" },
      { label: "Ambientes", value: summary.environments ?? "—" },
      { label: "Serviços monitorados", value: summary.services ?? "—" },
      { label: "Ambientes críticos", value: summary.critical ?? "—" },
      { label: "Em atenção", value: summary.attention ?? "—" },
      { label: "Desconhecidos", value: summary.unknown ?? "—" },
      { label: "Em manutenção", value: summary.maintenance ?? "—" },
    ]} />
    <FilterBar title="Filtros do NOC">
      <label>Busca<input type="search" placeholder="Buscar empresa ou ambiente" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
      <label>Empresa<select value={company} onChange={(e) => setCompany(e.target.value)}><option value="">Todas as empresas</option>{companies.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label>Status<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Todos os status</option>{Object.entries(nocStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Criticidade<select value={criticality} onChange={(e) => setCriticality(e.target.value)}><option value="">Todas</option>{Object.entries(nocCriticalityLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label className="noc-toggle"><input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />Somente com problemas</label>
      <div className="noc-mode" role="group" aria-label="Modo de visualização">{[["cards", "Cards"], ["table", "Painel NOC"]].map(([key, label]) => <button type="button" key={key} className={mode === key ? "active" : ""} aria-pressed={mode === key} onClick={() => setMode(key)}>{label}</button>)}</div>
    </FilterBar>
    {filters.length > 0 && <div className="noc-chips">{filters.map(([label, value, remove]) => <button type="button" key={label} onClick={remove} aria-label={`Remover filtro ${label}: ${value}`}>{label}: {value} ×</button>)}<button type="button" className="link" onClick={clear}>Limpar</button></div>}
    <p className="noc-count" aria-live="polite">{rows.length} de {environments.length} ambiente(s)</p>
    {!environments.length ? <Empty>Nenhum ambiente monitorado nas empresas autorizadas.</Empty> : !rows.length ? <NoResults description="Ajuste ou limpe os filtros." /> : mode === "cards"
      ? <div className="noc-grid">{rows.map((item) => {
          const open = expanded.has(item.asset_id);
          return <article className={`noc-card ${item.status} ${open ? "open" : ""}`} key={item.asset_id}>
            <header><div><strong>{item.environment}</strong><small>{item.company}</small></div><span className={`noc-badge ${item.status}`}>{nocStatusLabels[item.status]}</span></header>
            <button type="button" className="noc-card-toggle" aria-expanded={open} onClick={() => toggleExpanded(item.asset_id)}>
              <p className="noc-card-meta noc-counts" aria-label="Contagem de monitores"><span className="ok"><b>{item.counts.ok}</b> OK</span><span className="stopped"><b>{item.counts.stopped}</b> parados</span><span className="alert"><b>{item.counts.alert}</b> em alerta</span><span className="unknown"><b>{item.counts.unknown}</b> desconhecidos</span></p>
              <ActionIcon name={open ? "chevron-up" : "chevron-down"} />
            </button>
            {open && <ul className="noc-services">{item.services.map((service) => <li key={service.id} className={service.state} title={`${service.name}: ${nocServiceLabels[service.state]}`}><i aria-hidden="true" />{service.name}<span>{nocServiceLabels[service.state]}</span></li>)}</ul>}
            <footer><span>Atualizado: {fmtTime(item.last_checked_at)}</span>{item.open_alerts > 0 && <span>{item.open_alerts} alerta(s) aberto(s)</span>}</footer>
          </article>;
        })}</div>
      : <div className="noc-table-wrap"><table className="noc-table"><thead><tr><th>Status</th><th>Empresa</th><th>Ambiente</th><th>Criticidade</th><th>OK</th><th>Parados</th><th>Em alerta</th><th>Desconhecidos</th><th>Alertas abertos</th><th>Última coleta</th></tr></thead><tbody>{rows.map((item) => <tr key={item.asset_id}><td><span className={`noc-badge ${item.status}`}>{nocStatusLabels[item.status]}</span></td><td>{item.company}</td><td>{item.environment}</td><td>{nocCriticalityLabels[item.criticality]}</td><td>{item.counts.ok}</td><td>{item.counts.stopped}</td><td>{item.counts.alert}</td><td>{item.counts.unknown}</td><td>{item.open_alerts}</td><td>{fmtTime(item.last_checked_at)}</td></tr>)}</tbody></table></div>}
  </section>;
}
function App() {
  const [token, setToken] = useState(""),
    [currentUser, setCurrentUser] = useState(null),
    [authContext, setAuthContext] = useState(null),
    [mobileMenuOpen, setMobileMenuOpen] = useState(false),
    [ready, setReady] = useState(false),
    [refreshing, setRefreshing] = useState(false),
    [lastUpdatedAt, setLastUpdatedAt] = useState(null),
    [routePath, setRoutePath] = useState(() => location.pathname),
    [view, setViewState] = useState(() => routeFromLocation().view),
    [error, setError] = useState(""),
    [errorStatus, setErrorStatus] = useState(0),
    [notice, setNotice] = useState(null),
    [tenantId, setTenantId] = useState("");
  const [tenants, setTenants] = useState([]),
    [assets, setAssets] = useState([]),
    [monitors, setMonitors] = useState([]),
    [alerts, setAlerts] = useState([]),
    [incidents, setIncidents] = useState([]),
    [portalData, setPortalData] = useState({ services: [], recent_tickets: [] }),
    [portalTickets, setPortalTickets] = useState([]),
    [portalTicketDetail, setPortalTicketDetail] = useState(null),
    [catalogServices, setCatalogServices] = useState([]),
    [requestingService, setRequestingService] = useState(null),
    [editingCatalog, setEditingCatalog] = useState(null),
    [catalogCreating, setCatalogCreating] = useState(false),
    [catalogSubmitting, setCatalogSubmitting] = useState(false),
    [catalogFields, setCatalogFields] = useState([]),
    [myQueue, setMyQueue] = useState({ tickets: [], tasks: [] }),
    [inAppNotifications, setInAppNotifications] = useState({ items: [], unread: 0 }),
    [workload, setWorkload] = useState({ users: [] }),
    [timeEntries, setTimeEntries] = useState([]),
    [contracts, setContracts] = useState([]),
    [editingContract, setEditingContract] = useState(null),
    [serviceEngagements, setServiceEngagements] = useState([]),
    [accessGrants, setAccessGrants] = useState([]),
    [consumptionData, setConsumptionData] = useState({ items: [] }),
    [consumptionReviews, setConsumptionReviews] = useState({ items: [] }),
    [billingCycles, setBillingCycles] = useState([]),
    [confirmingBillingCycle, setConfirmingBillingCycle] = useState(null),
    [billingConfirmationRequested, setBillingConfirmationRequested] = useState(() => routeFromLocation().billingMode === "erp-confirm"),
    [billingSubmitting, setBillingSubmitting] = useState(false),
    [qualityData, setQualityData] = useState({ metrics: {}, csat: {} }),
    [profitabilityData, setProfitabilityData] = useState({ items: [] }),
    [topologyData, setTopologyData] = useState({ nodes: [], edges: [] }),
    [assetIdentities, setAssetIdentities] = useState([]),
    [quarantineEvents, setQuarantineEvents] = useState([]),
    [mappingQuarantineEvent, setMappingQuarantineEvent] = useState(null),
    [quarantineMappingRequested, setQuarantineMappingRequested] = useState(() => routeFromLocation().quarantineMode === "map"),
    [quarantineSubmitting, setQuarantineSubmitting] = useState(false),
    [majorIncidents, setMajorIncidents] = useState([]),
    [knowledgeArticles, setKnowledgeArticles] = useState([]),
    [knowledgeQuery, setKnowledgeQuery] = useState(""),
    [problems, setProblems] = useState([]),
    [changes, setChanges] = useState([]),
    [runbooks, setRunbooks] = useState([]),
    [featureFlags, setFeatureFlags] = useState([]),
    [integrationConnections, setIntegrationConnections] = useState([]),
    [integrationCreating, setIntegrationCreating] = useState(false),
    [integrationSubmitting, setIntegrationSubmitting] = useState(false),
    [operationalCreateView, setOperationalCreateView] = useState(() => routeFromLocation().operationalMode || null),
    [operationalSubmitting, setOperationalSubmitting] = useState(false),
    [integrationCommands, setIntegrationCommands] = useState({items:[],totals:[],metrics:{}}),
    [integrationCommandFilter, setIntegrationCommandFilter] = useState("all"),
    [dashboard, setDashboard] = useState({ totals: {}, companies: [] }),
    [noc, setNoc] = useState(null), [nocLoading, setNocLoading] = useState(false);
  const captureError = (exception) => {
    setError(exception?.message || "Não foi possível concluir a operação.");
    setErrorStatus(Number(exception?.status || 0));
  };
  const clearError = () => { setError(""); setErrorStatus(0); };
  const featureModes = authContext?.feature_modes_by_company?.[tenantId] || {};
  const featureEnabled = (feature) => featureModeAllowsTraffic(featureModes[feature]);
  const viewAvailable = (candidate) => {
    if (candidate === "overview") return true;
    if (!featureEnabled(`view:${candidate}`)) return false;
    const required = viewPermissionRequirements[candidate];
    if (!required || !authContext || currentUser?.is_master || authContext.user?.is_master) return true;
    return (authContext.permissions_by_company?.[tenantId] || []).includes(required);
  };
  const activeTenantRef=useRef(tenantId);
  activeTenantRef.current=tenantId;
  const [dashboardTenantIds, setDashboardTenantIds] = useState([]),
    [dashboardCompanyFilterOpen, setDashboardCompanyFilterOpen] = useState(false);
  const [suppliers, setSuppliers] = useState([]),
    [slas, setSlas] = useState([]),
    [holidays, setHolidays] = useState([]),
    [teams, setTeams] = useState([]),
    [departments, setDepartments] = useState([]),
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
    [agents, setAgents] = useState([]),
    [agentEnrollment, setAgentEnrollment] = useState(null),
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
    [databaseSources, setDatabaseSources] = useState([]),
    [selectedDatabaseId, setSelectedDatabaseId] = useState("all"),
    [databaseTab, setDatabaseTab] = useState("diagnostics"),
    [databaseSearch, setDatabaseSearch] = useState(""),
    [detail, setDetail] = useState(null),
    [completingIncident, setCompletingIncident] = useState(null),
    [selected, setSelected] = useState(null),
    [editingTeam, setEditingTeam] = useState(null),
    [teamSearch, setTeamSearch] = useSessionState(`nexora:list:teams:${tenantId}:search`, ""),
    [teamStatus, setTeamStatus] = useSessionState(`nexora:list:teams:${tenantId}:status`, "all"),
    [editingAccessUser, setEditingAccessUser] = useState(null),
    [editingUserMaster, setEditingUserMaster] = useState(false),
    [userSubmitting, setUserSubmitting] = useState(false),
    [editingSupplier, setEditingSupplier] = useState(null),
    [supplierSubmitting, setSupplierSubmitting] = useState(false),
    [editingSla, setEditingSla] = useState(null),
    [slaSubmitting, setSlaSubmitting] = useState(false),
    [queues, setQueues] = useState([]),
    [editingQueue, setEditingQueue] = useState(null),
    [queueSubmitting, setQueueSubmitting] = useState(false),
    [editingAgent, setEditingAgent] = useState(null),
    [agentSubmitting, setAgentSubmitting] = useState(false),
    [editingRule, setEditingRule] = useState(null),
    [ruleSubmitting, setRuleSubmitting] = useState(false),
    [editingMaintenance, setEditingMaintenance] = useState(null),
    [maintenanceSubmitting, setMaintenanceSubmitting] = useState(false),
    [editingTenant, setEditingTenant] = useState(null),
    [tenantSubmitting, setTenantSubmitting] = useState(false),
    [editingAlert, setEditingAlert] = useState(null),
    [alertSubmitting, setAlertSubmitting] = useState(false),
    [incidentTab, setIncidentTab] = useState("service"),
    [incidentFilters, setIncidentFilters] = useSessionState(`nexora:list:incidents:${tenantId}:filters`, { search: "", priority: "all", status: "all", sla: "all", glpi: "all", team: "all", assignee: "all", mine: false, actionable: true }),
    [incidentPage, setIncidentPage] = useSessionState(`nexora:list:incidents:${tenantId}:page`, 1),
    [incidentPageSize, setIncidentPageSize] = useSessionState(`nexora:list:incidents:${tenantId}:page-size`, 10),
    [showMonitorForm, setShowMonitorForm] = useState(false),
    [editingMonitor, setEditingMonitor] = useState(null),
    [monitorSubmitting, setMonitorSubmitting] = useState(false),
    [showAssetForm, setShowAssetForm] = useState(false),
    [editingAsset, setEditingAsset] = useState(null),
    [assetSubmitting, setAssetSubmitting] = useState(false),
    [monitorFiltersOpen, setMonitorFiltersOpen] = useState(true),
    [monitorFilters, setMonitorFilters] = useSessionState(`nexora:list:monitors:${tenantId}:filters`, { status: "all", search: "", type: "all", target: "", severity: "all", team: "all", user: "all", asset: "all" }),
    [selectedMonitorIds, setSelectedMonitorIds] = useState([]),
    [monitorPage, setMonitorPage] = useSessionState(`nexora:list:monitors:${tenantId}:page`, 1),
    [monitorPageSize, setMonitorPageSize] = useSessionState(`nexora:list:monitors:${tenantId}:page-size`, 10),
    [alertFiltersOpen, setAlertFiltersOpen] = useState(true),
    [alertFilters, setAlertFilters] = useSessionState(`nexora:list:alerts:${tenantId}:filters`, { search: "", severity: "all", status: "all", monitor: "all", openedFrom: "", openedTo: "" }),
    [alertPage, setAlertPage] = useSessionState(`nexora:list:alerts:${tenantId}:page`, 1),
    [alertPageSize, setAlertPageSize] = useSessionState(`nexora:list:alerts:${tenantId}:page-size`, 10),
    [teamContacts, setTeamContacts] = useState({ emails: [], numbers: [], groups: [], members: [] }),
    [companySearch, setCompanySearch] = useState(""),
    [pendingDelete, setPendingDelete] = useState(null),
    [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const setView = (nextView, options = {}) => {
    if (tenantId && authContext && !viewAvailable(nextView)) {
      captureError(new ApiError("Esta funcionalidade não está liberada para seu perfil ou para a Empresa selecionada.", { status: 403, code: "feature_or_capability_denied" }));
      return;
    }
    clearError();
    setViewState(nextView);
    if (nextView !== "admin-teams") setEditingTeam(null);
    const nextPath = options.path || viewPaths[nextView] || "/visao-geral";
    if (location.pathname !== nextPath) history[options.replace ? "replaceState" : "pushState"]({}, "", nextPath);
  };
  const closeTeamForm = () => {
    setEditingTeam(null);
    setTeamContacts({ emails: [], numbers: [], groups: [], members: [] });
    setView("admin-teams");
  };
  const openTeamForm = (team = null) => {
    const record = team || { id: null, name: "", active: true };
    setEditingTeam(record);
    setTeamContacts({
      emails: record.emails || [],
      numbers: record.whatsapp_numbers || [],
      groups: record.whatsapp_group_ids || (record.whatsapp_group_id ? [record.whatsapp_group_id] : []),
      members: (record.member_user_ids || []).map(String),
    });
    setViewState("admin-teams");
    const path = record.id ? `/administracao/equipes/${encodeURIComponent(record.id)}/editar` : "/administracao/equipes/novo";
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeMonitorForm = () => {
    setEditingMonitor(null);
    setShowMonitorForm(false);
    setView("monitors");
  };
  const openMonitorForm = (monitor = null) => {
    setEditingMonitor(monitor);
    setShowMonitorForm(true);
    setViewState("monitors");
    const path = monitor?.id
      ? `/monitoramento/servicos/${encodeURIComponent(monitor.id)}/editar`
      : "/monitoramento/servicos/novo";
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeAssetForm = () => {
    setEditingAsset(null);
    setShowAssetForm(false);
    setView("assets");
  };
  const openAssetForm = (asset = null) => {
    setEditingAsset(asset);
    setShowAssetForm(true);
    setViewState("assets");
    const path = asset?.id
      ? `/monitoramento/ambientes/${encodeURIComponent(asset.id)}/editar`
      : "/monitoramento/ambientes/novo";
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeUserForm = () => {
    setEditingAccessUser(null);
    setEditingUserMaster(false);
    setView("admin-users");
  };
  const openUserForm = (user) => {
    setEditingAccessUser(user);
    setEditingUserMaster(Boolean(user.is_master));
    setViewState("admin-users");
    const path = `/administracao/usuarios/${encodeURIComponent(user.id)}/editar`;
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeBillingConfirmation = () => {
    setConfirmingBillingCycle(null);
    setBillingConfirmationRequested(false);
    setView("billing");
  };
  const openBillingConfirmation = (cycle) => {
    setConfirmingBillingCycle(cycle);
    setBillingConfirmationRequested(true);
    setViewState("billing");
    const path = `/operacao/pre-faturamento/${encodeURIComponent(cycle.id)}/confirmar-erp`;
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeQuarantineMapping = () => {
    setMappingQuarantineEvent(null);
    setQuarantineMappingRequested(false);
    setView("event-quarantine");
  };
  const openQuarantineMapping = (event) => {
    setMappingQuarantineEvent(event);
    setQuarantineMappingRequested(true);
    setViewState("event-quarantine");
    const path = `/monitoramento/quarentena/${encodeURIComponent(event.id)}/mapear`;
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeSupplierForm = () => { setEditingSupplier(null); setView("suppliers"); };
  const openSupplierForm = (supplier = null) => {
    const record = supplier || { id: null, active: true, response_minutes: 240, resolution_minutes: 1440 };
    setEditingSupplier(record);
    setViewState("suppliers");
    const path = record.id ? `/operacao/fornecedores/${encodeURIComponent(record.id)}/editar` : "/operacao/fornecedores/novo";
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeSlaForm = () => { setEditingSla(null); setView("sla"); };
  const openSlaForm = (policy = null) => {
    const record = policy || { id: null, priority: "medium", coverage: "24x7", active: true, pause_on_waiting: false };
    setEditingSla(record);
    setViewState("sla");
    const path = record.id ? `/operacao/sla/${encodeURIComponent(record.id)}/editar` : "/operacao/sla/novo";
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeQueueForm = () => { setEditingQueue(null); setView("queues"); };
  const openQueueForm = (queue = null) => {
    const record = queue || { id: null, active: true, team_id: "" };
    setEditingQueue(record);
    setViewState("queues");
    const path = record.id ? `/operacao/filas/${encodeURIComponent(record.id)}/editar` : "/operacao/filas/nova";
    if (location.pathname !== path) history.pushState({}, "", path);
  };
  const closeAgentForm = () => { setEditingAgent(null); setAgentEnrollment(null); setView("admin-agents"); };
  const openAgentForm = (agent = null) => { setEditingAgent(agent || { id: null }); setAgentEnrollment(null); setViewState("admin-agents"); window.scrollTo({ top: 0, behavior: "instant" }); const path=agent?.id?`/administracao/agentes/${encodeURIComponent(agent.id)}/editar`:"/administracao/agentes/novo"; if(location.pathname!==path)history.pushState({},"",path); };
  const closeRuleForm = () => { setEditingRule(null); setView("settings-notifications"); };
  const openRuleForm = (rule = null) => { setEditingRule(rule || { id:null,channel:"email",minimum_priority:"low",events:["incident.created","incident.resolved","incident.in_progress"],active:true }); setViewState("settings-notifications"); const path=rule?.id?`/configuracoes/notificacoes/${encodeURIComponent(rule.id)}/editar`:"/configuracoes/notificacoes/nova"; if(location.pathname!==path)history.pushState({},"",path); };
  const closeMaintenanceForm = () => { setEditingMaintenance(null); setView("admin-maintenance"); };
  const openMaintenanceForm = (window = null) => { setEditingMaintenance(window || { id:null,active:true }); setViewState("admin-maintenance"); const path=window?.id?`/administracao/janelas-manutencao/${encodeURIComponent(window.id)}/editar`:"/administracao/janelas-manutencao/nova"; if(location.pathname!==path)history.pushState({},"",path); };
  const closeTenantForm = () => { setEditingTenant(null); setView("clients"); };
  const openTenantForm = (company = null) => { const record=company||{id:null,active:true,timezone:"America/Sao_Paulo"};setEditingTenant(record);setViewState("clients");const path=record.id?`/empresas/${encodeURIComponent(record.id)}/editar`:"/empresas/nova";if(location.pathname!==path)history.pushState({},"",path); };
  const closeAlertForm = () => { setEditingAlert(null); setView("alerts"); };
  const openAlertForm = (alert) => { setEditingAlert(alert); setViewState("alerts"); const path=`/monitoramento/alertas/${encodeURIComponent(alert.id)}/editar`; if(location.pathname!==path)history.pushState({},"",path); };
  const closeCatalogForm = () => { setCatalogCreating(false); setEditingCatalog(null); setCatalogFields([]); setView("catalog"); };
  const openCatalogCreate = () => { setCatalogCreating(true); setEditingCatalog(null); setViewState("catalog"); if(location.pathname!=="/operacao/catalogo/novo")history.pushState({},"","/operacao/catalogo/novo"); };
  const openCatalogVersion = (service) => { setCatalogCreating(false); setEditingCatalog(service);setCatalogFields((service.form_schema?.fields||[]).map((field)=>({...field,optionsText:(field.options||[]).map((option)=>typeof option==="object"?option.value:option).join(", ")})));setViewState("catalog");const path=`/operacao/catalogo/${encodeURIComponent(service.id)}/nova-versao`;if(location.pathname!==path)history.pushState({},"",path); };
  const closeIntegrationForm = () => { setIntegrationCreating(false); setView("admin-integrations"); };
  const openIntegrationForm = () => { setIntegrationCreating(true); setViewState("admin-integrations"); if(location.pathname!=="/administracao/integracoes/nova")history.pushState({},"","/administracao/integracoes/nova"); };
  const operationalCreatePaths = { "major-incidents":"/operacao/incidentes-maiores/novo", knowledge:"/operacao/conhecimento/novo", problems:"/operacao/problemas/novo", changes:"/operacao/mudancas/nova", runbooks:"/operacao/runbooks/novo", "time-entries":"/operacao/apontamentos/novo", contracts:"/operacao/contratos/novo", consumption:"/operacao/consumo/nova-revisao", billing:"/operacao/pre-faturamento/nova-competencia", ticket:"/operacao/chamados/novo", "asset-identity":"/monitoramento/topologia/novo-vinculo", "service-engagement":"/administracao/relacoes-servico/nova", "access-grant":"/administracao/concessoes/nova", "feature-flag":"/administracao/liberacao/nova" };
  const operationalParentViews = { ticket:"incidents", "asset-identity":"asset-topology", "service-engagement":"incidents", "access-grant":"incidents", "feature-flag":"admin-release" };
const openOperationalCreate = (targetView) => { const parent=operationalParentViews[targetView]||targetView;if(targetView==="contracts")setEditingContract(null);setOperationalCreateView(targetView);setViewState(parent);const path=operationalCreatePaths[targetView];if(location.pathname!==path)history.pushState({},"",path); };
const openContractForm = (contract) => { setEditingContract(contract);setOperationalCreateView("contracts");setViewState("contracts");const path=`/operacao/contratos/${encodeURIComponent(contract.id)}/editar`;if(location.pathname!==path)history.pushState({},"",path); };
const closeOperationalCreate = () => { const target=operationalParentViews[operationalCreateView]||operationalCreateView;if(operationalCreateView==="contracts")setEditingContract(null);setOperationalCreateView(null);setView(target||"overview"); };
  const identity = useMemo(() => (token ? parseToken(token) : null), [token]),
    tenant = tenants.find((t) => t.id === tenantId),
    run = async (fn, success = "Operação realizada com sucesso.") => {
      try {
        await fn();
        clearError();
        if (success) setNotice({ type: "success", text: success });
      } catch (e) {
        captureError(e);
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
        captureError(e);
        setReady(true);
      });
  }, []);
  useEffect(() => {
    if (!mobileMenuOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setMobileMenuOpen(false);
    };
    addEventListener("keydown", closeOnEscape);
    return () => removeEventListener("keydown", closeOnEscape);
  }, [mobileMenuOpen]);
  useEffect(() => {
    const handleRouteChange = () => {
      const route = routeFromLocation();
      setRoutePath(location.pathname);
      setViewState(route.view);
      if (route.view !== "admin-teams" || !route.teamMode) {
        setEditingTeam(null);
        setTeamContacts({ emails: [], numbers: [], groups: [], members: [] });
      }
      if (route.view !== "monitors" || !route.monitorMode) {
        setEditingMonitor(null);
        setShowMonitorForm(false);
      }
      if (route.view !== "assets" || !route.assetMode) {
        setEditingAsset(null);
        setShowAssetForm(false);
      }
      if (route.view !== "admin-users" || !route.userMode) {
        setEditingAccessUser(null);
        setEditingUserMaster(false);
      }
      if (route.view === "billing" && route.billingMode === "erp-confirm") {
        setBillingConfirmationRequested(true);
      } else {
        setConfirmingBillingCycle(null);
        setBillingConfirmationRequested(false);
      }
      if (route.view === "event-quarantine" && route.quarantineMode === "map") {
        setQuarantineMappingRequested(true);
      } else {
        setMappingQuarantineEvent(null);
        setQuarantineMappingRequested(false);
      }
      if (route.view !== "suppliers" || !route.supplierMode) setEditingSupplier(null);
      if (route.view !== "sla" || !route.slaMode) setEditingSla(null);
      if (route.view !== "queues" || !route.queueMode) setEditingQueue(null);
      if (route.view !== "admin-agents" || !route.agentMode) setEditingAgent(null);
      if (route.view !== "settings-notifications" || !route.ruleMode) setEditingRule(null);
      if (route.view !== "admin-maintenance" || !route.maintenanceMode) setEditingMaintenance(null);
      if (route.view !== "clients" || !route.tenantMode) setEditingTenant(null);
      if (route.view !== "alerts" || !route.alertMode) setEditingAlert(null);
      if (route.view !== "catalog" || !route.catalogMode) { setCatalogCreating(false); setEditingCatalog(null); setCatalogFields([]); }
      if (route.view !== "admin-integrations" || !route.integrationMode) setIntegrationCreating(false);
      if (route.view !== "incidents" || !route.ticketMode) {
        setDetail(null);
        setSelected(null);
      }
      if (route.view !== "portal-requests" || !route.portalTicketId) setPortalTicketDetail(null);
      setOperationalCreateView(route.operationalMode || null);
    };
    addEventListener("popstate", handleRouteChange);
    return () => removeEventListener("popstate", handleRouteChange);
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
    refreshNoc = () => {
      const query = dashboardTenantIds.length ? `?tenant_ids=${encodeURIComponent(dashboardTenantIds.join(","))}` : "";
      setNocLoading(true);
      return api(`/api/v1/dashboard/noc${query}`, token).then(setNoc).finally(() => setNocLoading(false));
    },
    refreshDashboard = () => {
      const query = dashboardTenantIds.length ? `?tenant_ids=${encodeURIComponent(dashboardTenantIds.join(","))}` : "";
      return api(`/api/v1/dashboard${query}`, token).then(setDashboard);
    };
  const refresh = () => {
    const requestedTenant=tenantId;
    return requestedTenant &&
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
        "agents",
        "users",
        "audit-log",
        "reports/summary",
        "actions/catalog",
        "benner/latest",
      ].map((p) => api(`/api/v1/${p}`, token, {}, requestedTenant)),
    ).then(([a, m, al, i, s, sl, h, t, r, ns, mw, ag, u, au, re, ac, b]) => {
      if(!isTenantResponseCurrent(activeTenantRef.current,requestedTenant))return;
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
      setAgents(ag);
      setUsers(u);
      setAudit(au);
      setReport(re);
      setActions(ac);
      setBenner(b);
    });
  };
  const loadTenantResource = (path, setter) => {
    const requestedTenant=tenantId;
    return api(`/api/v1/${path}`,token,{},requestedTenant).then((data)=>{
      if(isTenantResponseCurrent(activeTenantRef.current,requestedTenant))setter(data);
      return data;
    });
  };
  const loadKnowledgeArticles = (query = knowledgeQuery) =>
    loadTenantResource(`knowledge?q=${encodeURIComponent(query.trim())}`, setKnowledgeArticles);
  const loadDatabaseSelection = async (selectedId, sources = databaseSources) => {
    const requestedTenant=tenantId;
    if (!selectedId || !sources.length) {
      setBenner({ snapshot: null, checks: [], history: [] });
      return;
    }
    if (selectedId === "all") {
      const results = await Promise.all(sources.map((source) => api(`/api/v1/database-monitoring/latest?asset_id=${encodeURIComponent(source.asset_id)}`, token, {}, requestedTenant)));
      if(!isTenantResponseCurrent(activeTenantRef.current,requestedTenant))return;
      const snapshots = results.map((result) => result.snapshot).filter(Boolean);
      const checks = results.flatMap((result, index) => (result.checks || []).map((item) => ({ ...item, database_name: sources[index].database_name, server: sources[index].server })));
      const history = results.flatMap((result, index) => (result.history || []).map((item) => ({ ...item, database_name: sources[index].database_name, server: sources[index].server }))).sort((a, b) => new Date(b.collected_at) - new Date(a.collected_at));
      const latest = snapshots.sort((a, b) => new Date(b.collected_at) - new Date(a.collected_at))[0] || null;
      setBenner({ snapshot: latest ? { ...latest, duration_ms: snapshots.reduce((total, item) => total + Number(item.duration_ms || 0), 0) } : null, checks, history });
      return;
    }
    const result=await api(`/api/v1/database-monitoring/latest?asset_id=${encodeURIComponent(selectedId)}`,token,{},requestedTenant);
    if(isTenantResponseCurrent(activeTenantRef.current,requestedTenant))setBenner(result);
  };
  const loadDatabaseSources = async (preferredId = selectedDatabaseId) => {
    if (!tenantId) return;
    const requestedTenant=tenantId;
    const sources = await api("/api/v1/database-monitoring/databases",token,{},requestedTenant);
    if(!isTenantResponseCurrent(activeTenantRef.current,requestedTenant))return;
    setDatabaseSources(sources);
    const selectedId = preferredId === "all" || sources.some((item) => item.asset_id === preferredId) ? preferredId : "all";
    setSelectedDatabaseId(selectedId);
    await loadDatabaseSelection(selectedId, sources);
  };
  const refreshCurrentView = async () => {
    if (!tenantId && view !== "clients" && view !== "overview" && view !== "noc") return;
    switch (view) {
      case "overview":
        await refreshDashboard();
        break;
      case "noc":
        await refreshNoc();
        break;
      case "clients":
        await Promise.all([refreshTenants(), refreshDashboard()]);
        break;
      case "assets":
        await Promise.all([
          loadTenantResource("assets", setAssets),
          loadTenantResource("teams", setTeams),
          loadTenantResource("users", setUsers),
        ]);
        break;
      case "asset-topology":
        await Promise.all([loadTenantResource("asset-topology",setTopologyData),loadTenantResource("asset-identities",setAssetIdentities),loadTenantResource("assets",setAssets)]);
        break;
      case "event-quarantine":
        await Promise.all([loadTenantResource("event-quarantine",setQuarantineEvents),loadTenantResource("assets",setAssets)]);
        break;
      case "monitors":
      case "monitors-legacy":
        await Promise.all([
          loadTenantResource("monitors", setMonitors),
          loadTenantResource("assets", setAssets),
          loadTenantResource("teams", setTeams),
          loadTenantResource("users", setUsers),
        ]);
        break;
      case "databases":
        await loadDatabaseSources();
        break;
      case "alerts":
        await Promise.all([
          loadTenantResource("alerts", setAlerts),
          loadTenantResource("teams", setTeams),
          loadTenantResource("users", setUsers),
        ]);
        break;
      case "incidents":
        await Promise.all([
          loadTenantResource("incidents", setIncidents),
          loadTenantResource("teams", setTeams),
          loadTenantResource("users", setUsers),
          loadTenantResource("suppliers", setSuppliers),
          loadTenantResource("sla-policies", setSlas),
        ]);
        if (detail?.incident?.id) {
          const updatedDetail = await api(`/api/v1/incidents/${detail.incident.id}/detail`, token, {}, tenantId);
          setDetail(updatedDetail);
        }
        break;
      case "portal":
        await loadTenantResource("portal", setPortalData);
        break;
      case "portal-requests":
        await loadTenantResource("portal/tickets",setPortalTickets);
        break;
      case "catalog":
        await Promise.all([loadTenantResource("catalog/services?include_drafts=true",setCatalogServices),loadTenantResource("teams",setTeams),loadTenantResource("sla-policies",setSlas)]);
        break;
      case "queues":
        await Promise.all([loadTenantResource("queues",setQueues),loadTenantResource("teams",setTeams)]);
        break;
      case "my-queue":
        await Promise.all([loadTenantResource("my-queue", setMyQueue),loadTenantResource("notifications",setInAppNotifications)]);
        break;
      case "workload":
        await loadTenantResource("operations/workload", setWorkload);
        break;
      case "time-entries":
        await Promise.all([loadTenantResource("time-entries", setTimeEntries),loadTenantResource("incidents", setIncidents),loadTenantResource("teams", setTeams)]);
        break;
      case "contracts":
        await Promise.all([loadTenantResource("contracts",setContracts),loadTenantResource("service-engagements",setServiceEngagements)]);
        break;
      case "consumption":
        await Promise.all([loadTenantResource("consumption",setConsumptionData),loadTenantResource("consumption/review-queue",setConsumptionReviews)]);
        break;
      case "billing":
        await Promise.all([loadTenantResource("billing-cycles",setBillingCycles),loadTenantResource("contracts",setContracts)]);
        break;
      case "quality":
        await loadTenantResource("quality",setQualityData);
        break;
      case "profitability":
        await loadTenantResource("profitability",setProfitabilityData);
        break;
      case "major-incidents":
        await loadTenantResource("major-incidents",setMajorIncidents);
        break;
      case "knowledge": await loadKnowledgeArticles(); break;
      case "problems": await loadTenantResource("problems",setProblems); break;
      case "changes": await loadTenantResource("changes",setChanges); break;
      case "runbooks": await loadTenantResource("runbooks",setRunbooks); break;
      case "suppliers":
        await loadTenantResource("suppliers", setSuppliers);
        break;
      case "sla":
        await Promise.all([
          loadTenantResource("sla-policies", setSlas),
          loadTenantResource("holidays", setHolidays),
        ]);
        break;
      case "automations":
        await Promise.all([
          loadTenantResource("actions/catalog", setActions),
          loadTenantResource("assets", setAssets),
        ]);
        break;
      case "reports":
        await loadTenantResource("reports/summary", setReport);
        break;
      case "admin-teams":
        await Promise.all([
          loadTenantResource("teams", setTeams),
          loadTenantResource("users", setUsers),
        ]);
        break;
      case "admin-agents":
        await loadTenantResource("agents", setAgents);
        break;
      case "settings-notifications":
        await Promise.all([
          loadTenantResource("notification-rules", setRules),
          loadTenantResource("notification-settings", setNotificationSettings),
          loadTenantResource("teams", setTeams),
          loadTenantResource("users", setUsers),
        ]);
        break;
      case "settings-email":
      case "settings-whatsapp":
        await loadTenantResource("notification-settings", setNotificationSettings);
        break;
      case "admin-maintenance":
        await loadTenantResource("maintenance-windows", setMaintenance);
        break;
      case "admin-release": await loadTenantResource("feature-flags",setFeatureFlags); break;
      case "admin-integrations": await loadTenantResource("integrations",setIntegrationConnections); break;
      case "admin-pending": await loadTenantResource("integration-commands",setIntegrationCommands); break;
      case "admin-users":
        await Promise.all([loadTenantResource("users",setUsers),loadTenantResource("access-grants",setAccessGrants),loadTenantResource("service-engagements",setServiceEngagements),loadTenantResource("departments",setDepartments),loadTenantResource("teams",setTeams)]);
        break;
      case "admin":
        await loadTenantResource("audit-log", setAudit);
        break;
      default:
        await refresh();
    }
    setLastUpdatedAt(new Date());
  };
  useEffect(() => {
    if (token) {
      api("/api/v1/auth/me", token).then(setCurrentUser).catch(captureError);
      api("/api/v1/me/context", token).then(setAuthContext).catch(captureError);
      refreshTenants();
      refreshDashboard();
      const timer = setInterval(() => {
        validAccessToken().then(setToken).catch(() => {});
      }, 60000);
      return () => clearInterval(timer);
    }
  }, [token]);
  useEffect(() => {
    if (token && tenantId && (!authContext || viewAvailable(view))) refreshCurrentView().catch(captureError);
  }, [token, tenantId, view, authContext]);
  useEffect(() => {
    if (!token || !tenantId || !authContext || viewAvailable(view)) return;
    captureError(new ApiError("A rota solicitada está desabilitada ou exige uma capacidade que seu perfil não possui.", { status: 403, code: "feature_or_capability_denied" }));
    setViewState("overview");
    history.replaceState({}, "", viewPaths.overview);
  }, [token, tenantId, view, authContext, featureFlags]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "admin-teams" || !route.teamMode) return;
    if (route.teamMode === "new") {
      if (!editingTeam) openTeamForm();
      return;
    }
    const team = teams.find((item) => String(item.id) === route.teamId);
    if (team && String(editingTeam?.id || "") !== route.teamId) {
      setEditingTeam(team);
      setTeamContacts({
        emails: team.emails || [],
        numbers: team.whatsapp_numbers || [],
        groups: team.whatsapp_group_ids || (team.whatsapp_group_id ? [team.whatsapp_group_id] : []),
        members: (team.member_user_ids || []).map(String),
      });
    }
  }, [teams, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "monitors" || !route.monitorMode) return;
    if (route.monitorMode === "new") {
      setEditingMonitor(null);
      setShowMonitorForm(true);
      return;
    }
    const monitor = monitors.find((item) => String(item.id) === route.monitorId);
    if (monitor) {
      setEditingMonitor(monitor);
      setShowMonitorForm(true);
    }
  }, [monitors, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "assets" || !route.assetMode) return;
    if (route.assetMode === "new") {
      setEditingAsset(null);
      setShowAssetForm(true);
      return;
    }
    const asset = assets.find((item) => String(item.id) === route.assetId);
    if (asset) {
      setEditingAsset(asset);
      setShowAssetForm(true);
    }
  }, [assets, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "admin-users" || route.userMode !== "edit") return;
    const user = users.find((item) => String(item.id) === route.userId);
    if (user) {
      setEditingAccessUser(user);
      setEditingUserMaster(Boolean(user.is_master));
    }
  }, [users, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "billing" || route.billingMode !== "erp-confirm") return;
    setBillingConfirmationRequested(true);
    const cycle = billingCycles.find((item) => String(item.id) === route.billingCycleId);
    if (cycle) setConfirmingBillingCycle(cycle);
  }, [billingCycles, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "event-quarantine" || route.quarantineMode !== "map") return;
    setQuarantineMappingRequested(true);
    const event = quarantineEvents.find((item) => String(item.id) === route.quarantineId);
    if (event) setMappingQuarantineEvent(event);
  }, [quarantineEvents, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "suppliers" || !route.supplierMode) return;
    if (route.supplierMode === "new") { if (!editingSupplier) openSupplierForm(); return; }
    const supplier = suppliers.find((item) => String(item.id) === route.supplierId);
    if (supplier) setEditingSupplier(supplier);
  }, [suppliers, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "sla" || !route.slaMode) return;
    if (route.slaMode === "new") { if (!editingSla) openSlaForm(); return; }
    const policy = slas.find((item) => String(item.id) === route.slaId);
    if (policy) setEditingSla(policy);
  }, [slas, view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (route.view !== "queues" || !route.queueMode) return;
    if (route.queueMode === "new") { if (!editingQueue) openQueueForm(); return; }
    const queue = queues.find((item) => String(item.id) === route.queueId);
    if (queue) setEditingQueue(queue);
  }, [queues, view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view!=="admin-agents"||!route.agentMode)return; if(route.agentMode==="new"){if(!editingAgent)openAgentForm();return;} const item=agents.find((value)=>String(value.id)===route.agentId); if(item)setEditingAgent(item); }, [agents,view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view!=="settings-notifications"||!route.ruleMode)return; if(route.ruleMode==="new"){if(!editingRule)openRuleForm();return;} const item=rules.find((value)=>String(value.id)===route.ruleId); if(item)setEditingRule(item); }, [rules,view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view!=="admin-maintenance"||!route.maintenanceMode)return; if(route.maintenanceMode==="new"){if(!editingMaintenance)openMaintenanceForm();return;} const item=maintenance.find((value)=>String(value.id)===route.maintenanceId); if(item)setEditingMaintenance(item); }, [maintenance,view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view!=="clients"||!route.tenantMode)return;if(route.tenantMode==="new"){if(!editingTenant)openTenantForm();return;} const item=tenants.find((value)=>String(value.id)===route.tenantEditId); if(item)setEditingTenant(item); }, [tenants,view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view!=="alerts"||route.alertMode!=="edit")return; const item=alerts.find((value)=>String(value.id)===route.alertId); if(item)setEditingAlert(item); }, [alerts,view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view!=="catalog"||!route.catalogMode)return;if(route.catalogMode==="new"){setCatalogCreating(true);return;}const item=catalogServices.find((value)=>String(value.id)===route.catalogId);if(item&&String(editingCatalog?.id||"")!==route.catalogId)openCatalogVersion(item); }, [catalogServices,view]);
  useEffect(() => { const route=routeFromLocation(); if(route.view==="admin-integrations"&&route.integrationMode==="new")setIntegrationCreating(true); }, [view]);
  useEffect(() => { const route=routeFromLocation(); if(route.operationalMode)setOperationalCreateView(route.operationalMode); }, [view]);
  useEffect(() => {
    const route = routeFromLocation();
    if (!token || !tenantId || route.view !== "incidents" || !route.ticketMode || !route.ticketId) return;
    if (String(detail?.incident?.id || "") === route.ticketId) return;
    loadDetail({ id: route.ticketId }, route.ticketMode, false);
  }, [token, tenantId, view, routePath, detail?.incident?.id]);
  useEffect(() => {
    const route = routeFromLocation();
    if (!token || !tenantId || route.view !== "portal-requests" || !route.portalTicketId) return;
    if (String(portalTicketDetail?.ticket?.id || "") === route.portalTicketId) return;
    openPortalTicket({ id: route.portalTicketId }, false);
  }, [token, tenantId, view, routePath, portalTicketDetail?.ticket?.id]);
  useEffect(() => {
    if (view === "overview" || view === "noc" || tenantId || !tenants.length) return;
    const saved = localStorage.getItem("nexora_tenant");
    const fallback = tenants.find((item) => item.id === saved && item.active)
      || tenants.find((item) => item.slug === "morada" && item.active)
      || tenants.find((item) => item.active);
    if (fallback) {
      setTenantId(String(fallback.id));
      localStorage.setItem("nexora_tenant", String(fallback.id));
    }
  }, [view, tenantId, tenants]);
  useEffect(() => {
    if (token && view === "overview") refreshDashboard().then(() => setLastUpdatedAt(new Date())).catch(captureError);
  }, [dashboardTenantIds]);
  useEffect(() => {
    if (token && view === "noc") refreshNoc().then(() => setLastUpdatedAt(new Date())).catch(captureError);
  }, [token, view, dashboardTenantIds]);
  useEffect(() => {
    const label = allNavigationItems.find((item) => item[0] === view)?.[1] || "Nexora";
    document.title = `${label} | Nexora`;
    setMobileMenuOpen(false);
  }, [view]);
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
    remove = (path, id, label) => {
      if (!tenantId) return setNotice({ type: "error", text: "Selecione uma empresa antes de inativar este registro." });
      setPendingDelete({ path, id, label });
    },
    confirmDelete = async () => {
      if (!pendingDelete || deleteSubmitting) return;
      setDeleteSubmitting(true);
      await run(async () => {
        await api(`/api/v1/${pendingDelete.path}/${pendingDelete.id}`, token, { method: "DELETE" }, tenantId);
        setPendingDelete(null);
        await refresh();
      }, "Registro inativado com sucesso.");
      setDeleteSubmitting(false);
    },
    loadDetail = (i, mode = "detail", navigate = true) =>
      run(async () => {
        setSelected(i);
        setIncidentTab("service");
        setDetail(
          await api(`/api/v1/incidents/${i.id}/detail`, token, {}, tenantId),
        );
  const openIncidentCompletion = async (incident) => {
    await run(async () => {
      const incidentDetail = await api(`/api/v1/incidents/${incident.id}/detail`, token, {}, tenantId);
      const pendingTasks = (incidentDetail.tasks || []).filter((task) => ["pending", "in_progress"].includes(task.status));
      if (pendingTasks.length) throw new Error(`Conclua as ${pendingTasks.length} tarefa(s) pendente(s) antes de finalizar o chamado.`);
      setCompletingIncident(incident);
    });
  };
        if (navigate) {
          const suffix = mode === "edit" ? "/editar" : "";
          const path = `/operacao/chamados/${encodeURIComponent(i.id)}${suffix}`;
          history.pushState({}, "", path);
          setRoutePath(path);
        }
      }, "");
  const closeIncidentDetail = () => {
    setDetail(null);
    setSelected(null);
    history.pushState({}, "", viewPaths.incidents);
    setRoutePath(viewPaths.incidents);
  };
  const openPortalTicket = (ticket, navigate = true) => run(async () => {
    setPortalTicketDetail(await api(`/api/v1/portal/tickets/${ticket.id}`, token, {}, tenantId));
    if (navigate) {
      const path = `/portal/solicitacoes/${encodeURIComponent(ticket.id)}`;
      history.pushState({}, "", path);
      setRoutePath(path);
    }
  }, "");
  const closePortalTicket = () => {
    setPortalTicketDetail(null);
    history.pushState({}, "", viewPaths["portal-requests"]);
    setRoutePath(viewPaths["portal-requests"]);
  };
  const updateIncident = (i, status, extra = {}) =>
      submit(
        `incidents/${i.id}`,
        { status, version: i.version, ...extra },
        async () => {
          await refresh();
          await loadDetail({ ...i, version: i.version + 1 }, routeFromLocation().ticketMode || "detail", false);
        },
        "PATCH",
      ),
    claimIncident = (incident) => run(async () => {
      await api(`/api/v1/tickets/${incident.id}/claim`, token, { method: "POST" }, tenantId);
      await refresh();
    }, "Chamado atribuído a você e colocado em atendimento."),
    download = async (f) => {
      const r = await fetch(`/api/v1/reports/export/${f}`, {
        headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenantId },
      });
      if (!r.ok) throw Error("Falha ao gerar relatório");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await r.blob());
      a.download = `relatorio-incidentes.${f}`;
      a.click();
    },
    downloadTickets = async () => {
      const query=new URLSearchParams();
      if(incidentFilters.search)query.set("q",incidentFilters.search);
      if(incidentFilters.status!=="all")query.set("status",incidentFilters.status);
      if(incidentFilters.actionable !== false)query.set("actionable","true");
      if(incidentFilters.priority!=="all")query.set("priority",incidentFilters.priority);
      const response=await fetch(`/api/v1/tickets/export.csv?${query}`,{headers:{Authorization:`Bearer ${token}`,"X-Tenant-ID":tenantId}});
      if(!response.ok)throw Error("Falha ao exportar os chamados conciliados");
      const link=document.createElement("a");link.href=URL.createObjectURL(await response.blob());link.download="chamados-conciliados.csv";link.click();URL.revokeObjectURL(link.href);
    };
  const downloadBilling = (cycle) => run(async () => {
    const response = await fetch(`/api/v1/billing-cycles/${cycle.id}/export`, { method:"POST", headers:{ Authorization:`Bearer ${await validAccessToken()}`, "X-Tenant-ID":tenantId } });
    if (!response.ok) throw Error((await response.json().catch(()=>null))?.detail || "Falha ao exportar pré-faturamento");
    const anchor=document.createElement("a"); anchor.href=URL.createObjectURL(await response.blob()); anchor.download=`${cycle.external_key}.csv`; anchor.click(); URL.revokeObjectURL(anchor.href);
    await loadTenantResource("billing-cycles",setBillingCycles);
  },"Pré-faturamento exportado com sucesso.");
  const exportMonitors = (items) => {
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const rows = items.map((m) => [m.name, m.asset_name, m.monitor_type, m.responsible_team_name, m.responsible_user_name, m.active ? "Ativo" : "Inativo", translated(m.last_status), fmt(m.last_checked_at)]);
    const csv = [["Serviço", "Ambiente", "Tipo", "Equipe", "Técnico", "Cadastro", "Estado", "Última coleta"], ...rows].map((row) => row.map(quote).join(";")).join("\r\n");
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
    anchor.download = "servicos-monitorados.csv";
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  };
  const monitorBody = (m, changes = {}) => ({ asset_id: m.asset_id, name: m.name, monitor_type: m.monitor_type, configuration: m.configuration || {}, interval_seconds: m.interval_seconds || 60, severity: m.severity, active: m.active, responsible_team_id: m.responsible_team_id || null, responsible_user_id: m.responsible_user_id || null, notify_on_failure: m.notify_on_failure, notify_on_recovery: m.notify_on_recovery, auto_resolve_incident: m.auto_resolve_incident, ...changes });
  const setSelectedMonitorsActive = (activeValue) => run(async () => {
    const selectedItems = monitors.filter((m) => selectedMonitorIds.includes(m.id));
    await Promise.all(selectedItems.map((m) => api(`/api/v1/monitors/${m.id}`, token, { method: "PUT", body: JSON.stringify(monitorBody(m, { active: activeValue })) }, tenantId)));
    setSelectedMonitorIds([]);
    await refresh();
  }, activeValue ? "Serviços ativados com sucesso." : "Serviços inativados com sucesso.");
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
      <main className="login-shell"><section className="login-card"><Loading title="Carregando Nexora..." /></section></main>
    );
  if (!token) return <Login error={error} />;
  const active = incidents.filter(
      (i) => !["resolved", "closed", "ignored"].includes(i.status),
    ),
    filteredMonitors = monitors.filter((item) => {
      const target = `${item.configuration?.url || item.configuration?.host || ""}${item.configuration?.port ? `:${item.configuration.port}` : ""}`;
      const status = !item.active ? "inactive" : item.last_status === "down" ? "failed" : item.last_status === "unknown" ? "normalizing" : "active";
      return (monitorFilters.status === "all" || monitorFilters.status === status)
        && (!monitorFilters.search || `${item.name} ${item.asset_name || ""}`.toLowerCase().includes(monitorFilters.search.toLowerCase()))
        && (monitorFilters.type === "all" || item.monitor_type === monitorFilters.type)
        && (!monitorFilters.target || target.toLowerCase().includes(monitorFilters.target.toLowerCase()))
        && (monitorFilters.severity === "all" || item.severity === monitorFilters.severity)
        && (monitorFilters.team === "all" || String(item.responsible_team_id || "") === monitorFilters.team)
        && (monitorFilters.user === "all" || String(item.responsible_user_id || "") === monitorFilters.user)
        && (monitorFilters.asset === "all" || String(item.asset_id || "") === monitorFilters.asset);
    }),
    monitorPageCount = Math.max(1, Math.ceil(filteredMonitors.length / monitorPageSize)),
    pagedMonitors = filteredMonitors.slice((monitorPage - 1) * monitorPageSize, monitorPage * monitorPageSize),
    filteredAlerts = alerts.filter((item) => {
      const opened = item.opened_at ? new Date(item.opened_at) : null;
      const from = alertFilters.openedFrom ? new Date(`${alertFilters.openedFrom}T00:00:00`) : null;
      const to = alertFilters.openedTo ? new Date(`${alertFilters.openedTo}T23:59:59`) : null;
      return (!alertFilters.search || `${item.title} ${item.message || ""}`.toLowerCase().includes(alertFilters.search.toLowerCase()))
        && (alertFilters.severity === "all" || item.severity === alertFilters.severity)
        && (alertFilters.status === "all" || item.status === alertFilters.status)
        && (alertFilters.monitor === "all" || String(item.monitor_id || "") === alertFilters.monitor)
        && (!from || (opened && opened >= from))
        && (!to || (opened && opened <= to));
    }),
    alertPageCount = Math.max(1, Math.ceil(filteredAlerts.length / alertPageSize)),
    pagedAlerts = filteredAlerts.slice((alertPage - 1) * alertPageSize, alertPage * alertPageSize),
    filteredTeams = teams.filter((item) => {
      const matchesSearch = `${item.name} ${item.member_names || ""} ${(item.emails || []).join(" ")}`.toLowerCase().includes(teamSearch.toLowerCase());
      const matchesStatus = teamStatus === "all" || (teamStatus === "active" ? item.active : !item.active);
      return matchesSearch && matchesStatus;
    }),
    distinctTeamMembers = new Set(filteredTeams.flatMap((item) => (item.member_user_ids || []).map(String))).size,
    visibleCompanies = tenants.filter((t) =>
      `${t.name} ${t.slug}`.toLowerCase().includes(companySearch.toLowerCase()),
    ),
    filteredIncidents = incidents.filter((item) => {
      const searchText = `${item.title || ""} ${item.glpi_ticket_id || ""} ${item.assignee_team_name || ""} ${item.assignee_user_name || ""}`.toLowerCase();
      return (!incidentFilters.search || searchText.includes(incidentFilters.search.toLowerCase()))
        && (incidentFilters.priority === "all" || item.priority === incidentFilters.priority)
        && (incidentFilters.status === "all" || item.status === incidentFilters.status)
        && (incidentFilters.actionable === false || !["resolved", "closed", "ignored"].includes(item.status))
        && (incidentFilters.sla === "all" || item.sla_status === incidentFilters.sla)
        && (incidentFilters.glpi === "all" || item.sync_status === incidentFilters.glpi)
        && (incidentFilters.team === "all" || item.assignee_team_id === incidentFilters.team)
        && (incidentFilters.assignee === "all" || item.assignee_user_id === incidentFilters.assignee)
        && (!incidentFilters.mine || item.assignee_user_id === currentUser?.id || teams.some((team) => team.id === item.assignee_team_id && (team.members || []).some((member) => member.id === currentUser?.id)));
    }),
    incidentPageCount = Math.max(1, Math.ceil(filteredIncidents.length / incidentPageSize)),
    pagedIncidents = filteredIncidents.slice((incidentPage - 1) * incidentPageSize, incidentPage * incidentPageSize),
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
  const selectDrilldownTenant = (id) => {
    const selectedId = id || (dashboardTenantIds.length === 1 ? dashboardTenantIds[0] : tenantId);
    if (selectedId) {
      setTenantId(String(selectedId));
      localStorage.setItem("nexora_tenant", String(selectedId));
    }
  };
  const openServices = (status = "all", selectedTenant = null) => {
    const affectedTenants = dashboard.status_tenants?.[status] || [];
    const drilldownTenant = selectedTenant || (affectedTenants.length === 1 ? affectedTenants[0] : null);
    selectDrilldownTenant(drilldownTenant);
    setMonitorFilters((current) => ({ ...current, status }));
    setMonitorPage(1);
    setView("monitors");
  };
  const openIncidents = (priority = "all", selectedTenant = null) => {
    selectDrilldownTenant(selectedTenant);
    setIncidentFilters((current) => ({ ...current, priority }));
    setDetail(null);
    setView("incidents");
  };
  return (
    <AppShell>
      <aside id="main-navigation" className={mobileMenuOpen ? "mobile-open" : ""}>
        <div className="logo">
          <span>
            <LogoMark />
          </span>
          <div>
            <strong>Nexora</strong>
            <small>OPERAÇÃO INTELIGENTE EM ESCALA</small>
          </div>
        </div>
        <nav aria-label="Navegação principal">
          {menu.map(([k, l, i]) => {
            const configuredSubmenu = navigationSubmenus[k];
            const submenu = configuredSubmenu?.filter(([sk]) => viewAvailable(sk));
            if ((configuredSubmenu && !submenu.length) || (!configuredSubmenu && !viewAvailable(k))) return null;
            const active = view === k || submenu?.some(([sk]) => sk === view);
            return submenu ? (
              <div className={`nav-group ${active ? "expanded" : ""}`} key={k}>
                <button className={active ? "active" : ""} aria-label={l} aria-expanded={active} onClick={() => setView(submenu[0][0])}>
                  <NavIcon name={i} /><span>{l}</span><b className="nav-chevron">⌄</b>
                </button>
                <div className="nav-submenu">
                  {submenu.map(([sk, sl, si]) => <button key={sk} className={view === sk ? "active" : ""} aria-label={sl} aria-current={view === sk ? "page" : undefined} onClick={() => setView(sk)}><NavIcon name={si} /><span>{sl}</span></button>)}
                </div>
              </div>
            ) : (
              <button key={k} className={view === k ? "active" : ""} aria-label={l} aria-current={view === k ? "page" : undefined} onClick={() => setView(k)}>
                <NavIcon name={i} /><span>{l}</span>
              </button>
            );
          })}
        </nav>
        <div className="account">
          <span>{identity?.name || identity?.preferred_username}</span>
          <small>{currentUser?.is_master ? "Master • acesso total" : `${tenants.length} cliente(s) autorizado(s)`}</small>
          <button onClick={logout}>Sair</button>
        </div>
      </aside>
      <div className="workspace">
        <div className="topbar">
          <button className="mobile-menu-button" aria-label={mobileMenuOpen ? "Fechar menu principal" : "Abrir menu principal"} aria-expanded={mobileMenuOpen} aria-controls="main-navigation" onClick={() => setMobileMenuOpen((value) => !value)}><NavIcon name="menu" /></button>
          <div className="breadcrumb">
            Nexora <b>›</b>
            <strong>{allNavigationItems.find((x) => x[0] === view)?.[1]}</strong>
          </div>
          {view === "overview" || view === "noc" ? (
            <div className="topbar-company-filter">
              <button className="topbar-company-trigger" onClick={() => setDashboardCompanyFilterOpen((value) => !value)}>
                <NavIcon name="building" />
                <span>{dashboardTenantIds.length ? `${dashboardTenantIds.length} empresa(s) selecionada(s)` : `Todas as empresas (${tenants.filter((item) => item.active).length})`}</span>
                <b>⌄</b>
              </button>
              {dashboardCompanyFilterOpen && <div className="dashboard-company-options">
                <label><input type="checkbox" checked={!dashboardTenantIds.length} onChange={() => setDashboardTenantIds([])} /> Todas as empresas</label>
                {tenants.filter((item) => item.active).map((item) => <label key={item.id}>
                  <input type="checkbox" checked={dashboardTenantIds.includes(String(item.id))} onChange={(e) => setDashboardTenantIds((current) => e.target.checked ? [...new Set([...current, String(item.id)])] : current.filter((id) => id !== String(item.id)))} /> {item.name}
                </label>)}
                <button className="primary" onClick={() => setDashboardCompanyFilterOpen(false)}>Aplicar</button>
              </div>}
            </div>
          ) : <select
            aria-label="Selecionar empresa"
            value={tenantId}
            onChange={(e) => {
              const value = e.target.value;
              setTenantId(value);
              localStorage.setItem("nexora_tenant", value);
            }}
          >
            {!tenantId && <option value="">Selecione uma empresa</option>}
            <optgroup label="Empresas cadastradas">
              {tenants
                .filter((t) => t.active)
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </optgroup>
          </select>}
          <input aria-label="Pesquisa global" placeholder="Pesquisar..." />
        </div>
        <main className="content">
          <header className={view === "monitors" ? "services-page-header" : ""}>
            <div>
              {view === "monitors" && <small>MONITORAMENTO</small>}
              <h1>{view === "overview" ? <>Dashboard <span className="overview-title">(Visão geral da operação)</span></> : allNavigationItems.find((x) => x[0] === view)?.[1]} {view === "monitors" && tenant?.name ? <span>({tenant.name})</span> : null}</h1>
              <p>{view === "overview" ? "Acompanhe a saúde dos ambientes, serviços e incidentes em tempo real." : view === "noc" ? "Painel operacional de todos os ambientes monitorados: identifique falhas e priorize a atuação." : view === "monitors" ? "Monitore e gerencie os serviços do ambiente. Os dados são coletados automaticamente." : (tenant?.name || "Visão consolidada da operação")}</p>
            </div>
            {view === "databases" && databaseSources.length > 0 && <label className="database-selector database-header-selector"><span>Bancos monitorados</span><select value={selectedDatabaseId} onChange={async (e) => { const value = e.target.value; setSelectedDatabaseId(value); await loadDatabaseSelection(value); }}>
              <option value="all">Todos os bancos ({databaseSources.length})</option>
              {databaseSources.map((source) => <option key={source.asset_id} value={source.asset_id}>{source.database_name} · {source.server || source.asset_name}</option>)}
            </select></label>}
            <div className="page-refresh">
              <span className="page-refresh-time">
                <small>Última atualização</small>
                <strong>{lastUpdatedAt ? lastUpdatedAt.toLocaleString("pt-BR") : "Aguardando dados"}</strong>
              </span>
              <button
                className="refresh-button"
                disabled={refreshing}
                onClick={() => run(async () => {
                  setRefreshing(true);
                  try {
                    await refreshCurrentView();
                  } finally {
                    setRefreshing(false);
                  }
                }, "Tela atualizada com sucesso.")}
              >
                <ActionIcon name="refresh" />{refreshing ? "Atualizando..." : "Atualizar"}
              </button>
            </div>
          </header>
          {error && (errorStatus === 401
            ? <SessionExpired onLogin={startLogin} />
            : errorStatus === 403
              ? <Forbidden description={error} />
              : errorStatus === 409
                ? <Conflict description={error} onReview={clearError} />
                : <Unavailable title="Não foi possível concluir a operação." description={error} />)}
          {view === "overview" && (
            <section className="dashboard-overview">
              <section className="dashboard-kpis">
                {[
                  ["server", dashboard.totals.services_active || 0, "Serviços ativos", "green", "active"],
                  ["alert", dashboard.totals.services_failed || 0, "Serviços em falha", "red", "failed"],
                  ["clock", dashboard.totals.services_normalizing || 0, "Sem dados", "amber", "normalizing"],
                  ["pause", dashboard.totals.services_inactive || 0, "Inativos", "blue", "inactive"],
                  ["clock", `${dashboard.totals.availability ?? 100}%`, "Disponibilidade", "purple", "reports"],
                ].map(([icon, value, label, tone, destination]) => <article className={`${tone} dashboard-link-card`} role="link" tabIndex="0" key={label} onClick={() => destination === "reports" ? setView("reports") : openServices(destination)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); destination === "reports" ? setView("reports") : openServices(destination); } }}><i><NavIcon name={icon} /></i><div><strong>{value}</strong><b>{label}</b><span>Ver detalhes →</span></div></article>)}
              </section>

              <section className="dashboard-charts">
                <article className="panel dashboard-status-card">
                  <h2>Status dos serviços</h2>
                  <div className="dashboard-donut" style={{ "--active": `${Math.round(((dashboard.totals.services_active || 0) / Math.max(1, (dashboard.totals.services_active || 0) + (dashboard.totals.services_failed || 0) + (dashboard.totals.services_normalizing || 0) + (dashboard.totals.services_inactive || 0))) * 100)}%` }}><b>{(dashboard.totals.services_active || 0) + (dashboard.totals.services_failed || 0) + (dashboard.totals.services_normalizing || 0) + (dashboard.totals.services_inactive || 0)}</b><span>Total</span></div>
                  <ul><li onClick={() => openServices("active")}><i className="green" />Ativos <b>{dashboard.totals.services_active || 0}</b></li><li onClick={() => openServices("failed")}><i className="red" />Em falha <b>{dashboard.totals.services_failed || 0}</b></li><li onClick={() => openServices("normalizing")}><i className="amber" />Sem dados <b>{dashboard.totals.services_normalizing || 0}</b></li><li onClick={() => openServices("inactive")}><i className="gray" />Inativos <b>{dashboard.totals.services_inactive || 0}</b></li></ul>
                </article>
                <article className="panel dashboard-severity-card"><h2>Incidentes por prioridade</h2><div className="severity-bars">{[["critical","Crítica"],["high","Alta"],["medium","Média"],["low","Baixa"]].map(([key,label]) => <div className="dashboard-chart-link" role="link" tabIndex="0" onClick={() => openIncidents(key)} onKeyDown={(e) => { if (e.key === "Enter") openIncidents(key); }} key={key}><b>{dashboard.severity?.[key] || 0}</b><i className={key} style={{ height: `${32 + Math.min(95, (dashboard.severity?.[key] || 0) * 10)}px` }} /><span>{label}</span></div>)}</div></article>
                <article className="panel dashboard-company-card"><h2>Operação por empresa</h2>{(dashboard.companies || []).map((company) => <div className="company-health dashboard-row-link" role="link" tabIndex="0" onClick={() => openServices("all", company.id)} onKeyDown={(e) => { if (e.key === "Enter") openServices("all", company.id); }} key={company.id}><span>{company.name}</span><b>{company.services} serviços</b><Status value={company.services_failed ? "failed" : company.services_normalizing || company.open_alerts ? "attention" : "healthy"} /></div>)}</article>
              </section>

              <section className="dashboard-main-grid">
                <article className="panel dashboard-wide"><div className="section-head"><h2>Últimos incidentes</h2><button onClick={() => openIncidents()}>Ver todos</button></div><DataGrid columns={[{key:"priority",label:"Prioridade",render:(row)=><Status value={row.priority}/>},{key:"title",label:"Título"},{key:"service_name",label:"Serviço",render:(row)=>row.service_name||"—"},{key:"company",label:"Empresa"},{key:"created_at",label:"Abertura",render:(row)=>fmt(row.created_at)},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>}]} rows={dashboard.recent_incidents || []} onRow={(row) => openIncidents(row.priority, row.tenant_id)} /></article>
                <article className="panel"><div className="section-head"><h2>Serviços mais críticos</h2><button onClick={() => openServices()}>Ver todos</button></div><div className="critical-service-list">{(dashboard.critical_services || []).map((service) => <div className="dashboard-row-link" role="link" tabIndex="0" onClick={() => openServices(service.last_status === "down" ? "failed" : service.last_status === "up" ? "active" : "normalizing", service.tenant_id)} key={service.id}><span><b>{service.name}</b><small>{service.company}</small></span><strong>{service.incidents_7d} incidente(s)</strong><Status value={service.last_status === "down" ? "failed" : service.last_status === "up" ? "active" : "pending"} /></div>)}</div></article>
              </section>

              <section className="dashboard-bottom-grid">
                <article className="panel"><div className="section-head"><h2>Ambientes</h2><button onClick={() => setView("assets")}>Ver todos</button></div><div className="environment-list">{(dashboard.environments || []).map((environment) => <div className="dashboard-row-link" role="link" tabIndex="0" onClick={() => { selectDrilldownTenant(environment.tenant_id); setView("assets"); }} key={environment.id}><span>{environment.name}<small>{environment.company}</small></span><b>{environment.services} serviços</b><Status value={environment.status} /></div>)}</div></article>
                <article className="panel quick-actions"><h2>Ações rápidas</h2><div><button onClick={() => setView("monitors")}><ActionIcon name="add" />Novo serviço</button><button onClick={() => setView("incidents")}><NavIcon name="alert" />Incidentes</button><button onClick={() => setView("alerts")}><NavIcon name="bell" />Alertas</button><button onClick={() => setView("reports")}><NavIcon name="report" />Relatórios</button><button onClick={() => setView("assets")}><NavIcon name="server" />Ambientes</button><button onClick={() => setView("settings-notifications")}><NavIcon name="settings" />Configurações</button></div></article>
              </section>
            </section>
          )}
          {view === "clients" && (
            editingTenant ? <FormPage title={editingTenant.id?"Editar empresa":"Nova empresa"} description="Defina a identificação e o contexto operacional da Empresa." onBack={closeTenantForm}><form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(tenantSubmitting)return;const f=new FormData(event.currentTarget);setTenantSubmitting(true);await run(async()=>{await api(`/api/v1/tenants${editingTenant.id?`/${editingTenant.id}`:""}`,token,{method:editingTenant.id?"PUT":"POST",body:JSON.stringify({name:f.get("name"),slug:f.get("slug"),timezone:f.get("timezone"),active:editingTenant.active})});await refreshTenants();closeTenantForm();},editingTenant.id?"Empresa atualizada com sucesso.":"Empresa criada com sucesso.");setTenantSubmitting(false);}}><FormSection title="Identificação"><Input name="name" label="Nome" defaultValue={editingTenant.name||""} required/><Input name="slug" label="Identificador" defaultValue={editingTenant.slug||""} required/><Input name="timezone" label="Fuso horário" defaultValue={editingTenant.timezone||"America/Sao_Paulo"} required/></FormSection><FormActions onCancel={closeTenantForm} submitting={tenantSubmitting} submitLabel={editingTenant.id?"Salvar empresa":"Criar empresa"}/></form></FormPage> : <section className="panel">
              <div className="section-head">
                <div>
                  <h2>Empresas monitoradas</h2>
                  <p>Segregação completa por organização.</p>
                </div>
                <button onClick={() => openTenantForm()}>
                  ＋ Nova empresa
                </button>
              </div>
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
                        <button onClick={() => openTenantForm(t)}>
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
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}
          {view === "asset-topology" && (
            operationalCreateView==="asset-identity"?<FormPage title="Novo vínculo de ativo" description="Associe uma identidade externa imutável a um ativo autorizado da Empresa." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("asset-identities",{asset_id:f.get("asset_id"),source:f.get("source"),connection_key:f.get("connection_key"),external_id:f.get("external_id"),verified:true,metadata:{}},async()=>{await Promise.all([loadTenantResource("asset-identities",setAssetIdentities),loadTenantResource("asset-topology",setTopologyData)]);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Identidade"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Ativo Nexora</span><select name="asset_id" required><option value="">Selecione</option>{assets.map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>Origem</span><select name="source"><option value="glpi">GLPI</option><option value="zabbix">Zabbix</option><option value="agent">Agente</option><option value="nexora">Nexora</option></select></label><Input name="connection_key" label="Conexão/origem" required/><Input name="external_id" label="ID externo imutável" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Validar vínculo"/></form></FormPage>:<section className="operations-screen"><section className="panel">
              <div className="section-head"><div><h2>Ativos e topologia</h2><p>Vínculos explícitos por origem e conexão. Nome ou IP, isoladamente, não fazem correlação.</p></div><button className="primary" onClick={()=>openOperationalCreate("asset-identity")}>＋ Novo vínculo</button></div>
              <DataGrid showEmptyGrid rows={assetIdentities} columns={[{key:"asset_name",label:"Ativo"},{key:"source",label:"Origem"},{key:"connection_key",label:"Conexão"},{key:"external_id",label:"ID externo"},{key:"verified",label:"Validado",render:(row)=><Status value={row.verified?"healthy":"pending"}/>}]} />
              <h3>Dependências</h3><DataGrid showEmptyGrid rows={topologyData.edges||[]} columns={[{key:"upstream_asset_id",label:"Origem"},{key:"downstream_asset_id",label:"Dependente"},{key:"dependency_type",label:"Relação"},{key:"source",label:"Fonte"}]} />
            </section></section>
          )}
          {view === "event-quarantine" && quarantineMappingRequested && mappingQuarantineEvent && (
            <FormPage title="Mapear evento em quarentena" description="Associe a identidade externa a um ativo Nexora da Empresa selecionada." onBack={closeQuarantineMapping}>
              <form className="nexora-form" onSubmit={async (event) => {
                event.preventDefault();
                if (quarantineSubmitting || !tenantId) return;
                const form = new FormData(event.currentTarget);
                setQuarantineSubmitting(true);
                await run(async () => {
                  await api(`/api/v1/event-quarantine/${mappingQuarantineEvent.id}/map`, token, { method: "POST", body: JSON.stringify({ asset_id: form.get("asset_id") }) }, tenantId);
                  await loadTenantResource("event-quarantine", setQuarantineEvents);
                  closeQuarantineMapping();
                }, "Identidade externa mapeada com sucesso.");
                setQuarantineSubmitting(false);
              }}>
                <FormSection title="Identidade externa" description="Estes dados vieram da integração e não podem ser alterados neste fluxo.">
                  <Input label="Empresa" value={tenant?.name || "Empresa selecionada"} readOnly />
                  <Input label="Origem" value={mappingQuarantineEvent.source || "—"} readOnly />
                  <Input label="Conexão" value={mappingQuarantineEvent.connection_key || "—"} readOnly />
                  <Input label="Ativo externo" value={mappingQuarantineEvent.external_asset_id || "—"} readOnly />
                  <Input label="Motivo da quarentena" value={mappingQuarantineEvent.reason || "—"} readOnly />
                </FormSection>
                <FormSection title="Ativo Nexora" description="Escolha explicitamente o ativo correto. Nome ou IP não produzem correlação automática.">
                  <label className="field"><span>Ativo de destino</span><select name="asset_id" required autoFocus defaultValue=""><option value="">Selecione um ativo</option>{assets.filter((item) => item.active !== false).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.address || "sem endereço"}</option>)}</select></label>
                </FormSection>
                <FormActions onCancel={closeQuarantineMapping} submitting={quarantineSubmitting} submitLabel="Confirmar mapeamento" />
              </form>
            </FormPage>
          )}
          {view === "event-quarantine" && quarantineMappingRequested && !mappingQuarantineEvent && (
            <section className="panel"><Empty>Evento pendente não encontrado ou indisponível para a Empresa selecionada.</Empty><div className="form-actions"><button className="secondary" onClick={closeQuarantineMapping}>Voltar à quarentena</button></div></section>
          )}
          {view === "event-quarantine" && !quarantineMappingRequested && (
            <section className="panel"><div className="section-head"><div><h2>Quarentena de eventos</h2><p>Eventos sem identidade explícita ficam isolados e nunca são atribuídos à empresa raiz.</p></div></div>
              <DataGrid showEmptyGrid rows={quarantineEvents} columns={[{key:"source",label:"Origem"},{key:"connection_key",label:"Conexão"},{key:"external_asset_id",label:"Ativo externo"},{key:"reason",label:"Motivo"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"created_at",label:"Recebido",render:(row)=>fmt(row.created_at)}]} actions={(row)=>row.status==="pending"&&<button onClick={()=>openQuarantineMapping(row)}>Mapear</button>} />
            </section>
          )}
          {view === "major-incidents" && (
            operationalCreateView==="major-incidents"?<FormPage title="Declarar incidente maior" description="Comando restrito à operadora e comunicação sanitizada por Empresa impactada." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("major-incidents",{title:f.get("title"),summary:f.get("summary"),severity:f.get("severity"),impacts:[]},async()=>{await loadTenantResource("major-incidents",setMajorIncidents);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Declaração"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="title" label="Título" required/><label className="field"><span>Resumo operacional</span><textarea name="summary" required/></label><label className="field"><span>Severidade</span><select name="severity"><option value="high">Alta</option><option value="critical">Crítica</option></select></label></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Declarar incidente"/></form></FormPage>:<section className="operations-screen"><section className="panel"><div className="section-head"><div><h2>Incidentes maiores</h2><p>Comando restrito à operadora e comunicação sanitizada por empresa impactada.</p></div><button className="primary" onClick={()=>openOperationalCreate("major-incidents")}>＋ Declarar incidente</button></div><DataGrid showEmptyGrid rows={majorIncidents} columns={[{key:"title",label:"Incidente"},{key:"severity",label:"Severidade",render:(row)=><Status value={row.severity}/>},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"impacted_companies",label:"Empresas"},{key:"declared_at",label:"Declaração",render:(row)=>fmt(row.declared_at)}]} /></section></section>
          )}
          {view === "knowledge" && (operationalCreateView==="knowledge"?<FormPage title="Novo artigo" description="O artigo nasce como rascunho e respeita ACL antes da busca." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("knowledge",{title:f.get("title"),content:f.get("content"),visibility:f.get("visibility"),acl_roles:["admin","manager","operator"]},async()=>{await loadKnowledgeArticles();closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Conteúdo"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="title" label="Título" required/><label className="field"><span>Conteúdo</span><textarea name="content" rows="8" required/></label><label className="field"><span>Visibilidade</span><select name="visibility"><option value="internal">Interno</option><option value="customer">Cliente</option><option value="public">Público</option></select></label></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Criar rascunho"/></form></FormPage>:<section className="panel"><div className="section-head"><div><h2>Conhecimento</h2><p>Artigos projetados do GLPI com ACL aplicada antes da busca.</p></div><button className="primary" onClick={()=>openOperationalCreate("knowledge")}>＋ Novo artigo</button></div><form className="incident-filters" aria-label="Busca de conhecimento" onSubmit={async(event)=>{event.preventDefault();await loadKnowledgeArticles();}}><div className="incident-filters-heading"><strong><ActionIcon name="filter" /> Busca autorizada</strong><button type="button" className="secondary" onClick={async()=>{setKnowledgeQuery("");await loadKnowledgeArticles("");}}>Limpar busca</button></div><label className="field incident-search-field"><span>Pesquisar artigos</span><input value={knowledgeQuery} onChange={(event)=>setKnowledgeQuery(event.target.value)} placeholder="Título ou conteúdo..." maxLength="200" /></label><button className="primary" type="submit">Pesquisar</button></form><DataGrid showEmptyGrid empty={knowledgeQuery?"Nenhum artigo autorizado corresponde à busca.":"Nenhum artigo autorizado disponível."} rows={knowledgeArticles} columns={[{key:"title",label:"Artigo"},{key:"visibility",label:"Visibilidade"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"version",label:"Versão"},{key:"updated_at",label:"Atualização",render:(row)=>fmt(row.updated_at)}]} actions={(row)=><>{row.status==="draft"&&<button onClick={()=>submit(`knowledge/${row.id}/status`,{status:"review",reason:"Enviado para revisão editorial"},loadKnowledgeArticles,"PATCH")}>Revisar</button>}{row.status==="review"&&<button onClick={()=>submit(`knowledge/${row.id}/status`,{status:"published",reason:"Conteúdo e ACL revisados"},loadKnowledgeArticles,"PATCH")}>Publicar</button>}</>}/></section>)}
          {view === "problems" && (operationalCreateView==="problems"?<FormPage title="Novo problema" description="Registre hipótese e contorno sem antecipar uma causa raiz não confirmada." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("problems",{title:f.get("title"),description:f.get("description"),hypothesis:f.get("hypothesis")||null,workaround:f.get("workaround")||null},async()=>{await loadTenantResource("problems",setProblems);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Registro"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="title" label="Problema" required/><label className="field"><span>Descrição</span><textarea name="description" required/></label><Input name="hypothesis" label="Hipótese"/><Input name="workaround" label="Contorno"/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Registrar problema"/></form></FormPage>:<section className="panel"><div className="section-head"><div><h2>Problemas</h2><p>Hipótese, causa raiz, erro conhecido e contorno permanecem rastreáveis.</p></div><button className="primary" onClick={()=>openOperationalCreate("problems")}>＋ Novo problema</button></div><DataGrid showEmptyGrid rows={problems} columns={[{key:"title",label:"Problema"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"hypothesis",label:"Hipótese"},{key:"root_cause",label:"Causa confirmada"},{key:"workaround",label:"Contorno"}]}/></section>)}
          {view === "changes" && (operationalCreateView==="changes"?<FormPage title="Nova mudança" description="Defina execução, validação e retorno antes da avaliação de risco." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("changes",{title:f.get("title"),change_type:f.get("change_type"),risk:f.get("risk"),implementation_plan:f.get("implementation_plan"),validation_plan:f.get("validation_plan"),rollback_plan:f.get("rollback_plan")},async()=>{await loadTenantResource("changes",setChanges);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Classificação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="title" label="Mudança" required/><label className="field"><span>Tipo</span><select name="change_type"><option value="standard">Padrão</option><option value="normal">Normal</option><option value="emergency">Emergencial</option></select></label><label className="field"><span>Risco</span><select name="risk"><option value="low">Baixo</option><option value="medium">Médio</option><option value="high">Alto</option><option value="critical">Crítico</option></select></label></FormSection><FormSection title="Planos"><label className="field"><span>Plano de execução</span><textarea name="implementation_plan" required/></label><label className="field"><span>Plano de validação</span><textarea name="validation_plan" required/></label><label className="field"><span>Plano de retorno</span><textarea name="rollback_plan" required/></label></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Registrar mudança"/></form></FormPage>:<section className="panel"><div className="section-head"><div><h2>Mudanças</h2><p>Risco, aprovação, janela, implementação, validação e retorno.</p></div><button className="primary" onClick={()=>openOperationalCreate("changes")}>＋ Nova mudança</button></div><DataGrid showEmptyGrid rows={changes} columns={[{key:"title",label:"Mudança"},{key:"change_type",label:"Tipo"},{key:"risk",label:"Risco",render:(row)=><Status value={row.risk}/>},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"window_start",label:"Janela",render:(row)=>fmt(row.window_start)}]}/></section>)}
          {view === "runbooks" && (operationalCreateView==="runbooks"?<FormPage title="Novo runbook" description="Somente passos tipados do catálogo fechado são permitidos; shell arbitrário continua proibido." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("runbooks",{code:f.get("code"),name:f.get("name"),risk:f.get("risk"),parameters_schema:{type:"object",properties:{}},target_selector:{kind:"asset"},preconditions:[],steps:[{type:"collect_diagnostics"}],postconditions:[],lock_key:f.get("lock_key"),approval_required:true},async()=>{await loadTenantResource("runbooks",setRunbooks);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Definição"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="code" label="Código" placeholder="DIAG.SQL" required/><Input name="name" label="Nome" required/><label className="field"><span>Risco</span><select name="risk"><option value="low">Baixo</option><option value="medium">Médio</option><option value="high">Alto</option><option value="critical">Crítico</option></select></label><Input name="lock_key" label="Lock do alvo" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Criar versão"/></form></FormPage>:<section className="panel"><div className="section-head"><div><h2>Runbooks</h2><p>Catálogo fechado, parâmetros tipados, locks, pré/pós-condições e aprovação. Shell arbitrário é proibido.</p></div><button className="primary" onClick={()=>openOperationalCreate("runbooks")}>＋ Novo runbook</button></div><DataGrid showEmptyGrid rows={runbooks} columns={[{key:"name",label:"Runbook"},{key:"code",label:"Código"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"current_version",label:"Versão"},{key:"created_at",label:"Criação",render:(row)=>fmt(row.created_at)}]} actions={(row)=><>{row.status==="draft"&&<button onClick={()=>submit(`runbooks/${row.id}/status`,{status:"review",reason:"Versão enviada para revisão"},async()=>loadTenantResource("runbooks",setRunbooks),"PATCH")}>Revisar</button>}{row.status==="review"&&<button onClick={()=>submit(`runbooks/${row.id}/status`,{status:"published",reason:"Risco e passos aprovados"},async()=>loadTenantResource("runbooks",setRunbooks),"PATCH")}>Publicar</button>}{row.status==="published"&&<button onClick={()=>submit(`runbooks/${row.id}/executions`,{parameters:{},target_asset_id:null},()=>{},"POST")}>Solicitar execução</button>}</>}/></section>)}
          {view === "assets" && showAssetForm && (
            <FormPage title={editingAsset ? "Editar ambiente" : "Novo ambiente"} description={`${tenant?.name || "Empresa selecionada"} · cadastre o ativo que será monitorado.`} onBack={closeAssetForm}>
              <form className="nexora-form" onSubmit={async (event) => {
                event.preventDefault();
                if (assetSubmitting) return;
                const form = new FormData(event.currentTarget);
                setAssetSubmitting(true);
                try {
                  await api(`/api/v1/${editingAsset ? `assets/${editingAsset.id}` : "assets"}`, token, {
                    method: editingAsset ? "PUT" : "POST",
                    body: JSON.stringify({ name: form.get("name"), kind: form.get("kind"), address: form.get("address") || null, criticality: form.get("criticality"), metadata: editingAsset?.metadata || {}, active: editingAsset?.active ?? true }),
                  }, tenantId);
                  await refresh();
                  setNotice({ type: "success", text: editingAsset ? "Ambiente atualizado com sucesso." : "Ambiente criado com sucesso." });
                  closeAssetForm();
                } catch (exception) {
                  captureError(exception);
                  setNotice({ type: "error", text: exception.message });
                } finally {
                  setAssetSubmitting(false);
                }
              }}>
                <FormSection title="Identificação" description="O ambiente permanece vinculado à Empresa selecionada.">
                  <label className="field"><span>Empresa</span><input value={tenant?.name || ""} readOnly aria-readonly="true" /></label>
                  <label className="field"><span>Nome</span><input name="name" defaultValue={editingAsset?.name || ""} required autoFocus /></label>
                  <label className="field"><span>Tipo</span><select name="kind" defaultValue={editingAsset?.kind || "website"}><option value="website">Website</option><option value="api">API</option><option value="linux">Linux</option><option value="windows">Windows</option><option value="database">Banco de dados</option><option value="network">Rede</option></select></label>
                  <label className="field"><span>Endereço</span><input name="address" defaultValue={editingAsset?.address || ""} placeholder="URL, host ou IP" /></label>
                  <label className="field"><span>Criticidade</span><select name="criticality" defaultValue={editingAsset?.criticality || "medium"}><option value="low">Baixa</option><option value="medium">Média</option><option value="high">Alta</option><option value="critical">Crítica</option></select></label>
                </FormSection>
                <FormActions onCancel={closeAssetForm} submitting={assetSubmitting} submitLabel={editingAsset ? "Salvar ambiente" : "Criar ambiente"} />
              </form>
            </FormPage>
          )}
          {view === "assets" && !showAssetForm && (
            <section className="panel">
              <div className="list-page-heading"><div><h2>Ambientes e ativos</h2><p>Ativos monitorados na Empresa selecionada.</p></div><button className="primary" onClick={() => openAssetForm()}><ActionIcon name="add" /> Novo ambiente</button></div>
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
                    <button onClick={() => openAssetForm(a)}>Editar</button>
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
                  </>
                )}
              />
            </section>
          )}
          {view === "monitors" && showMonitorForm && (
            <FormPage title={editingMonitor ? "Editar serviço" : "Novo serviço"} description={`${tenant?.name || "Empresa selecionada"} · configure o monitoramento e a responsabilidade operacional.`} onBack={closeMonitorForm}>
              <form className="nexora-form" onSubmit={async (e) => {
                e.preventDefault();
                if (monitorSubmitting) return;
                const f = new FormData(e.currentTarget), type = f.get("type");
                setMonitorSubmitting(true);
                try {
                  await api(`/api/v1/${editingMonitor ? `monitors/${editingMonitor.id}` : "monitors"}`, token, {
                    method: editingMonitor ? "PUT" : "POST",
                    body: JSON.stringify({ asset_id: f.get("asset"), name: f.get("name"), monitor_type: type, configuration: type === "http" ? { url: f.get("target") } : { host: f.get("target"), port: Number(f.get("port")) }, interval_seconds: editingMonitor?.interval_seconds || 60, severity: f.get("severity"), active: editingMonitor?.active ?? true, responsible_team_id: f.get("responsible_team") || null, responsible_user_id: f.get("responsible_user") || null, notify_on_failure: f.get("notify_failure") === "on", notify_on_recovery: f.get("notify_recovery") === "on", auto_resolve_incident: f.get("auto_resolve") === "on" }),
                  }, tenantId);
                  await refresh();
                  setNotice({ type: "success", text: editingMonitor ? "Serviço atualizado com sucesso." : "Serviço criado com sucesso." });
                  closeMonitorForm();
                } catch (exception) {
                  captureError(exception);
                  setNotice({ type: "error", text: exception.message });
                } finally {
                  setMonitorSubmitting(false);
                }
              }}>
                <FormSection title="Identificação" description="A Empresa é herdada do contexto global e não pode ser alterada durante o cadastro.">
                  <label className="field"><span>Empresa</span><input value={tenant?.name || ""} readOnly aria-readonly="true" /></label>
                  <label className="field"><span>Ambiente</span><select name="asset" defaultValue={editingMonitor?.asset_id || ""} required><option value="">Selecione</option>{assets.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                  <label className="field"><span>Nome do serviço</span><input name="name" defaultValue={editingMonitor?.name || ""} required autoFocus /></label>
                  <label className="field"><span>Tipo</span><select name="type" defaultValue={editingMonitor?.monitor_type || "http"}><option value="http">HTTP</option><option value="tcp">TCP</option></select></label>
                  <label className="field"><span>URL ou host</span><input name="target" defaultValue={editingMonitor?.configuration?.url || editingMonitor?.configuration?.host || ""} required /></label>
                  <label className="field"><span>Porta</span><input name="port" defaultValue={editingMonitor?.configuration?.port || ""} type="number" min="1" max="65535" /></label>
                  <label className="field"><span>Severidade</span><select name="severity" defaultValue={editingMonitor?.severity || "warning"}><option value="warning">Atenção</option><option value="high">Alta</option><option value="disaster">Crítica</option></select></label>
                </FormSection>
                <FormSection title="Responsabilidade e automação">
                  <label className="field"><span>Equipe responsável</span><select name="responsible_team" defaultValue={editingMonitor?.responsible_team_id || ""}><option value="">Sem equipe</option>{teams.filter((t) => t.active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
                  <label className="field"><span>Técnico específico</span><select name="responsible_user" defaultValue={editingMonitor?.responsible_user_id || ""}><option value="">Sem técnico</option>{users.filter((u) => u.active).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
                  <label className="check-field"><input name="notify_failure" type="checkbox" defaultChecked={editingMonitor?.notify_on_failure ?? true} /> Avisar na falha</label>
                  <label className="check-field"><input name="notify_recovery" type="checkbox" defaultChecked={editingMonitor?.notify_on_recovery ?? true} /> Avisar ao normalizar</label>
                  <label className="check-field"><input name="auto_resolve" type="checkbox" defaultChecked={editingMonitor?.auto_resolve_incident ?? true} /> Encerrar incidente ao normalizar</label>
                </FormSection>
                <FormActions onCancel={closeMonitorForm} submitting={monitorSubmitting} submitLabel={editingMonitor ? "Salvar serviço" : "Criar serviço"} />
              </form>
            </FormPage>
          )}
          {view === "monitors" && !showMonitorForm && (
            <section className="services-page">
              <section className="service-metrics" aria-label="Resumo dos serviços">
                {[
                  ["active", "Serviços ativos", monitors.filter((m) => m.active && m.last_status === "up").length, "settings"],
                  ["failed", "Serviços em falha", monitors.filter((m) => m.active && m.last_status === "down").length, "alert"],
                  ["normalizing", "Sem dados", monitors.filter((m) => m.active && m.last_status === "unknown").length, "alert"],
                  ["inactive", "Inativos", monitors.filter((m) => !m.active).length, "pause"],
                ].map(([kind, label, value, icon]) => (
                  <article key={kind} className={`service-metric service-metric-${kind}`}>
                    <i>{icon === "pause" ? <ActionIcon name="pause" /> : <NavIcon name={icon} />}</i>
                    <div><strong>{value}</strong><b>{label}</b><span>{monitors.length ? `${Math.round((value / monitors.length) * 100)}% do total` : "0% do total"}</span></div>
                  </article>
                ))}
              </section>
              <section className={`service-filters ${monitorFiltersOpen ? "expanded" : ""}`}>
                <div className="service-filters-heading">
                  <h2><ActionIcon name="filter" /> Filtros</h2>
                  <div>
                    <button className="secondary" onClick={() => { setMonitorFilters({ status: "all", search: "", type: "all", target: "", severity: "all", team: "all", user: "all", asset: "all" }); setMonitorPage(1); }}>Limpar filtros</button>
                    <button className="icon-button" aria-label={monitorFiltersOpen ? "Recolher filtros" : "Expandir filtros"} title={monitorFiltersOpen ? "Recolher filtros" : "Expandir filtros"} onClick={() => setMonitorFiltersOpen((value) => !value)}>{monitorFiltersOpen ? "⌃" : "⌄"}</button>
                  </div>
                </div>
                {monitorFiltersOpen && <div className="service-filter-grid">
                  <label><span>Status</span><select value={monitorFilters.status} onChange={(e) => { setMonitorFilters({ ...monitorFilters, status: e.target.value }); setMonitorPage(1); }}><option value="all">Todos</option><option value="active">Ativos</option><option value="failed">Em falha</option><option value="normalizing">Sem dados</option><option value="inactive">Inativos</option></select></label>
                  <label><span>Nome do serviço</span><input value={monitorFilters.search} placeholder="Digite o nome do serviço..." onChange={(e) => { setMonitorFilters({ ...monitorFilters, search: e.target.value }); setMonitorPage(1); }} /></label>
                  <label><span>Tipo</span><select value={monitorFilters.type} onChange={(e) => { setMonitorFilters({ ...monitorFilters, type: e.target.value }); setMonitorPage(1); }}><option value="all">Todos</option>{[...new Set(monitors.map((m) => m.monitor_type))].filter(Boolean).map((type) => <option key={type} value={type}>{type}</option>)}</select></label>
                  <label><span>URL ou host</span><input value={monitorFilters.target} placeholder="Digite o host ou URL..." onChange={(e) => { setMonitorFilters({ ...monitorFilters, target: e.target.value }); setMonitorPage(1); }} /></label>
                  <label><span>Severidade</span><select value={monitorFilters.severity} onChange={(e) => { setMonitorFilters({ ...monitorFilters, severity: e.target.value }); setMonitorPage(1); }}><option value="all">Todas</option><option value="warning">Atenção</option><option value="high">Alta</option><option value="disaster">Crítica</option></select></label>
                  <label><span>Equipe responsável</span><select value={monitorFilters.team} onChange={(e) => { setMonitorFilters({ ...monitorFilters, team: e.target.value }); setMonitorPage(1); }}><option value="all">Todas</option>{teams.filter((t) => t.active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
                  <label><span>Técnico específico</span><select value={monitorFilters.user} onChange={(e) => { setMonitorFilters({ ...monitorFilters, user: e.target.value }); setMonitorPage(1); }}><option value="all">Todos</option>{users.filter((u) => u.active).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
                  <label><span>Ambiente</span><select value={monitorFilters.asset} onChange={(e) => { setMonitorFilters({ ...monitorFilters, asset: e.target.value }); setMonitorPage(1); }}><option value="all">Todos</option>{assets.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                </div>}
              </section>
              <div className="service-toolbar">
                <div>
                  <button className="primary" onClick={() => openMonitorForm()}><ActionIcon name="add" /> Novo serviço</button>
                  <button className="secondary" disabled={selectedMonitorIds.length !== 1} onClick={() => {const item=monitors.find((m)=>m.id===selectedMonitorIds[0]);if(item)openMonitorForm(item);}}><ActionIcon name="edit" /> Editar</button>
                  <button className="secondary" disabled={!selectedMonitorIds.length} onClick={() => setSelectedMonitorsActive(false)}><ActionIcon name="pause" /> Inativar</button>
                  <button className="secondary" disabled={!selectedMonitorIds.length} onClick={() => setSelectedMonitorsActive(true)}><ActionIcon name="play" /> Ativar</button>
                </div>
                <button className="secondary" onClick={() => exportMonitors(filteredMonitors)}><ActionIcon name="download" /> Exportar</button>
              </div>
              <DataGrid showEmptyGrid selectedIds={selectedMonitorIds} onSelectionChange={setSelectedMonitorIds}
                columns={[
                  { key: "name", label: "Serviço", icon: "database" },
                  { key: "active", label: "Ativo", render: (m) => <span className={`service-toggle ${m.active ? "on" : "off"}`} aria-label={m.active ? "Ativo" : "Inativo"}><i /></span> },
                  { key: "monitor_type", label: "Tipo" },
                  { key: "responsible_team_name", label: "Equipe", render: (m) => m.responsible_team_name || "—" },
                  { key: "responsible_user_name", label: "Técnico", render: (m) => m.responsible_user_name || "—" },
                  { key: "created_at", label: "Cadastro", render: (m) => m.created_at ? new Date(m.created_at).toLocaleDateString("pt-BR") : "—" },
                  { key: "last_status", label: "Estado", render: (m) => <Status value={!m.active ? "inativo" : m.last_status === "up" ? "ativo" : m.last_status === "down" ? "down" : "unknown"} /> },
                  { key: "last_checked_at", label: "Última coleta", render: (m) => fmt(m.last_checked_at) },
                ]}
                rows={pagedMonitors}
                stateKey={`${tenantId}:monitors`}
                actions={(m) => <><button onClick={() => setNotice({ type: "success", text: `${m.name}: ${m.last_message || "Sem detalhes da última coleta."}` })}>Visualizar</button><button onClick={() => openMonitorForm(m)}>Editar</button><button onClick={() => submit(`monitors/${m.id}`, monitorBody(m, { active: !m.active }), refresh, "PUT")}>{m.active ? "Inativar" : "Ativar"}</button></>}
              />
              <div className="service-pagination"><span>Mostrando {filteredMonitors.length ? (monitorPage - 1) * monitorPageSize + 1 : 0} a {Math.min(monitorPage * monitorPageSize, filteredMonitors.length)} de {filteredMonitors.length} registros</span><div><label>Registros por página <select value={monitorPageSize} onChange={(e) => { setMonitorPageSize(Number(e.target.value)); setMonitorPage(1); }}><option>10</option><option>25</option><option>50</option></select></label><button disabled={monitorPage === 1} onClick={() => setMonitorPage((page) => page - 1)}>‹</button><b>{monitorPage}</b><button disabled={monitorPage >= monitorPageCount} onClick={() => setMonitorPage((page) => page + 1)}>›</button></div></div>
            </section>
          )}
          {view === "monitors-legacy" && (
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
                      onClick={() => openMonitorForm(m)}
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
                  </>
                )}
              />
            </section>
          )}
          {view === "noc" && <NocDashboard data={noc} loading={nocLoading} onRetry={() => refreshNoc().catch(captureError)} />}
          {view === "databases" && (
            <section className="database-workspace">
              <div className="section-head database-heading">
                <div>
                  <h2>Monitoramento Banco de Dados</h2>
                  <p>
                    Diagnóstico, indicadores, histórico, evolução e desempenho
                    dos bancos de dados da empresa selecionada.
                  </p>
                </div>
              </div>
              {!databaseSources.length ? (
                <section className="panel">
                  <Empty>
                    Esta empresa ainda não possui bancos de dados monitorados. Configure um coletor para que os diagnósticos apareçam aqui.
                  </Empty>
                </section>
              ) : (
                <>
                  <section className="database-summary">
                    {[
                      ["normal", "Normal", benner.checks.filter((item) => ["ok", "normal", "success", "healthy", "up"].includes(item.status)).length],
                      ["attention", "Atenção", benner.checks.filter((item) => ["warning", "attention"].includes(item.status)).length],
                      ["critical", "Crítico", benner.checks.filter((item) => ["critical"].includes(item.status)).length],
                      ["info", "Informativo", benner.checks.filter((item) => ["info"].includes(item.status)).length],
                      ["error", "Erro", benner.checks.filter((item) => ["error", "failed"].includes(item.status)).length],
                    ].map(([kind, label, count]) => <article key={kind} className={`database-summary-${kind}`}><strong>{count}</strong><span>{label}</span></article>)}
                  </section>
                  <section className="panel database-native-panel">
                    <div className="database-panel-head">
                      <div><h3>{selectedDatabaseId === "all" ? "Todos os bancos" : (databaseSources.find((source) => source.asset_id === selectedDatabaseId)?.database_name || "Banco de dados")}</h3><p>{selectedDatabaseId === "all" ? `${databaseSources.length} bancos monitorados` : `Servidor ${databaseSources.find((source) => source.asset_id === selectedDatabaseId)?.server || "—"}`} · Última coleta {fmt(benner.snapshot?.collected_at)}</p></div>
                    </div>
                    <div className="database-tabs" role="tablist" aria-label="Seções do monitoramento">
                      <button role="tab" aria-selected={databaseTab === "diagnostics"} className={databaseTab === "diagnostics" ? "active" : ""} onClick={() => setDatabaseTab("diagnostics")}>Diagnóstico</button>
                      <button role="tab" aria-selected={databaseTab === "dash"} className={databaseTab === "dash" ? "active" : ""} onClick={() => setDatabaseTab("dash")}>Dash</button>
                      <button role="tab" aria-selected={databaseTab === "history"} className={databaseTab === "history" ? "active" : ""} onClick={() => setDatabaseTab("history")}>Histórico e evolução</button>
                      <button role="tab" aria-selected={databaseTab === "performance"} className={databaseTab === "performance" ? "active" : ""} onClick={() => setDatabaseTab("performance")}>Desempenho</button>
                    </div>
                    {databaseTab === "diagnostics" ? <>
                      <div className="database-search"><ActionIcon name="filter" /><input value={databaseSearch} onChange={(e) => setDatabaseSearch(e.target.value)} placeholder="Buscar diagnóstico por nome, categoria ou mensagem..." /></div>
                      <div className="database-check-grid">
                        {benner.checks.filter((item) => `${item.name} ${item.category || ""} ${item.message || ""}`.toLowerCase().includes(databaseSearch.toLowerCase())).map((item) => <article key={item.id}>
                          <div className="database-check-number">{String(item.check_number).padStart(2, "0")}</div>
                          <div><h4>{item.name}</h4><p>{item.message || item.category || "Diagnóstico concluído."}</p><small>{selectedDatabaseId === "all" && item.database_name ? `${item.database_name} · ` : ""}{item.category || "Banco de dados"}{item.duration_ms ? ` · ${item.duration_ms} ms` : ""}</small></div>
                          <Status value={item.status} />
                        </article>)}
                      </div>
                    </> : databaseTab === "dash" ? <div className="database-dash">
                      <section className="database-dash-kpis">
                        <article><span>Diagnósticos</span><strong>{benner.checks.length}</strong><small>na última coleta</small></article>
                        <article><span>Com atenção</span><strong>{benner.checks.filter((item) => !["ok", "normal", "info", "success", "healthy", "up"].includes(item.status)).length}</strong><small>exigem análise</small></article>
                        <article><span>Categorias</span><strong>{new Set(benner.checks.map((item) => item.category).filter(Boolean)).size}</strong><small>áreas avaliadas</small></article>
                        <article><span>Duração da coleta</span><strong>{benner.snapshot?.duration_ms ? `${Math.round(benner.snapshot.duration_ms / 1000)}s` : "—"}</strong><small>{fmt(benner.snapshot?.collected_at)}</small></article>
                      </section>
                      <section className="database-category-panel"><h4>Diagnósticos por categoria</h4><div>{[...new Set(benner.checks.map((item) => item.category || "Outros"))].map((category) => { const items = benner.checks.filter((item) => (item.category || "Outros") === category); const problems = items.filter((item) => !["ok", "normal", "info", "success", "healthy", "up"].includes(item.status)).length; return <article key={category}><div><b>{category}</b><span>{items.length} verificação(ões)</span></div><strong className={problems ? "has-problems" : "healthy"}>{problems ? `${problems} atenção` : "Normal"}</strong></article>; })}</div></section>
                    </div> : databaseTab === "history" ? <DataGrid showEmptyGrid empty="Nenhum histórico disponível para este banco." columns={[
                      { key: "database_name", label: "Banco", render: (item) => item.database_name || databaseSources.find((source) => source.asset_id === selectedDatabaseId)?.database_name || "—" },
                      { key: "collected_at", label: "Coleta", render: (item) => fmt(item.collected_at) },
                      { key: "duration_ms", label: "Duração", render: (item) => item.duration_ms ? `${item.duration_ms} ms` : "—" },
                      { key: "summary", label: "Resumo", render: (item) => { const summary = item.summary || {}; return `${summary.normal ?? summary.ok ?? 0} normal · ${summary.attention ?? summary.warning ?? 0} atenção · ${summary.critical ?? summary.error ?? 0} crítico`; } },
                    ]} rows={benner.history || []} /> : <DatabasePerformance />}
                  </section>
                </>
              )}
            </section>
          )}
          {view === "alerts" && (
            editingAlert ? <FormPage title="Editar alerta" description={`${tenant?.name||"Empresa selecionada"} · ajuste a classificação operacional do alerta.`} onBack={closeAlertForm}><form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(alertSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setAlertSubmitting(true);await run(async()=>{await api(`/api/v1/alerts/${editingAlert.id}`,token,{method:"PUT",body:JSON.stringify({title:f.get("title"),severity:f.get("severity"),status:f.get("status")})},tenantId);await refresh();closeAlertForm();},"Alerta atualizado com sucesso.");setAlertSubmitting(false);}}><FormSection title="Classificação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="title" label="Título" defaultValue={editingAlert.title||""} required/><label className="field"><span>Severidade</span><select name="severity" defaultValue={editingAlert.severity}>{["info","low","medium","high","critical"].map((value)=><option key={value} value={value}>{translated(value)}</option>)}</select></label><label className="field"><span>Estado</span><select name="status" defaultValue={editingAlert.status}><option value="open">Aberto</option><option value="resolved">Resolvido</option><option value="ignored">Ignorado</option></select></label></FormSection><FormActions onCancel={closeAlertForm} submitting={alertSubmitting} submitLabel="Salvar alerta"/></form></FormPage> : <section className="alerts-page">
              <section className={`service-filters ${alertFiltersOpen ? "expanded" : ""}`}>
                <div className="service-filters-heading">
                  <h2><ActionIcon name="filter" /> Filtros</h2>
                  <div>
                    <button className="secondary" onClick={() => { setAlertFilters({ search: "", severity: "all", status: "all", monitor: "all", openedFrom: "", openedTo: "" }); setAlertPage(1); }}>Limpar filtros</button>
                    <button className="icon-button" aria-label={alertFiltersOpen ? "Recolher filtros" : "Expandir filtros"} title={alertFiltersOpen ? "Recolher filtros" : "Expandir filtros"} onClick={() => setAlertFiltersOpen((value) => !value)}>{alertFiltersOpen ? "⌃" : "⌄"}</button>
                  </div>
                </div>
                {alertFiltersOpen && <div className="alert-filter-grid">
                  <label><span>Buscar alerta</span><input value={alertFilters.search} placeholder="Título ou mensagem..." onChange={(e) => { setAlertFilters({ ...alertFilters, search: e.target.value }); setAlertPage(1); }} /></label>
                  <label><span>Severidade</span><select value={alertFilters.severity} onChange={(e) => { setAlertFilters({ ...alertFilters, severity: e.target.value }); setAlertPage(1); }}><option value="all">Todas</option>{[...new Set(alerts.map((a) => a.severity))].filter(Boolean).map((severity) => <option key={severity} value={severity}>{translated(severity)}</option>)}</select></label>
                  <label><span>Estado</span><select value={alertFilters.status} onChange={(e) => { setAlertFilters({ ...alertFilters, status: e.target.value }); setAlertPage(1); }}><option value="all">Todos</option><option value="open">Aberto</option><option value="resolved">Resolvido</option><option value="ignored">Ignorado</option></select></label>
                  <label><span>Serviço</span><select value={alertFilters.monitor} onChange={(e) => { setAlertFilters({ ...alertFilters, monitor: e.target.value }); setAlertPage(1); }}><option value="all">Todos</option>{monitors.map((monitor) => <option key={monitor.id} value={monitor.id}>{monitor.name}</option>)}</select></label>
                  <label><span>Abertura de</span><input type="date" value={alertFilters.openedFrom} onChange={(e) => { setAlertFilters({ ...alertFilters, openedFrom: e.target.value }); setAlertPage(1); }} /></label>
                  <label><span>Abertura até</span><input type="date" value={alertFilters.openedTo} min={alertFilters.openedFrom || undefined} onChange={(e) => { setAlertFilters({ ...alertFilters, openedTo: e.target.value }); setAlertPage(1); }} /></label>
                </div>}
                {Object.entries(alertFilters).some(([key, value]) => key === "search" ? value : value && value !== "all") && <div className="active-filter-summary"><span>{filteredAlerts.length} alerta(s) encontrado(s)</span><button onClick={() => { setAlertFilters({ search: "", severity: "all", status: "all", monitor: "all", openedFrom: "", openedTo: "" }); setAlertPage(1); }}>Limpar tudo</button></div>}
              </section>
              <section className="panel alerts-directory">
              <div className="section-head"><div><h2>Alertas</h2><p>{filteredAlerts.length} registro(s) conforme os filtros aplicados.</p></div></div>
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
                rows={pagedAlerts}
                stateKey={`${tenantId}:alerts`}
                showEmptyGrid
                empty="Nenhum alerta corresponde aos filtros selecionados."
                actions={(a) => (
                  <>
                    <button onClick={() => openAlertForm(a)}>
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
                  </>
                )}
              />
              <div className="service-pagination"><span>Mostrando {filteredAlerts.length ? (alertPage - 1) * alertPageSize + 1 : 0} a {Math.min(alertPage * alertPageSize, filteredAlerts.length)} de {filteredAlerts.length} registros</span><div><label>Registros por página <select value={alertPageSize} onChange={(e) => { setAlertPageSize(Number(e.target.value)); setAlertPage(1); }}><option>10</option><option>25</option><option>50</option></select></label><button disabled={alertPage === 1} onClick={() => setAlertPage((page) => page - 1)}>‹</button><b>{alertPage}</b><button disabled={alertPage >= alertPageCount} onClick={() => setAlertPage((page) => page + 1)}>›</button></div></div>
              </section>
            </section>
          )}
          {view === "queues" && (
            editingQueue ? <FormPage title={editingQueue.id ? "Editar fila de atendimento" : "Nova fila de atendimento"} description={`${tenant?.name||"Empresa selecionada"} · associe a fila a uma equipe responsável.`} onBack={closeQueueForm}>
              <form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(queueSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setQueueSubmitting(true);await run(async()=>{const body={name:f.get("name"),description:f.get("description")||null,team_id:f.get("team_id")||null,active:f.get("active")==="on"};await api(`/api/v1/queues${editingQueue.id?`/${editingQueue.id}`:""}`,token,{method:editingQueue.id?"PUT":"POST",body:JSON.stringify(body)},tenantId);await loadTenantResource("queues",setQueues);closeQueueForm();},editingQueue.id?"Fila atualizada com sucesso.":"Fila criada com sucesso.");setQueueSubmitting(false);}}>
                <FormSection title="Identificação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="name" label="Nome da fila" defaultValue={editingQueue.name||""} required maxLength="160"/><label className="field"><span>Equipe responsável</span><select name="team_id" defaultValue={editingQueue.team_id||""}><option value="">Sem equipe fixa</option>{teams.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="switch"><input name="active" type="checkbox" defaultChecked={editingQueue.active??true}/> Fila ativa</label></FormSection>
                <FormSection title="Finalidade"><label className="field"><span>Descrição e critérios de roteamento</span><textarea name="description" defaultValue={editingQueue.description||""} maxLength="1000"/></label></FormSection>
                <FormActions onCancel={closeQueueForm} submitting={queueSubmitting} submitLabel={editingQueue.id?"Salvar fila":"Criar fila"}/>
              </form>
            </FormPage> : <section className="panel"><div className="list-page-heading"><div><h2>Filas de atendimento</h2><p>Roteamento por Empresa e equipe, com backlog operacional visível.</p></div><button className="primary" onClick={()=>openQueueForm()}>＋ Nova fila</button></div><DataGrid showEmptyGrid rows={queues} columns={[{key:"name",label:"Fila"},{key:"team_name",label:"Equipe",render:(row)=>row.team_name||"Sem equipe fixa"},{key:"description",label:"Finalidade",render:(row)=>row.description||"—"},{key:"backlog",label:"Backlog"},{key:"active",label:"Estado",render:(row)=><Status value={row.active?"ativo":"inativo"}/>}]} actions={(row)=><><button onClick={()=>openQueueForm(row)}>Editar</button><button onClick={()=>submit(`queues/${row.id}`,{name:row.name,description:row.description,team_id:row.team_id,active:!row.active},async()=>loadTenantResource("queues",setQueues),"PUT")}>{row.active?"Inativar":"Ativar"}</button></>}/></section>
          )}
          {view === "my-queue" && (
            <section className="operations-screen">
              <div className="section-head"><div><h2>Minha fila</h2><p>Chamados e tarefas atribuídos a você ou às suas equipes.</p></div><span>{inAppNotifications.unread||0} notificação(ões) nova(s)</span></div>
              <section className="metric-strip"><article><strong>{(myQueue.tickets||[]).length}</strong><span>Chamados ativos</span></article><article><strong>{(myQueue.tasks||[]).length}</strong><span>Tarefas pendentes</span></article><article><strong>{(myQueue.tickets||[]).filter((item)=>item.sla_state==="breached").length}</strong><span>Vencidos</span></article><article><strong>{(myQueue.tickets||[]).filter((item)=>item.priority==="critical").length}</strong><span>Críticos</span></article></section>
              <section className="panel"><h3>Notificações internas</h3><DataGrid showEmptyGrid empty="Nenhuma notificação para você." rows={inAppNotifications.items||[]} columns={[{key:"title",label:"Notificação"},{key:"body",label:"Detalhe"},{key:"created_at",label:"Recebida",render:(row)=>fmt(row.created_at)},{key:"read_at",label:"Estado",render:(row)=><Status value={row.read_at?"read":"new"}/>}]} actions={(row)=><>{row.ticket_id&&<button onClick={()=>loadDetail({id:row.ticket_id},"detail")}>Abrir chamado</button>}{!row.read_at&&<button onClick={()=>run(async()=>{await api(`/api/v1/notifications/${row.id}/read`,token,{method:"POST"},tenantId);await loadTenantResource("notifications",setInAppNotifications);},"Notificação marcada como lida.")}>Marcar como lida</button>}</>}/></section>
              <section className="panel"><h3>Chamados priorizados</h3><DataGrid rows={myQueue.tickets||[]} columns={[{key:"priority",label:"Prioridade",render:(row)=><Status value={row.priority}/>},{key:"title",label:"Chamado"},{key:"queue_name",label:"Fila",render:(row)=>row.queue_name||"—"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"sla_state",label:"SLA",render:(row)=><Status value={row.sla_state}/>},{key:"resolution_due_at",label:"Prazo",render:(row)=>fmt(row.resolution_due_at)}]} /></section>
              <section className="panel"><h3>Minhas tarefas</h3><DataGrid rows={myQueue.tasks||[]} columns={[{key:"ticket_title",label:"Chamado"},{key:"title",label:"Tarefa"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"due_at",label:"Prazo",render:(row)=>fmt(row.due_at)}]} /></section>
            </section>
          )}
          {view === "workload" && (
            <section className="panel"><div className="section-head"><div><h2>Desempenho por técnico</h2><p>Fila, entregas, primeira resposta, solução e esforço confirmado no mês.</p></div></div><DataGrid showEmptyGrid rows={workload.users||[]} columns={[{key:"display_name",label:"Técnico"},{key:"open_tickets",label:"Chamados abertos"},{key:"resolved_tickets_month",label:"Resolvidos no mês"},{key:"avg_first_response_minutes",label:"Resposta média (min)",render:(row)=>row.avg_first_response_minutes??"—"},{key:"avg_resolution_minutes",label:"Solução média (min)",render:(row)=>row.avg_resolution_minutes??"—"},{key:"pending_tasks",label:"Tarefas pendentes"},{key:"confirmed_minutes_month",label:"Minutos confirmados"}]} /></section>
          )}
          {view === "time-entries" && (
            operationalCreateView==="time-entries"?<FormPage title="Novo apontamento" description="O esforço permanece como rascunho até ser confirmado como tarefa no GLPI." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("time-entries",{ticket_id:f.get("ticket_id"),description:f.get("description"),started_at:new Date(f.get("started_at")).toISOString(),minutes:Number(f.get("minutes"))},async()=>{await loadTenantResource("time-entries",setTimeEntries);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Esforço"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Chamado</span><select name="ticket_id" required><option value="">Selecione</option>{incidents.filter((item)=>!["resolved","closed","cancelled","duplicate","ignored"].includes(item.status)).map((item)=><option key={item.id} value={item.id}>{item.title}</option>)}</select></label><Input name="description" label="Atividade realizada" required/><Input name="started_at" label="Início" type="datetime-local" required/><Input name="minutes" label="Minutos" type="number" min="1" max="1440" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Salvar rascunho"/></form></FormPage>:<section className="operations-screen"><section className="panel"><div className="section-head"><div><h2>Apontamentos</h2><p>O esforço permanece como rascunho até ser confirmado como tarefa no GLPI.</p></div><button className="primary" onClick={()=>openOperationalCreate("time-entries")}>＋ Novo apontamento</button></div><DataGrid showEmptyGrid rows={timeEntries} columns={[{key:"ticket_title",label:"Chamado"},{key:"description",label:"Atividade"},{key:"user_name",label:"Técnico"},{key:"minutes",label:"Minutos"},{key:"state",label:"Estado",render:(row)=><Status value={row.state}/>},{key:"started_at",label:"Início",render:(row)=>fmt(row.started_at)}]} actions={(entry)=><>{entry.state==="draft"&&<button onClick={()=>submit(`time-entries/${entry.id}/submit`,{},async()=>loadTenantResource("time-entries",setTimeEntries))}>Enviar ao GLPI</button>}</>} /></section></section>
          )}
          {view === "contracts" && (
            operationalCreateView==="contracts"?<FormPage title={editingContract?"Editar contrato":"Novo contrato"} description={editingContract?"Atualize os dados cadastrais e a vigência do contrato.":"Crie o contrato para posteriormente versionar franquia, tarifas, metas e cobertura."} onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit(editingContract?`contracts/${editingContract.id}`:"contracts",{engagement_id:f.get("engagement_id"),code:f.get("code"),name:f.get("name"),starts_on:f.get("starts_on"),ends_on:f.get("ends_on")||null,...(editingContract?{active:editingContract.status!=="inactive"}:{})},async()=>{await loadTenantResource("contracts",setContracts);closeOperationalCreate();},editingContract?"PUT":"POST");setOperationalSubmitting(false);}}><FormSection title="Contrato"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Relação de serviço</span><select name="engagement_id" defaultValue={editingContract?.engagement_id||""} required><option value="">Selecione</option>{serviceEngagements.filter((item)=>item.active||item.id===editingContract?.engagement_id).map((item)=><option key={item.id} value={item.id}>{item.name} · {item.kind}</option>)}</select></label><Input name="code" label="Código" defaultValue={editingContract?.code||""} required/><Input name="name" label="Nome" defaultValue={editingContract?.name||""} required/><Input name="starts_on" label="Início da vigência" type="date" defaultValue={editingContract?.starts_on||""} required/><Input name="ends_on" label="Fim da vigência" type="date" defaultValue={editingContract?.ends_on||""}/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel={editingContract?"Salvar contrato":"Criar contrato"}/></form></FormPage>:<section className="operations-screen"><section className="panel"><div className="section-head"><div><h2>Contratos</h2><p>Partes, vigência, franquia, tarifas, metas e cobertura versionadas.</p></div><button className="primary" onClick={()=>openOperationalCreate("contracts")}>＋ Novo contrato</button></div><DataGrid showEmptyGrid rows={contracts} columns={[{key:"name",label:"Contrato"},{key:"code",label:"Código"},{key:"engagement_kind",label:"Modelo",render:(row)=>row.engagement_kind||"—"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"version",label:"Versão",render:(row)=>row.version||"Sem versão"},{key:"included_minutes",label:"Franquia",render:(row)=>row.version?`${row.included_minutes} min`:"—"},{key:"excess_rate",label:"Tarifa excedente",render:(row)=>row.excess_rate??"Base ausente"}]} actions={(contract)=><><button onClick={()=>openContractForm(contract)}>Editar</button><button onClick={()=>submit(`contracts/${contract.id}`,{engagement_id:contract.engagement_id,code:contract.code,name:contract.name,starts_on:contract.starts_on,ends_on:contract.ends_on||null,active:contract.status==="inactive"},async()=>loadTenantResource("contracts",setContracts),"PUT")}>{contract.status==="inactive"?"Ativar":"Inativar"}</button>{!contract.version&&<button onClick={()=>submit(`contracts/${contract.id}/versions`,{currency:"BRL",included_minutes:0,rounding_minutes:15,excess_rate:null,internal_cost_rate:null,coverage:{},targets:{},effective_from:contract.starts_on},async()=>loadTenantResource("contracts",setContracts))}>Criar versão</button>}{contract.version_state==="draft"&&<button onClick={()=>submit(`contracts/${contract.id}/versions/${contract.version}/review`,{},async()=>loadTenantResource("contracts",setContracts))}>Revisar</button>}{contract.version_state==="review"&&<button onClick={()=>submit(`contracts/${contract.id}/versions/${contract.version}/publish`,{},async()=>loadTenantResource("contracts",setContracts))}>Publicar</button>}</>} /></section></section>
          )}
          {view === "consumption" && (
            operationalCreateView==="consumption"?<FormPage title="Solicitar revisão comercial" description="A revisão cria um ajuste auditável sem alterar o apontamento técnico original." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit(`consumption/${f.get("entry_id")}/reviews`,{proposed_minutes:Number(f.get("proposed_minutes")),reason:f.get("reason")},async()=>{await Promise.all([loadTenantResource("consumption",setConsumptionData),loadTenantResource("consumption/review-queue",setConsumptionReviews)]);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Revisão"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Apontamento técnico</span><select name="entry_id" required><option value="">Selecione</option>{(consumptionData.items||[]).filter((item)=>item.entry_type==="effort"&&item.commercial_state!=="pending").map((item)=><option key={item.id} value={item.id}>{item.contract_name} · {item.description} · {item.raw_minutes} min</option>)}</select></label><Input name="proposed_minutes" label="Minutos comerciais" type="number" min="0" required/><Input name="reason" label="Justificativa da revisão" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Solicitar revisão"/></form></FormPage>:<section className="operations-screen"><section className="panel"><div className="section-head"><div><h2>Consumo</h2><p>O esforço técnico é preservado. Revisões comerciais geram ajustes referenciados no ledger imutável.</p></div><button className="primary" onClick={()=>openOperationalCreate("consumption")}>＋ Solicitar revisão</button></div><DataGrid showEmptyGrid rows={consumptionData.items||[]} columns={[{key:"competence",label:"Competência"},{key:"contract_name",label:"Contrato"},{key:"description",label:"Descrição"},{key:"entry_type",label:"Tipo",render:(row)=><Status value={row.entry_type}/>},{key:"raw_minutes",label:"Técnico"},{key:"billable_minutes",label:"Comercial"},{key:"commercial_state",label:"Revisão",render:(row)=><Status value={row.commercial_state}/>},{key:"included_minutes",label:"Incluído"},{key:"excess_minutes",label:"Excedente"},{key:"amount",label:"Valor",render:(row)=>row.amount??"Base ausente"}]} /></section><section className="panel"><div className="section-head"><div><h2>Fila de revisão comercial</h2><p>A decisão registra responsável, data e hash do snapshot; o apontamento técnico não é alterado.</p></div></div><DataGrid showEmptyGrid rows={consumptionReviews.items||[]} columns={[{key:"requested_at",label:"Solicitada em",render:(row)=>fmt(row.requested_at)},{key:"contract_name",label:"Contrato"},{key:"description",label:"Descrição"},{key:"technical_minutes",label:"Técnico"},{key:"proposed_minutes",label:"Proposto"},{key:"state",label:"Estado",render:(row)=><Status value={row.state}/>},{key:"reason",label:"Justificativa"}]} actions={(review)=>review.state==="pending"?<><button onClick={()=>submit(`consumption-reviews/${review.id}/decision`,{decision:"approved",note:"Aprovado na fila comercial"},async()=>Promise.all([loadTenantResource("consumption",setConsumptionData),loadTenantResource("consumption/review-queue",setConsumptionReviews)]))}>Aprovar</button><button className="danger" onClick={()=>submit(`consumption-reviews/${review.id}/decision`,{decision:"rejected",note:"Rejeitado na fila comercial"},async()=>loadTenantResource("consumption/review-queue",setConsumptionReviews))}>Rejeitar</button></>:null} /></section></section>
          )}
          {view === "billing" && operationalCreateView==="billing" && (
            <FormPage title="Nova competência de pré-faturamento" description="Gere o rascunho da competência para um contrato ativo da Empresa selecionada." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit(`billing-cycles/${f.get("contract_id")}/${f.get("competence")}`,{},async()=>{await loadTenantResource("billing-cycles",setBillingCycles);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Competência"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Contrato ativo</span><select name="contract_id" required><option value="">Selecione</option>{contracts.filter((item)=>item.status==="active"||item.version_state==="published").map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{!contracts.some((item)=>item.status==="active"||item.version_state==="published")&&<p className="field-help">Nenhum contrato com versão publicada está disponível para pré-faturamento.</p>}<Input name="competence" label="Competência" type="date" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Gerar competência"/></form></FormPage>
          )}
          {view === "billing" && operationalCreateView!=="billing" && billingConfirmationRequested && confirmingBillingCycle && (
            <FormPage title="Confirmar retorno do ERP" description="Registre a referência devolvida pelo ERP para concluir a integração deste ciclo." onBack={closeBillingConfirmation}>
              <form className="nexora-form" onSubmit={async (event) => {
                event.preventDefault();
                if (billingSubmitting || !tenantId) return;
                const form = new FormData(event.currentTarget);
                setBillingSubmitting(true);
                await run(async () => {
                  await api(`/api/v1/billing-cycles/${confirmingBillingCycle.id}/erp-confirm`, token, { method: "POST", body: JSON.stringify({ reference: String(form.get("reference") || "").trim() }) }, tenantId);
                  await loadTenantResource("billing-cycles", setBillingCycles);
                  closeBillingConfirmation();
                }, "Retorno do ERP confirmado com sucesso.");
                setBillingSubmitting(false);
              }}>
                <FormSection title="Ciclo exportado" description="Confira os dados imutáveis da revisão enviada antes de registrar o retorno.">
                  <Input label="Empresa" value={tenant?.name || "Empresa selecionada"} readOnly />
                  <Input label="Contrato" value={confirmingBillingCycle.contract_name || "—"} readOnly />
                  <Input label="Competência" value={confirmingBillingCycle.competence || "—"} readOnly />
                  <Input label="Revisão" value={`R${confirmingBillingCycle.revision || 1}`} readOnly />
                  <Input label="Chave externa" value={confirmingBillingCycle.external_key || "—"} readOnly />
                  <Input label="Valor" value={confirmingBillingCycle.total_amount ?? "Base ausente"} readOnly />
                </FormSection>
                <FormSection title="Retorno do ERP" description="Informe a referência única gerada pelo ERP para manter a rastreabilidade ponta a ponta.">
                  <Input label="Referência do ERP" name="reference" required maxLength="160" autoFocus placeholder="Ex.: ERP-2026-10-000123" />
                </FormSection>
                <FormActions onCancel={closeBillingConfirmation} submitting={billingSubmitting} submitLabel="Confirmar retorno" />
              </form>
            </FormPage>
          )}
          {view === "billing" && operationalCreateView!=="billing" && billingConfirmationRequested && !confirmingBillingCycle && (
            <section className="panel"><Empty>Ciclo de pré-faturamento não encontrado ou indisponível para a Empresa selecionada.</Empty><div className="form-actions"><button className="secondary" onClick={closeBillingConfirmation}>Voltar ao pré-faturamento</button></div></section>
          )}
          {view === "billing" && operationalCreateView!=="billing" && !billingConfirmationRequested && (
            <section className="operations-screen"><section className="panel"><div className="section-head"><div><h2>Pré-faturamento</h2><p>Rascunho, revisão, aprovação, exportação, confirmação ERP e recebimento são estados independentes.</p></div><button className="primary" onClick={()=>openOperationalCreate("billing")}>＋ Nova competência</button></div>
              <DataGrid showEmptyGrid rows={billingCycles} columns={[{key:"competence",label:"Competência"},{key:"revision",label:"Revisão",render:(row)=>`R${row.revision||1}`},{key:"contract_name",label:"Contrato"},{key:"external_key",label:"Chave externa"},{key:"state",label:"Estado",render:(row)=><Status value={row.state}/>},{key:"total_minutes",label:"Minutos"},{key:"excess_minutes",label:"Excedente"},{key:"total_amount",label:"Valor",render:(row)=>row.total_amount??"Base ausente"}]} actions={(cycle)=><>{cycle.state==="draft"&&<button onClick={()=>submit(`billing-cycles/${cycle.id}/review`,{},async()=>loadTenantResource("billing-cycles",setBillingCycles))}>Revisar</button>}{cycle.state==="review"&&<button onClick={()=>submit(`billing-cycles/${cycle.id}/approve`,{},async()=>loadTenantResource("billing-cycles",setBillingCycles))}>Aprovar</button>}{cycle.state==="approved"&&<button onClick={()=>downloadBilling(cycle)}>Exportar</button>}{cycle.state==="exported"&&<button onClick={()=>openBillingConfirmation(cycle)}>Confirmar ERP</button>}{cycle.state==="erp_confirmed"&&<button onClick={()=>submit(`billing-cycles/${cycle.id}/receive`,{},async()=>loadTenantResource("billing-cycles",setBillingCycles))}>Registrar recebimento</button>}</>} />
            </section></section>
          )}
          {view === "quality" && (
            <section className="operations-screen"><div className="section-head"><div><h2>Qualidade</h2><p>Indicadores com definição, fonte, amostra e corte explícitos.</p></div></div><section className="metric-strip"><article><strong>{qualityData.metrics?.first_response_sla_pct??"—"}</strong><span>Primeira resposta no SLA (%)</span></article><article><strong>{qualityData.metrics?.resolution_sla_pct??"—"}</strong><span>Solução no SLA (%)</span></article><article><strong>{qualityData.metrics?.reopened??"—"}</strong><span>Reaberturas</span></article><article><strong>{qualityData.csat?.average??"—"}</strong><span>CSAT ({qualityData.csat?.sample||0} respostas)</span></article></section><section className="panel"><p><b>Fonte:</b> {qualityData.source}</p><p><b>Definição:</b> {qualityData.definition}</p><p><b>Corte:</b> {fmt(qualityData.cutoff)}</p><p><b>Amostra:</b> {qualityData.metrics?.sample??0} chamados</p></section></section>
          )}
          {view === "profitability" && (
            <section className="panel"><div className="section-head"><div><h2>Rentabilidade</h2><p>Disponível somente para perfis com permissão específica; ausência de base não é convertida em zero.</p></div></div><DataGrid showEmptyGrid rows={profitabilityData.items||[]} columns={[{key:"competence",label:"Competência"},{key:"name",label:"Contrato"},{key:"revenue",label:"Receita",render:(row)=>row.revenue??"Base ausente"},{key:"cost",label:"Custo",render:(row)=>row.cost??"Base ausente"},{key:"margin",label:"Margem",render:(row)=>row.margin??"Base insuficiente"},{key:"sample",label:"Linhas"}]} /></section>
          )}
          {view === "portal" && (
            <section className="portal-screen">
              <div className="section-head"><div><h2>Portal de serviços</h2><p>Solicite serviços e acompanhe somente suas solicitações.</p></div></div>
              <section className="portal-service-grid">
                {(portalData.services || []).map((service) => <article className="panel portal-service-card" key={service.id}>
                  <small>{service.category}</small><h3>{service.name}</h3><p>{service.description}</p><span>Versão {service.version}</span>
                  <button className="primary" onClick={() => setRequestingService(service)}>Solicitar serviço</button>
                </article>)}
                {!(portalData.services || []).length && <Empty>Nenhum serviço elegível publicado.</Empty>}
              </section>
              {requestingService && <section className="panel portal-request-panel"><div className="section-head"><div><h3>{requestingService.name}</h3><p>Formulário versionado {requestingService.version}</p></div><button className="secondary" onClick={() => setRequestingService(null)}>Fechar</button></div>
                <form className="catalog-form" onSubmit={(e) => { e.preventDefault(); const f=new FormData(e.currentTarget), answers={}; (requestingService.form_schema?.fields || []).forEach((field) => { const values=f.getAll(field.code); answers[field.code]=field.type === "multi_select" ? values : field.type === "number" ? Number(values[0]) : values[0]; }); submit(`portal/services/${requestingService.id}/request`, { answers }, async () => { setRequestingService(null); await loadTenantResource("portal", setPortalData); }); }}>
                  {(requestingService.form_schema?.fields || []).map((field) => <label className="field" key={field.code}><span>{field.label || field.code}{field.required ? " *" : ""}</span>{field.type === "long_text" ? <textarea name={field.code} required={field.required} /> : field.type === "select" ? <select name={field.code} required={field.required}><option value="">Selecione</option>{(field.options || []).map((option) => { const value=typeof option === "object" ? option.value : option; return <option key={value} value={value}>{typeof option === "object" ? option.label || value : value}</option>; })}</select> : field.type === "multi_select" ? <select name={field.code} multiple required={field.required}>{(field.options || []).map((option) => { const value=typeof option === "object" ? option.value : option; return <option key={value} value={value}>{typeof option === "object" ? option.label || value : value}</option>; })}</select> : <input name={field.code} type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"} required={field.required} />}</label>)}
                  <div className="service-create-actions"><button type="button" className="secondary" onClick={() => setRequestingService(null)}>Cancelar</button><button className="primary">Enviar solicitação</button></div>
                </form>
              </section>}
              <section className="panel"><h3>Minhas solicitações recentes</h3><DataGrid rows={portalData.recent_tickets || []} columns={[{key:"title",label:"Título"},{key:"ticket_type",label:"Tipo",render:(row)=>translated(row.ticket_type)},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"sync_status",label:"GLPI",render:(row)=><Status value={row.sync_status}/>},{key:"updated_at",label:"Atualização",render:(row)=>fmt(row.updated_at)}]} /></section>
            </section>
          )}
          {view === "portal-requests" && <section className="portal-screen"><div className="section-head"><div><h2>Minhas solicitações</h2><p>Acompanhe a confirmação no GLPI, responda publicamente e aceite ou rejeite soluções.</p></div></div>{!portalTicketDetail?<section className="panel"><DataGrid showEmptyGrid rows={portalTickets} columns={[{key:"title",label:"Solicitação"},{key:"ticket_type",label:"Tipo",render:(row)=>translated(row.ticket_type)},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"sync_status",label:"GLPI",render:(row)=><Status value={row.sync_status}/>},{key:"updated_at",label:"Atualização",render:(row)=>fmt(row.updated_at)}]} actions={(row)=><button onClick={()=>openPortalTicket(row)}>Abrir</button>}/></section>:<section className="panel"><div className="section-head"><div><button className="back-button" onClick={closePortalTicket}>← Voltar</button><h3>{portalTicketDetail.ticket.title}</h3><p><Status value={portalTicketDetail.ticket.status}/> · GLPI {portalTicketDetail.ticket.glpi_ticket_id?`#${portalTicketDetail.ticket.glpi_ticket_id}`:"pendente"}</p></div></div><p>{portalTicketDetail.ticket.description}</p><h3>Conversas públicas</h3>{(portalTicketDetail.entries||[]).map((entry)=><article className="timeline" key={entry.id}><small>{fmt(entry.created_at)}</small><p>{entry.body}</p></article>)}<form className="form-row" onSubmit={(e)=>{e.preventDefault();const f=new FormData(e.currentTarget);submit(`portal/tickets/${portalTicketDetail.ticket.id}/entries`,{body:f.get("body")},async()=>{e.currentTarget.reset();setPortalTicketDetail(await api(`/api/v1/portal/tickets/${portalTicketDetail.ticket.id}`,token,{},tenantId));});}}><input name="body" placeholder="Responder publicamente" required/><button className="primary">Enviar</button></form><h3>Soluções propostas</h3>{(portalTicketDetail.solutions||[]).map((solution)=><article className="timeline" key={solution.id}><b>{translated(solution.solution_type)}</b><p>{solution.description}</p><Status value={solution.status}/>{solution.status==="proposed"&&<div className="service-create-actions"><button onClick={()=>submit(`tickets/${portalTicketDetail.ticket.id}/solutions/${solution.id}/decision`,{decision:"accepted",reason:"Aceita pelo solicitante"},async()=>setPortalTicketDetail(await api(`/api/v1/portal/tickets/${portalTicketDetail.ticket.id}`,token,{},tenantId)))}>Aceitar</button><button className="danger" onClick={()=>submit(`tickets/${portalTicketDetail.ticket.id}/solutions/${solution.id}/decision`,{decision:"rejected",reason:"Rejeitada pelo solicitante"},async()=>setPortalTicketDetail(await api(`/api/v1/portal/tickets/${portalTicketDetail.ticket.id}`,token,{},tenantId)))}>Rejeitar</button></div>}</article>)}</section>}</section>}
          {view === "catalog" && (
            catalogCreating ? <FormPage title="Novo serviço de catálogo" description={`${tenant?.name||"Empresa selecionada"} · crie a identidade inicial do serviço.`} onBack={closeCatalogForm}><form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(catalogSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setCatalogSubmitting(true);await run(async()=>{await api("/api/v1/catalog/services",token,{method:"POST",body:JSON.stringify({code:f.get("code"),name:f.get("name"),description:f.get("description"),category:f.get("category")||"Geral"})},tenantId);await api("/api/v1/catalog/services?include_drafts=true",token,{},tenantId).then(setCatalogServices);closeCatalogForm();},"Serviço de catálogo criado com sucesso.");setCatalogSubmitting(false);}}><FormSection title="Identificação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="code" label="Código" required/><Input name="name" label="Nome do serviço" required/><Input name="category" label="Categoria" defaultValue="Geral"/><label className="field"><span>Descrição</span><textarea name="description" required/></label></FormSection><FormActions onCancel={closeCatalogForm} submitting={catalogSubmitting} submitLabel="Criar serviço"/></form></FormPage> : editingCatalog ? <FormPage title={`Nova versão · ${editingCatalog.name}`} description="Elegibilidade, formulário, fila, SLA, aprovação e cobertura serão congelados na publicação." onBack={closeCatalogForm}><form className="catalog-form panel" onSubmit={async(e)=>{e.preventDefault();if(catalogSubmitting)return;const f=new FormData(e.currentTarget);const roles=f.getAll("roles");const fields=catalogFields.map((field)=>({...field,required:Boolean(field.required),options:["select","multi_select"].includes(field.type)?String(field.optionsText||"").split(",").map((value)=>value.trim()).filter(Boolean):undefined}));setCatalogSubmitting(true);await submit(`catalog/services/${editingCatalog.id}/versions`,{description:f.get("description"),form_schema:{fields},eligibility:{roles},routing:{team_id:f.get("team_id")||null},approval_policy:{required:f.get("approval_required")==="on"},coverage:{summary:f.get("coverage")||""},sla_policy_id:f.get("sla_policy_id")||null},async()=>{await api("/api/v1/catalog/services?include_drafts=true",token,{},tenantId).then(setCatalogServices);closeCatalogForm();});setCatalogSubmitting(false);}}>
                <div className="section-head"><div><h3>Nova versão · {editingCatalog.name}</h3><p>Elegibilidade, formulário, fila, SLA, aprovação e cobertura serão congelados na publicação.</p></div><button type="button" className="secondary" onClick={()=>{setEditingCatalog(null);setCatalogFields([]);}}>Cancelar</button></div>
                <label className="field"><span>Descrição da versão</span><textarea name="description" defaultValue={editingCatalog.description} required/></label>
                <fieldset><legend>Papéis elegíveis</legend>{["client","viewer","operator","manager","admin"].map((role)=><label key={role}><input type="checkbox" name="roles" value={role} defaultChecked/> {translated(role)}</label>)}</fieldset>
                <div className="form-row"><select name="team_id"><option value="">Fila por roteamento padrão</option>{teams.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select><select name="sla_policy_id"><option value="">SLA não definido</option>{slas.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select><input name="coverage" placeholder="Cobertura e limites"/><label><input type="checkbox" name="approval_required"/> Exigir aprovação</label></div>
                <div className="section-head"><h3>Campos do formulário</h3><button type="button" onClick={()=>setCatalogFields((items)=>[...items,{code:`campo_${items.length+1}`,label:"Novo campo",type:"text",required:false,optionsText:""}])}>Adicionar campo</button></div>
                {catalogFields.map((field,index)=><div className="form-row" key={`${field.code}-${index}`}><input value={field.code} onChange={(e)=>setCatalogFields((items)=>items.map((item,i)=>i===index?{...item,code:e.target.value}:item))} placeholder="Código" required/><input value={field.label} onChange={(e)=>setCatalogFields((items)=>items.map((item,i)=>i===index?{...item,label:e.target.value}:item))} placeholder="Rótulo" required/><select value={field.type} onChange={(e)=>setCatalogFields((items)=>items.map((item,i)=>i===index?{...item,type:e.target.value}:item))}>{["text","long_text","number","date","select","multi_select","attachment"].map((type)=><option key={type} value={type}>{type}</option>)}</select>{["select","multi_select"].includes(field.type)&&<input value={field.optionsText||""} onChange={(e)=>setCatalogFields((items)=>items.map((item,i)=>i===index?{...item,optionsText:e.target.value}:item))} placeholder="Opções separadas por vírgula"/>}<label><input type="checkbox" checked={Boolean(field.required)} onChange={(e)=>setCatalogFields((items)=>items.map((item,i)=>i===index?{...item,required:e.target.checked}:item))}/> Obrigatório</label><button type="button" className="danger" onClick={()=>setCatalogFields((items)=>items.filter((_,i)=>i!==index))}>Remover</button></div>)}
                <div className="service-create-actions"><button className="primary">Salvar nova versão</button></div>
              </form></FormPage> : <section className="panel catalog-screen"><div className="section-head"><div><h2>Catálogo de serviços</h2><p>Serviços, versões, elegibilidade, roteamento, aprovação e cobertura.</p></div><button className="primary" onClick={openCatalogCreate}>＋ Novo serviço</button></div>
              <DataGrid showEmptyGrid rows={catalogServices} columns={[{key:"name",label:"Serviço"},{key:"code",label:"Código"},{key:"category",label:"Categoria"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"version",label:"Versão",render:(row)=>row.version || "Sem versão"},{key:"version_state",label:"Publicação",render:(row)=><Status value={row.version_state || "draft"}/>}]} actions={(service)=><><button onClick={()=>openCatalogVersion(service)}>Nova versão</button>{service.version_state === "draft" && <button onClick={() => submit(`catalog/services/${service.id}/versions/${service.version}/review`,{},async()=>api("/api/v1/catalog/services?include_drafts=true",token,{},tenantId).then(setCatalogServices))}>Enviar para revisão</button>}{service.version_state === "review" && <button onClick={() => submit(`catalog/services/${service.id}/versions/${service.version}/publish`,{},async()=>api("/api/v1/catalog/services?include_drafts=true",token,{},tenantId).then(setCatalogServices))}>Publicar</button>}</>} />
            </section>
          )}
          {view === "incidents" && operationalCreateView==="ticket" && (
            <FormPage title="Novo chamado" description="Registre a intenção local; o número do GLPI só aparece após confirmação remota." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("tickets",{title:f.get("title"),description:f.get("description"),ticket_type:f.get("ticket_type"),priority:f.get("priority"),category:f.get("category")||"general",assignee_team_id:f.get("assignee_team_id")||null,assignee_user_id:f.get("assignee_user_id")||null,sla_policy_id:f.get("sla_policy_id")||null},async()=>{await refresh();closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Solicitação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="title" label="Título" required/><label className="field"><span>Tipo</span><select name="ticket_type"><option value="incident">Incidente</option><option value="request">Solicitação</option></select></label><label className="field"><span>Prioridade</span><select name="priority"><option value="low">Baixa</option><option value="medium">Média</option><option value="high">Alta</option><option value="critical">Crítica</option></select></label><Input name="category" label="Categoria" defaultValue="general"/><label className="field"><span>Equipe responsável</span><select name="assignee_team_id"><option value="">Definir depois</option>{teams.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>Técnico responsável</span><select name="assignee_user_id"><option value="">Definir depois</option>{users.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.display_name||item.email}</option>)}</select></label><label className="field"><span>Política de SLA</span><select name="sla_policy_id"><option value="">Automática pela prioridade</option>{slas.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>Descrição e impacto</span><textarea name="description" rows="6" required/></label></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Registrar intenção"/></form></FormPage>
          )}
          {view === "incidents" && operationalCreateView==="service-engagement" && (
            <FormPage title="Nova relação de serviço" description="Defina se o atendimento é interno ou prestado como serviço gerenciado." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget);setOperationalSubmitting(true);await submit("service-engagements",{operator_company_id:f.get("operator_company_id"),kind:f.get("kind"),name:f.get("name"),active:true},async()=>{await loadTenantResource("service-engagements",setServiceEngagements);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Relação"><Input label="Empresa atendida" value={tenant?.name||""} readOnly/><label className="field"><span>Empresa operadora</span><select name="operator_company_id" required><option value="">Selecione</option>{tenants.map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>Modelo</span><select name="kind"><option value="internal">Interno</option><option value="managed_service">Serviço gerenciado</option></select></label><Input name="name" label="Nome da relação" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Criar relação"/></form></FormPage>
          )}
          {view === "incidents" && operationalCreateView==="access-grant" && (
            <FormPage title="Nova concessão de acesso" description="Conceda papel, escopo e validade sem transformar o usuário em Master." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget),valid=f.get("valid_until"),row_scope=f.get("row_scope");setOperationalSubmitting(true);await submit("access-grants",{user_id:f.get("user_id"),role:f.get("role"),row_scope,engagement_id:row_scope==="team_service"?(f.get("engagement_id")||null):null,team_id:row_scope==="team_service"?(f.get("team_id")||null):null,department_id:row_scope==="department"?(f.get("department_id")||null):null,valid_until:valid?new Date(valid).toISOString():null,reason:f.get("reason")},async()=>{await Promise.all([loadTenantResource("access-grants",setAccessGrants),loadTenantResource("users",setUsers)]);closeOperationalCreate();});setOperationalSubmitting(false);}}><FormSection title="Concessão"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Usuário</span><select name="user_id" required><option value="">Selecione</option>{users.filter((item)=>item.active&&!item.is_master).map((item)=><option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label><label className="field"><span>Papel</span><select name="role"><option value="viewer">Leitura</option><option value="client">Cliente</option><option value="operator">Operador</option><option value="manager">Gestor</option><option value="auditor">Auditor</option><option value="admin">Administrador</option></select></label><label className="field"><span>Escopo</span><select name="row_scope"><option value="company">Empresa</option><option value="team_service">Equipe/serviço</option><option value="department">Departamento</option><option value="own">Próprios registros</option></select></label><label className="field"><span>Departamento (para escopo Departamento)</span><select name="department_id"><option value="">Selecione</option>{departments.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>Equipe (para escopo Equipe/serviço)</span><select name="team_id"><option value="">Selecione</option>{teams.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>Relação de serviço (para escopo Equipe/serviço)</span><select name="engagement_id"><option value="">Selecione</option>{serviceEngagements.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><Input name="valid_until" label="Validade" type="datetime-local"/><Input name="reason" label="Motivo" defaultValue="Acesso concedido pela administração" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Conceder acesso"/></form></FormPage>
          )}
          {view === "incidents" && completingIncident && (
            <FormPage title="Concluir chamado" description="Registre a solução aplicada antes de finalizar o atendimento." onBack={()=>setCompletingIncident(null)}><form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(operationalSubmitting)return;const f=new FormData(event.currentTarget);setOperationalSubmitting(true);await run(async()=>{await api(`/api/v1/tickets/${completingIncident.id}/solutions`,token,{method:"POST",body:JSON.stringify({solution_type:f.get("solution_type"),description:f.get("description"),evidence:f.get("evidence")||null})},tenantId);await api(`/api/v1/tickets/${completingIncident.id}/transitions`,token,{method:"POST",body:JSON.stringify({target_status:"resolved",reason:f.get("reason")})},tenantId);await refresh();setCompletingIncident(null);},"Chamado concluído e encaminhado para sincronização com o GLPI.");setOperationalSubmitting(false);}}><FormSection title="Finalização"><Input label="Chamado" value={completingIncident.title} readOnly/><label className="field"><span>Tipo de solução</span><select name="solution_type"><option value="permanent">Definitiva</option><option value="workaround">Contorno</option><option value="not_reproduced">Não reproduzido</option><option value="cancelled">Cancelamento</option></select></label><label className="field"><span>Solução aplicada</span><textarea name="description" rows="5" required/></label><label className="field"><span>Evidências</span><textarea name="evidence" rows="3"/></label><Input name="reason" label="Motivo da conclusão" defaultValue="Atendimento concluído com solução registrada" required/></FormSection><FormActions onCancel={()=>setCompletingIncident(null)} submitting={operationalSubmitting} submitLabel="Concluir chamado"/></form></FormPage>
          )}
          {view === "incidents" && !completingIncident && !["ticket","service-engagement","access-grant"].includes(operationalCreateView) && (
            <div className="incident-screen">
              {!detail ? (
              <section className="panel">
                <div className="section-head">
                  <div><h2>Chamados</h2><p>Incidentes e solicitações conciliados com o GLPI.</p></div>
                  <div className="service-create-actions"><button className="secondary" onClick={()=>run(downloadTickets,"Exportação conciliada gerada.")}>Exportar CSV</button><button className="primary" onClick={()=>openOperationalCreate("ticket")}><ActionIcon name="add" /> Novo chamado</button></div>
                </div>
                <MetricGroup items={[
                  { label: "Abertos", value: incidents.filter((item) => !["closed", "resolved", "ignored"].includes(item.status)).length },
                  { label: "Críticos", value: incidents.filter((item) => item.priority === "critical" && !["closed", "resolved", "ignored"].includes(item.status)).length },
                  { label: "SLA vencido", value: incidents.filter((item) => item.sla_status === "breached").length },
                  { label: "Sincronização pendente", value: incidents.filter((item) => ["pending", "failed"].includes(item.sync_status)).length },
                ]} />
                <section className="incident-filters" aria-label="Filtros de incidentes">
                  <div className="incident-filters-heading">
                    <strong><ActionIcon name="filter" /> Filtros</strong>
                    <button className="secondary" onClick={() => { setIncidentFilters({ search: "", priority: "all", status: "all", sla: "all", glpi: "all", team: "all", assignee: "all", mine: false, actionable: true }); setIncidentPage(1); }}>Limpar filtros</button>
                  </div>
                  <label className="field incident-search-field">
                    <span>Pesquisar incidente</span>
                    <input value={incidentFilters.search} onChange={(e) => { setIncidentFilters((current) => ({ ...current, search: e.target.value })); setIncidentPage(1); }} placeholder="Título, chamado, equipe ou técnico..." />
                  </label>
                  <label className="field">
                    <span>Prioridade</span>
                    <select value={incidentFilters.priority} onChange={(e) => { setIncidentFilters((current) => ({ ...current, priority: e.target.value })); setIncidentPage(1); }}>
                      <option value="all">Todas</option>
                      {['low', 'medium', 'high', 'critical'].map((value) => <option key={value} value={value}>{translated(value)}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>Estado</span>
                    <select value={incidentFilters.status} onChange={(e) => { setIncidentFilters((current) => ({ ...current, status: e.target.value, actionable: e.target.value === "all" ? current.actionable : false })); setIncidentPage(1); }}>
                      <option value="all">Todos</option>
                      {['new', 'acknowledged', 'in_progress', 'waiting_customer', 'waiting_supplier', 'resolved', 'closed', 'ignored'].map((value) => <option key={value} value={value}>{translated(value)}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>SLA</span>
                    <select value={incidentFilters.sla} onChange={(e) => { setIncidentFilters((current) => ({ ...current, sla: e.target.value })); setIncidentPage(1); }}>
                      <option value="all">Todos</option>
                      {['on_track', 'at_risk', 'breached'].map((value) => <option key={value} value={value}>{translated(value)}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>Sincronização GLPI</span>
                    <select value={incidentFilters.glpi} onChange={(e) => { setIncidentFilters((current) => ({ ...current, glpi: e.target.value })); setIncidentPage(1); }}>
                      <option value="all">Todas</option>
                      {['synced', 'pending', 'failed'].map((value) => <option key={value} value={value}>{translated(value)}</option>)}
                    </select>
                  </label>
                  <label className="field"><span>Equipe</span><select value={incidentFilters.team} onChange={(e)=>{setIncidentFilters((current)=>({...current,team:e.target.value}));setIncidentPage(1);}}><option value="all">Todas</option>{teams.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
                  <label className="field"><span>Técnico</span><select value={incidentFilters.assignee} onChange={(e)=>{setIncidentFilters((current)=>({...current,assignee:e.target.value}));setIncidentPage(1);}}><option value="all">Todos</option>{users.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.display_name||item.email}</option>)}</select></label>
                  <label className="field checkbox-field"><span>Minha fila</span><input type="checkbox" checked={incidentFilters.mine} onChange={(e)=>{setIncidentFilters((current)=>({...current,mine:e.target.checked}));setIncidentPage(1);}} /></label>
                  <label className="field checkbox-field"><span>Pendentes de tratamento</span><input type="checkbox" checked={incidentFilters.actionable !== false} onChange={(e)=>{setIncidentFilters((current)=>({...current,actionable:e.target.checked,status:e.target.checked?"all":current.status}));setIncidentPage(1);}} /></label>
                </section>
                <p className="filter-result-count">{filteredIncidents.length} de {incidents.length} incidente(s)</p>
                <DataGrid
                  columns={incidentCols}
                  rows={pagedIncidents}
                  stateKey={`${tenantId}:incidents`}
                  onRow={(i) => loadDetail(i, "detail")}
                  actions={(i) => (
                    <>
                      <button onClick={() => loadDetail(i, "detail")}>Detalhes</button>
                      <button onClick={() => loadDetail(i, "edit")}>Editar</button>
                      <button onClick={() => claimIncident(i)} disabled={["resolved","closed","cancelled","duplicate","ignored"].includes(i.status)}>
                        Atender
                      </button>
                      <button onClick={() => openIncidentCompletion(i)} disabled={!['acknowledged','in_progress','waiting_customer','waiting_supplier'].includes(i.status)}>
                        Concluir
                      </button>
                    </>
                  )}
                />
                <div className="service-pagination"><span>Mostrando {filteredIncidents.length ? (incidentPage - 1) * incidentPageSize + 1 : 0} a {Math.min(incidentPage * incidentPageSize, filteredIncidents.length)} de {filteredIncidents.length} registros</span><div><label>Registros por página <select value={incidentPageSize} onChange={(e) => { setIncidentPageSize(Number(e.target.value)); setIncidentPage(1); }}><option>10</option><option>25</option><option>50</option></select></label><button aria-label="Página anterior de chamados" disabled={incidentPage === 1} onClick={() => setIncidentPage((page) => page - 1)}>‹</button><b>{incidentPage}</b><button aria-label="Próxima página de chamados" disabled={incidentPage >= incidentPageCount} onClick={() => setIncidentPage((page) => page + 1)}>›</button></div></div>
                <section className="form-section"><div className="section-head"><div><h3>Relações de serviço</h3><p>Defina se o atendimento é interno ou prestado como serviço gerenciado.</p></div><button className="primary" onClick={()=>openOperationalCreate("service-engagement")}>＋ Nova relação</button></div>
                  <DataGrid showEmptyGrid rows={serviceEngagements} columns={[{key:"name",label:"Relação"},{key:"kind",label:"Modelo",render:(row)=><Status value={row.kind}/>},{key:"operator_company_name",label:"Operadora"},{key:"active",label:"Estado",render:(row)=><Status value={row.active?"ativo":"inativo"}/>}]} />
                </section>
                <section className="form-section"><div className="section-head"><div><h3>Concessões detalhadas</h3><p>Conceda papel, escopo de linha, relação de serviço e validade sem transformar o usuário em Master.</p></div><button className="primary" onClick={()=>openOperationalCreate("access-grant")}>＋ Nova concessão</button></div>
                  <DataGrid showEmptyGrid rows={accessGrants} columns={[{key:"display_name",label:"Usuário"},{key:"role",label:"Papel",render:(row)=><Status value={row.role}/>},{key:"row_scope",label:"Escopo"},{key:"scope_target",label:"Alvo",render:(row)=>row.department_name||row.team_name||row.engagement_name||"Toda a empresa"},{key:"valid_until",label:"Validade",render:(row)=>row.valid_until?fmt(row.valid_until):"Sem expiração"}]} actions={(grant)=><button className="danger" onClick={()=>remove("access-grants",grant.id,grant.display_name,async()=>Promise.all([loadTenantResource("access-grants",setAccessGrants),loadTenantResource("users",setUsers)]))}>Revogar</button>} />
                </section>
              </section>
              ) : (
                <section className="panel incident-detail-page">
                  <div className="section-head">
                    <div>
                      <button className="back-button" onClick={closeIncidentDetail}>← Voltar para incidentes</button>
                      <h2>{routeFromLocation().ticketMode === "edit" ? "Editar chamado" : detail.incident.title}</h2>
                      <p>{routeFromLocation().ticketMode === "edit" ? detail.incident.title : "Atendimento, responsáveis, prazos e histórico do incidente."}</p>
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
                  <nav className="database-tabs" aria-label="Seções do chamado">
                    {[["service","Atendimento"],["tasks","Tarefas"],["approvals","Aprovações"],["assets","Ativos"],["contract","Contrato e consumo"],["history","Histórico"]].filter(([key])=>(detail.allowed_tabs||[]).includes(key)).map(([key,label])=><button key={key} className={incidentTab===key?"active":""} onClick={()=>setIncidentTab(key)}>{label}</button>)}
                  </nav>
                  {incidentTab === "service" && <section className="ticket-tab-panel">
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
                    <label className="field"><span>Política de SLA</span><select defaultValue={detail.incident.sla_policy_id||""} onChange={(e)=>updateIncident(detail.incident,detail.incident.status,{sla_policy_id:e.target.value||null})}><option value="">SLA automático pela prioridade</option>{slas.filter((item)=>item.active).map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
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
                      {(c.author_id===currentUser?.id || currentUser?.is_master) && <details><summary>Editar comentário</summary><form onSubmit={async(event)=>{event.preventDefault();const f=new FormData(event.currentTarget);await run(async()=>{await api(`/api/v1/tickets/${detail.incident.id}/entries/${c.id}`,token,{method:"PATCH",body:JSON.stringify({body:f.get("body")})},tenantId);await loadDetail(detail.incident,routeFromLocation().ticketMode||"detail",false);},"Comentário atualizado com sucesso.");}}><textarea name="body" defaultValue={c.body} required/><button type="submit">Salvar comentário</button></form></details>}
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
                  <h3>Anexos</h3>
                  <form className="form-row" onSubmit={async(event)=>{event.preventDefault();const file=event.currentTarget.elements.attachment.files?.[0];if(!file)return;const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=reject;reader.readAsDataURL(file);});await run(async()=>{await api(`/api/v1/portal/tickets/${detail.incident.id}/attachments`,token,{method:"POST",body:JSON.stringify({original_name:file.name,declared_type:file.type||null,content_base64:content})},tenantId);await loadDetail(detail.incident,routeFromLocation().ticketMode||"detail",false);event.currentTarget.reset();},"Anexo recebido e colocado em quarentena para verificação.");}}><label className="field"><span>Arquivo (até 10 MiB)</span><input name="attachment" type="file" required/></label><button type="submit">Enviar anexo</button></form>
                  </section>}
                  {incidentTab === "tasks" && <section className="ticket-tab-panel"><h3>Tarefas</h3><DataGrid showEmptyGrid rows={detail.tasks||[]} columns={[{key:"title",label:"Tarefa"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"assignee_name",label:"Responsável"},{key:"due_at",label:"Prazo",render:(row)=>fmt(row.due_at)}]}/><h3>Obrigações</h3><DataGrid showEmptyGrid rows={detail.obligations||[]} columns={[{key:"kind",label:"Obrigação"},{key:"state",label:"Estado",render:(row)=><Status value={row.state}/>},{key:"due_at",label:"Prazo",render:(row)=>fmt(row.due_at)},{key:"completed_at",label:"Conclusão",render:(row)=>fmt(row.completed_at)}]}/></section>}
                  {incidentTab === "approvals" && <section className="ticket-tab-panel"><h3>Aprovações e soluções</h3><DataGrid showEmptyGrid rows={detail.approvals||[]} columns={[{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"reason",label:"Motivo"},{key:"created_at",label:"Solicitada",render:(row)=>fmt(row.created_at)},{key:"decided_at",label:"Decisão",render:(row)=>fmt(row.decided_at)}]}/><DataGrid showEmptyGrid rows={detail.solutions||[]} columns={[{key:"solution_type",label:"Tipo"},{key:"description",label:"Solução"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"created_at",label:"Proposta",render:(row)=>fmt(row.created_at)}]}/><form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();const f=new FormData(event.currentTarget);await run(async()=>{await api(`/api/v1/tickets/${detail.incident.id}/solutions`,token,{method:"POST",body:JSON.stringify({solution_type:f.get("solution_type"),description:f.get("description"),evidence:f.get("evidence")||null})},tenantId);await loadDetail(detail.incident,routeFromLocation().ticketMode||"detail",false);event.currentTarget.reset();},"Solução registrada. O chamado já pode seguir para resolução.");}}><FormSection title="Registrar solução"><label className="field"><span>Tipo</span><select name="solution_type"><option value="permanent">Definitiva</option><option value="workaround">Contorno</option><option value="not_reproduced">Não reproduzido</option><option value="cancelled">Cancelamento</option></select></label><label className="field"><span>Nota de solução</span><textarea name="description" required/></label><label className="field"><span>Evidências</span><textarea name="evidence"/></label></FormSection><button type="submit">Registrar solução</button></form></section>}
                  {incidentTab === "assets" && <section className="ticket-tab-panel"><h3>Ativos e anexos</h3><DataGrid showEmptyGrid rows={detail.assets||[]} columns={[{key:"name",label:"Ativo"},{key:"kind",label:"Tipo"},{key:"address",label:"Endereço"},{key:"criticality",label:"Criticidade",render:(row)=><Status value={row.criticality}/>}]} /><DataGrid showEmptyGrid rows={detail.attachments||[]} columns={[{key:"original_name",label:"Arquivo"},{key:"detected_type",label:"Tipo real"},{key:"size_bytes",label:"Bytes"},{key:"scan_status",label:"Varredura",render:(row)=><Status value={row.scan_status}/>}]} /></section>}
                  {incidentTab === "contract" && <section className="ticket-tab-panel"><h3>Contrato e consumo</h3><DataGrid showEmptyGrid rows={detail.contracts||[]} columns={[{key:"name",label:"Contrato"},{key:"code",label:"Código"},{key:"status",label:"Estado",render:(row)=><Status value={row.status}/>},{key:"active_version",label:"Versão"}]}/><DataGrid showEmptyGrid rows={detail.consumption||[]} columns={[{key:"competence",label:"Competência"},{key:"description",label:"Descrição"},{key:"entry_type",label:"Tipo"},{key:"raw_minutes",label:"Bruto"},{key:"billable_minutes",label:"Cobrável"},{key:"amount",label:"Valor",render:(row)=>row.amount??"Base ausente"}]}/></section>}
                  {incidentTab === "history" && <section className="ticket-tab-panel"><h3>Histórico</h3>
                  {(detail.history||[]).map((h) => (
                    <article className="timeline" key={h.id}>
                      <b>{h.event_type}</b>
                      <small>
                        {fmt(h.created_at)} • {h.actor_name || "Sistema"}
                      </small>
                    </article>
                  ))}</section>}
                </section>
              )}
            </div>
          )}
          {view === "suppliers" && (
            editingSupplier ? <FormPage title={editingSupplier.id ? "Editar fornecedor" : "Novo fornecedor"} description={`${tenant?.name || "Empresa selecionada"} · contatos, cobertura e compromissos do terceiro.`} onBack={closeSupplierForm}>
              <form className="nexora-form" onSubmit={async (event) => { event.preventDefault(); if (supplierSubmitting || !tenantId) return; const f=new FormData(event.currentTarget); setSupplierSubmitting(true); await run(async()=>{const body={name:f.get("name"),contact_name:f.get("contact_name")||null,email:f.get("email")||null,phone:f.get("phone")||null,services:f.get("services")||null,contract_reference:f.get("contract_reference")||null,response_minutes:Number(f.get("response_minutes")),resolution_minutes:Number(f.get("resolution_minutes")),active:editingSupplier.active??true}; await api(`/api/v1/suppliers${editingSupplier.id?`/${editingSupplier.id}`:""}`,token,{method:editingSupplier.id?"PUT":"POST",body:JSON.stringify(body)},tenantId); await refresh(); closeSupplierForm();},editingSupplier.id?"Fornecedor atualizado com sucesso.":"Fornecedor criado com sucesso."); setSupplierSubmitting(false); }}>
                <FormSection title="Identificação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="name" label="Fornecedor" defaultValue={editingSupplier.name||""} required maxLength="160"/><Input name="services" label="Serviços prestados" defaultValue={editingSupplier.services||""}/><Input name="contract_reference" label="Referência contratual" defaultValue={editingSupplier.contract_reference||""}/></FormSection>
                <FormSection title="Contato"><Input name="contact_name" label="Pessoa de contato" defaultValue={editingSupplier.contact_name||""}/><Input name="email" label="E-mail" type="email" defaultValue={editingSupplier.email||""}/><Input name="phone" label="Telefone" defaultValue={editingSupplier.phone||""}/></FormSection>
                <FormSection title="Compromissos"><Input name="response_minutes" label="Resposta (minutos)" type="number" min="1" defaultValue={editingSupplier.response_minutes||240} required/><Input name="resolution_minutes" label="Solução (minutos)" type="number" min="1" defaultValue={editingSupplier.resolution_minutes||1440} required/></FormSection>
                <FormActions onCancel={closeSupplierForm} submitting={supplierSubmitting} submitLabel={editingSupplier.id?"Salvar fornecedor":"Criar fornecedor"}/>
              </form>
            </FormPage> : <section className="panel">
              <div className="list-page-heading"><div><h2>Fornecedores e terceiros</h2><p>Cadastros vinculados à Empresa selecionada.</p></div><button className="primary" onClick={()=>openSupplierForm()}>＋ Novo fornecedor</button></div>
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
                    <button onClick={() => openSupplierForm(s)}>Editar</button>
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
                  </>
                )}
              />
            </section>
          )}
          {view === "sla" && (
            editingSla ? <FormPage title={editingSla.id?"Editar política de SLA":"Nova política de SLA"} description={`${tenant?.name||"Empresa selecionada"} · defina prioridade, prazos e cobertura.`} onBack={closeSlaForm}>
              <form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(slaSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setSlaSubmitting(true);await run(async()=>{const body={name:f.get("name"),priority:f.get("priority"),response_minutes:Number(f.get("response_minutes")),resolution_minutes:Number(f.get("resolution_minutes")),coverage:f.get("coverage"),pause_on_waiting:f.get("pause_on_waiting")==="on",active:editingSla.active??true};await api(`/api/v1/sla-policies${editingSla.id?`/${editingSla.id}`:""}`,token,{method:editingSla.id?"PUT":"POST",body:JSON.stringify(body)},tenantId);await refresh();closeSlaForm();},editingSla.id?"Política atualizada com sucesso.":"Política criada com sucesso.");setSlaSubmitting(false);}}>
                <FormSection title="Política"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="name" label="Nome" defaultValue={editingSla.name||""} required/><label className="field"><span>Prioridade</span><select name="priority" defaultValue={editingSla.priority||"medium"}>{["low","medium","high","critical"].map((value)=><option key={value} value={value}>{translated(value)}</option>)}</select></label><label className="field"><span>Cobertura</span><select name="coverage" defaultValue={editingSla.coverage||"24x7"}><option value="24x7">24×7</option><option value="business_hours">Horário comercial</option></select></label></FormSection>
                <FormSection title="Prazos"><Input name="response_minutes" label="Resposta (minutos)" type="number" min="1" defaultValue={editingSla.response_minutes||""} required/><Input name="resolution_minutes" label="Solução (minutos)" type="number" min="1" defaultValue={editingSla.resolution_minutes||""} required/><label className="switch"><input name="pause_on_waiting" type="checkbox" defaultChecked={Boolean(editingSla.pause_on_waiting)}/> Pausar em espera autorizada</label></FormSection>
                <FormActions onCancel={closeSlaForm} submitting={slaSubmitting} submitLabel={editingSla.id?"Salvar política":"Criar política"}/>
              </form>
            </FormPage> : <section className="panel">
              <div className="list-page-heading"><div><h2>Políticas de SLA</h2><p>Prazos e coberturas versionados por Empresa.</p></div><button className="primary" onClick={()=>openSlaForm()}>＋ Nova política</button></div>
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
                    <button onClick={() => openSlaForm(s)}>Editar</button>
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
                      Revogar
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
          {["settings-notifications", "admin-teams", "admin-agents", "admin-maintenance"].includes(view) && (
            <div className="ops-grid">
              {view === "admin-teams" && (
              editingTeam ? (
                <section className="team-form-page wide-panel">
                  <div className="form-page-heading">
                    <button className="secondary back-button" onClick={closeTeamForm}>← Voltar</button>
                    <div><h2>{editingTeam.id ? "Editar equipe" : "Nova equipe"}</h2><p>{editingTeam.id ? "Atualize os dados" : "Cadastre uma nova equipe"} para a empresa <b>{tenant?.name}</b>.</p></div>
                  </div>
                  <form key={editingTeam.id || "new-team"} className="team-form standard-form-page" onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    submit(editingTeam.id ? `teams/${editingTeam.id}` : "teams", {
                      name: f.get("name"), emails: teamContacts.emails, whatsapp_numbers: teamContacts.numbers,
                      whatsapp_group_ids: teamContacts.groups, member_user_ids: teamContacts.members,
                      active: editingTeam.active ?? true,
                    }, async () => { closeTeamForm(); await refresh(); }, editingTeam.id ? "PUT" : "POST");
                  }}>
                    <section className="form-section"><h3>1. Identificação</h3><label className="field"><span>Empresa</span><input value={tenant?.name || ""} disabled /></label><Input name="name" label="Nome da equipe" defaultValue={editingTeam.name || ""} required /></section>
                    <section className="form-section"><h3>2. Técnicos</h3><CollectionField label="Técnicos vinculados" items={teamContacts.members} onChange={(members) => setTeamContacts((v) => ({ ...v, members }))} options={users.filter((u) => u.active).map((u) => ({ value: String(u.id), label: `${u.display_name} — ${u.email}` }))} /></section>
                    <section className="form-section form-section-wide"><h3>3. Canais de notificação</h3><div className="form-two-columns"><CollectionField label="E-mails" items={teamContacts.emails} onChange={(emails) => setTeamContacts((v) => ({ ...v, emails }))} placeholder="nome@empresa.com" /><CollectionField label="Números de WhatsApp" items={teamContacts.numbers} onChange={(numbers) => setTeamContacts((v) => ({ ...v, numbers }))} placeholder="5511999999999" /></div><CollectionField label="Grupos de WhatsApp" items={teamContacts.groups} onChange={(groups) => setTeamContacts((v) => ({ ...v, groups }))} placeholder="Nome ou ID do grupo" /></section>
                    <section className="form-section"><h3>4. Configurações</h3><label className="switch"><input type="checkbox" checked={editingTeam.active ?? true} onChange={(e) => setEditingTeam((value) => ({ ...value, active: e.target.checked }))} /> Equipe ativa</label></section>
                    <div className="form-actions"><button type="button" className="secondary" onClick={closeTeamForm}>Cancelar</button><button className="primary">{editingTeam.id ? "Salvar equipe" : "Criar equipe"}</button></div>
                  </form>
                </section>
              ) : (
                <section className="team-list-page wide-panel">
                  <div className="list-page-heading"><div><h2>Equipes</h2><p>Gerencie os técnicos e os canais de notificação da empresa <b>{tenant?.name || "selecionada"}</b>.</p></div><button className="primary" disabled={!tenantId} onClick={() => openTeamForm()}>＋ Nova equipe</button></div>
                  <section className="metric-group" aria-label="Indicadores do resultado filtrado"><article><span>Total de equipes</span><strong>{filteredTeams.length}</strong></article><article><span>Ativas</span><strong>{filteredTeams.filter((t) => t.active).length}</strong></article><article><span>Inativas</span><strong>{filteredTeams.filter((t) => !t.active).length}</strong></article><article><span>Técnicos vinculados</span><strong>{distinctTeamMembers}</strong></article></section>
                  <div className="filter-bar"><label className="field filter-search"><span>Buscar</span><input value={teamSearch} onChange={(e) => setTeamSearch(e.target.value)} placeholder="Buscar equipe, técnico ou e-mail" /></label><label className="field"><span>Situação</span><select value={teamStatus} onChange={(e) => setTeamStatus(e.target.value)}><option value="all">Todas</option><option value="active">Ativas</option><option value="inactive">Inativas</option></select></label></div>
                  <DataGrid stateKey={`${tenantId}:teams`} showEmptyGrid empty={tenantId ? `Nenhuma equipe cadastrada para ${tenant?.name || "esta empresa"}.` : "Selecione uma empresa no topo para visualizar as equipes."} columns={[{ key: "name", label: "Equipe", icon: "team" },{ key: "member_names", label: "Técnicos", render: (r) => r.member_names || "—" },{ key: "emails", label: "E-mails", render: (r) => (r.emails || []).length },{ key: "whatsapp_numbers", label: "WhatsApp", render: (r) => (r.whatsapp_numbers || []).length },{ key: "active", label: "Situação", render: (r) => <Status value={r.active ? "ativo" : "inativo"} /> }]} rows={filteredTeams} actions={(t) => <><button aria-label={`Editar equipe ${t.name}`} title="Editar" onClick={() => openTeamForm(t)}>Editar</button><button onClick={() => submit(`teams/${t.id}`, { name: t.name, emails: t.emails || [], whatsapp_numbers: t.whatsapp_numbers || [], whatsapp_group_ids: t.whatsapp_group_ids || [], member_user_ids: t.member_user_ids || [], active: !t.active }, refresh, "PUT")}>{t.active ? "Inativar" : "Reativar"}</button></>} />
                </section>
              )
              )}
              {view === "admin-agents" && (
              editingAgent ? <FormPage title={editingAgent.id?"Editar agente":"Novo agente de coleta"} description={`${tenant?.name||"Empresa selecionada"} · instalação segura e identificação do coletor.`} onBack={closeAgentForm}>
                {editingAgent.id ? <form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(agentSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setAgentSubmitting(true);await run(async()=>{await api(`/api/v1/agents/${editingAgent.id}`,token,{method:"PUT",body:JSON.stringify({name:f.get("name"),status:editingAgent.status,configuration:editingAgent.configuration||{}})},tenantId);await refresh();closeAgentForm();},"Agente atualizado com sucesso.");setAgentSubmitting(false);}}><FormSection title="Identificação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="name" label="Nome do agente" defaultValue={editingAgent.name||""} required/><Input label="Máquina" value={editingAgent.machine_id||"—"} readOnly/><Input label="Versão" value={editingAgent.version||"—"} readOnly/></FormSection><FormActions onCancel={closeAgentForm} submitting={agentSubmitting} submitLabel="Salvar agente"/></form> : <form className="nexora-form agent-enrollment-form" onSubmit={async(event)=>{event.preventDefault();if(agentSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setAgentSubmitting(true);await run(async()=>{const enrollment=await api("/api/v1/agents/enrollment-tokens",token,{method:"POST",body:JSON.stringify({description:f.get("description"),valid_hours:f.get("never_expires")?null:Number(f.get("hours"))})},tenantId);setAgentEnrollment(enrollment);},"Chave de instalação gerada com sucesso.");setAgentSubmitting(false);}}><FormSection title="Instalação"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="description" label="Identificação da instalação" placeholder="Servidor SQL da empresa" required/><Input name="hours" label="Validade em horas" type="number" min="1" max="8760" defaultValue="24"/><label className="check-field"><input name="never_expires" type="checkbox"/> Não expira enquanto não for utilizada</label></FormSection>{agentEnrollment&&<div className="enrollment-token"><strong>Chave de instalação</strong><p>Exibida somente agora. Copie e guarde em local seguro.</p><code>{agentEnrollment.enrollment_token}</code><button type="button" className="secondary" onClick={()=>navigator.clipboard.writeText(agentEnrollment.enrollment_token)}>Copiar chave</button><small>{agentEnrollment.expires_at?`Válida até ${fmt(agentEnrollment.expires_at)}`:"Sem data de expiração"} · aceita uma única instalação.</small></div>}<FormActions onCancel={closeAgentForm} submitting={agentSubmitting} submitLabel="Gerar chave de instalação"/></form>}
              </FormPage> : <section className="panel wide-panel">
                <h2>Agentes de coleta</h2>
                <p>Gere uma chave de instalação de uso único. O endereço da Central e essa chave serão informados no instalador do servidor do cliente.</p>
                <div className="list-page-heading"><span></span><button className="primary" onClick={()=>openAgentForm()}>＋ Novo agente</button></div>
                <DataGrid
                  columns={[
                    { key: "name", label: "Agente" },
                    { key: "machine_id", label: "Máquina" },
                    { key: "version", label: "Versão" },
                    { key: "last_seen_at", label: "Último contato", render: (r) => fmt(r.last_seen_at) },
                    { key: "status", label: "Estado", render: (r) => <Status value={r.status === "online" ? "ativo" : r.status === "disabled" ? "inativo" : "offline"} /> },
                  ]}
                  rows={agents}
                  actions={(a) => <>
                    <button onClick={() => openAgentForm(a)}>Editar</button>
                    <button onClick={() => submit(`agents/${a.id}`, { name: a.name, status: a.status === "disabled" ? "offline" : "disabled", configuration: a.configuration || {} }, refresh, "PUT")}>{a.status === "disabled" ? "Ativar" : "Inativar"}</button>
                  </>}
                />
              </section>
              )}
              {view === "settings-notifications" && (
              editingRule ? <FormPage title={editingRule.id?"Editar regra de notificação":"Nova regra de notificação"} description={`${tenant?.name||"Empresa selecionada"} · defina canal, destino e prioridade mínima.`} onBack={closeRuleForm}>
                <form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(ruleSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setRuleSubmitting(true);await run(async()=>{const body={name:f.get("name"),channel:f.get("channel"),target:f.get("target"),minimum_priority:f.get("priority"),events:editingRule.events||["incident.created","incident.resolved","incident.in_progress"],active:editingRule.active??true};await api(`/api/v1/notification-rules${editingRule.id?`/${editingRule.id}`:""}`,token,{method:editingRule.id?"PUT":"POST",body:JSON.stringify(body)},tenantId);await refresh();closeRuleForm();},editingRule.id?"Regra atualizada com sucesso.":"Regra criada com sucesso.");setRuleSubmitting(false);}}><FormSection title="Regra"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="name" label="Nome" defaultValue={editingRule.name||""} required/><label className="field"><span>Canal</span><select name="channel" defaultValue={editingRule.channel||"email"}><option value="email">E-mail</option><option value="webhook">Webhook</option></select></label><Input name="target" label="Destino" defaultValue={editingRule.target||""} required/><label className="field"><span>Prioridade mínima</span><select name="priority" defaultValue={editingRule.minimum_priority||"low"}>{["low","medium","high","critical"].map((value)=><option key={value} value={value}>{translated(value)}</option>)}</select></label></FormSection><FormActions onCancel={closeRuleForm} submitting={ruleSubmitting} submitLabel={editingRule.id?"Salvar regra":"Criar regra"}/></form>
              </FormPage> : <section className="panel">
                <h2>Notificações</h2>
                <div className="list-page-heading"><p>Regras de distribuição por canal e severidade.</p><button className="primary" onClick={()=>openRuleForm()}>＋ Nova regra</button></div>
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
                      <button onClick={() => openRuleForm(r)}>Editar</button>
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
                    </>
                  )}
                />
              </section>
              )}
              {view === "admin-maintenance" && (
              editingMaintenance ? <FormPage title={editingMaintenance.id?"Editar janela de manutenção":"Nova janela de manutenção"} description={`${tenant?.name||"Empresa selecionada"} · programe a supressão operacional de forma auditável.`} onBack={closeMaintenanceForm}>
                <form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(maintenanceSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setMaintenanceSubmitting(true);await run(async()=>{const body={name:f.get("name"),asset_id:f.get("asset")||null,starts_at:new Date(f.get("start")).toISOString(),ends_at:new Date(f.get("end")).toISOString(),reason:f.get("reason")||null,active:editingMaintenance.active??true};await api(`/api/v1/maintenance-windows${editingMaintenance.id?`/${editingMaintenance.id}`:""}`,token,{method:editingMaintenance.id?"PUT":"POST",body:JSON.stringify(body)},tenantId);await refresh();closeMaintenanceForm();},editingMaintenance.id?"Janela atualizada com sucesso.":"Janela agendada com sucesso.");setMaintenanceSubmitting(false);}}><FormSection title="Agendamento"><Input label="Empresa" value={tenant?.name||""} readOnly/><Input name="name" label="Nome" defaultValue={editingMaintenance.name||""} required/><label className="field"><span>Ativo</span><select name="asset" defaultValue={editingMaintenance.asset_id||""}><option value="">Todos os ativos</option>{assets.map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><Input name="start" label="Início" type="datetime-local" defaultValue={editingMaintenance.starts_at?String(editingMaintenance.starts_at).slice(0,16):""} required/><Input name="end" label="Fim" type="datetime-local" defaultValue={editingMaintenance.ends_at?String(editingMaintenance.ends_at).slice(0,16):""} required/><Input name="reason" label="Motivo" defaultValue={editingMaintenance.reason||""} required/></FormSection><FormActions onCancel={closeMaintenanceForm} submitting={maintenanceSubmitting} submitLabel={editingMaintenance.id?"Salvar janela":"Agendar janela"}/></form>
              </FormPage> : <section className="panel wide-panel">
                <h2>Janelas de manutenção</h2>
                <div className="list-page-heading"><p>Períodos planejados por Empresa e ativo.</p><button className="primary" onClick={()=>openMaintenanceForm()}>＋ Nova janela</button></div>
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
                      <button onClick={() => openMaintenanceForm(m)}>Editar</button>
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
                    </>
                  )}
                />
              </section>
              )}
            </div>
          )}
          {view === "admin-integrations" && (integrationCreating ? <FormPage title="Nova integração" description={`${tenant?.name||"Empresa selecionada"} · registre a conexão sem expor segredos no frontend.`} onBack={closeIntegrationForm}><form className="nexora-form" onSubmit={async(event)=>{event.preventDefault();if(integrationSubmitting||!tenantId)return;const f=new FormData(event.currentTarget);setIntegrationSubmitting(true);await run(async()=>{await api("/api/v1/integrations",token,{method:"POST",body:JSON.stringify({integration:f.get("integration"),connection_key:f.get("connection_key"),display_name:f.get("display_name"),base_url:f.get("base_url")||null,secret_reference:null})},tenantId);await loadTenantResource("integrations",setIntegrationConnections);closeIntegrationForm();},"Integração cadastrada com sucesso.");setIntegrationSubmitting(false);}}><FormSection title="Conexão"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Tipo</span><select name="integration">{["glpi","zabbix","agent","smtp","whatsapp","erp"].map((value)=><option key={value} value={value}>{value.toUpperCase()}</option>)}</select></label><Input name="connection_key" label="Chave da conexão" required/><Input name="display_name" label="Nome" required/><Input name="base_url" label="URL base" type="url"/></FormSection><FormActions onCancel={closeIntegrationForm} submitting={integrationSubmitting} submitLabel="Cadastrar integração"/></form></FormPage> : <section className="panel"><div className="section-head"><div><h2>Integrações</h2><p>Conexões, capacidade, homologação, reconciliação e mapeamentos explícitos. Segredos permanecem no backend.</p></div><button className="primary" onClick={openIntegrationForm}>＋ Nova integração</button></div><DataGrid showEmptyGrid rows={integrationConnections} columns={[{key:"display_name",label:"Integração"},{key:"integration",label:"Tipo"},{key:"connection_key",label:"Conexão"},{key:"homologation_state",label:"Homologação",render:(row)=><Status value={row.homologation_state}/>},{key:"mapping_count",label:"Mapeamentos"},{key:"last_reconciled_at",label:"Última reconciliação",render:(row)=>fmt(row.last_reconciled_at)},{key:"open_reconciliation_findings",label:"Divergências",render:(row)=><Status value={Number(row.open_reconciliation_findings)>0?`${row.open_reconciliation_findings} abertas`:"sem divergências"}/>},{key:"last_success_at",label:"Último sucesso",render:(row)=>fmt(row.last_success_at)},{key:"last_error",label:"Última falha"}]}/></section>)}
          {view === "admin-pending" && <ListPage title="Operações pendentes" description="Inspeção, atraso, tentativas, erro, conflito, incerteza e reprocessamento autorizado da outbox." eyebrow="Operação das integrações" metrics={<MetricGroup items={[{label:"Na fila",value:integrationCommands.metrics?.queued||0},{label:"Incertas",value:integrationCommands.metrics?.uncertain||0},{label:"Conflitos",value:integrationCommands.metrics?.conflicts||0},{label:"Maior atraso",value:`${integrationCommands.metrics?.oldest_queue_seconds||0}s`}]} />} filters={<FilterBar><label className="field"><span>Estado</span><select value={integrationCommandFilter} onChange={(event)=>setIntegrationCommandFilter(event.target.value)}><option value="all">Todos</option>{["pending","running","retry_wait","uncertain","conflict","rejected","succeeded","cancelled"].map((state)=><option key={state} value={state}>{translated(state)}</option>)}</select></label></FilterBar>}><DataGrid showEmptyGrid rows={(integrationCommands.items||[]).filter((row)=>integrationCommandFilter==="all"||row.state===integrationCommandFilter)} columns={[{key:"id",label:"ID"},{key:"topic",label:"Operação"},{key:"state",label:"Estado",render:(row)=><Status value={row.state}/>},{key:"attempts",label:"Tentativas"},{key:"available_at",label:"Disponível",render:(row)=>fmt(row.available_at)},{key:"state_reason",label:"Motivo"},{key:"last_error",label:"Erro"}]} actions={(row)=>featureEnabled("action:integration-command.retry")&&["retry_wait","rejected","uncertain","conflict"].includes(row.state)&&<button onClick={()=>submit(`integration-commands/${row.id}/retry`,{},async()=>loadTenantResource("integration-commands",setIntegrationCommands))}>Reprocessar</button>}/></ListPage>}
          {view === "admin-release" && (operationalCreateView==="feature-flag"?<FormPage title="Configurar liberação gradual" description="Defina o modo por Empresa e registre a justificativa auditável da decisão." onBack={closeOperationalCreate}><form className="nexora-form" onSubmit={async(e)=>{e.preventDefault();if(operationalSubmitting)return;const f=new FormData(e.currentTarget),feature=f.get("feature");setOperationalSubmitting(true);await submit(`feature-flags/${encodeURIComponent(feature)}`,{mode:f.get("mode"),reason:f.get("reason")},async()=>{await Promise.all([loadTenantResource("feature-flags",setFeatureFlags),api("/api/v1/me/context",token).then(setAuthContext)]);closeOperationalCreate();},"PUT");setOperationalSubmitting(false);}}><FormSection title="Funcionalidade"><Input label="Empresa" value={tenant?.name||""} readOnly/><label className="field"><span>Chave da funcionalidade</span><input name="feature" list="feature-flag-keys" required/><datalist id="feature-flag-keys"><option value="view:billing"/><option value="view:profitability"/><option value="view:runbooks"/><option value="action:integration-command.retry"/></datalist></label><label className="field"><span>Modo</span><select name="mode"><option value="off">Desligado</option><option value="shadow">Sombra</option><option value="write_pilot">Escrita piloto</option><option value="enabled">Habilitado</option></select></label><Input name="reason" label="Motivo e evidência da decisão" required/></FormSection><FormActions onCancel={closeOperationalCreate} submitting={operationalSubmitting} submitLabel="Aplicar liberação"/></form></FormPage>:<section className="panel"><div className="section-head"><div><h2>Liberação gradual</h2><p>Controle por empresa: desligado, sombra, escrita piloto e habilitado.</p></div><button className="primary" onClick={()=>openOperationalCreate("feature-flag")}>＋ Configurar liberação</button></div><DataGrid showEmptyGrid rows={featureFlags} columns={[{key:"feature",label:"Funcionalidade"},{key:"mode",label:"Modo",render:(row)=><Status value={row.mode}/>},{key:"reason",label:"Motivo"},{key:"updated_at",label:"Atualização",render:(row)=>fmt(row.updated_at)}]}/></section>)}
          {(view === "admin" || view === "admin-users") && (
            <div className="ops-grid">
              {view === "admin-users" && editingAccessUser && (
                <FormPage title="Editar usuário" description="Defina o Master técnico, contato e as Empresas explicitamente autorizadas." onBack={closeUserForm}>
                  <form className="nexora-form" onSubmit={async (event) => {
                    event.preventDefault();
                    if (userSubmitting) return;
                    const form = new FormData(event.currentTarget);
                    const is_master = form.get("is_master") === "on";
                    const tenant_ids = is_master ? [] : form.getAll("tenant_ids");
                    setUserSubmitting(true);
                    try {
                      await api(`/api/v1/users/${editingAccessUser.id}/notifications`, token, { method: "PUT", body: JSON.stringify({ whatsapp_number: form.get("whatsapp_number")?.trim() || null }) }, tenantId);
                      await api(`/api/v1/users/${editingAccessUser.id}/access`, token, { method: "PUT", body: JSON.stringify({ is_master, tenant_ids }) }, tenantId);
                      if (!is_master && tenantId) await api(`/api/v1/users/${editingAccessUser.id}/departments`, token, { method: "PUT", body: JSON.stringify({ department_ids: form.getAll("department_ids") }) }, tenantId);
                      await refresh();
                      await api("/api/v1/auth/me", token).then(setCurrentUser);
                      setNotice({ type: "success", text: "Usuário e acessos atualizados com sucesso." });
                      closeUserForm();
                    } catch (exception) {
                      captureError(exception);
                      setNotice({ type: "error", text: exception.message });
                    } finally {
                      setUserSubmitting(false);
                    }
                  }}>
                    <FormSection title="Identificação e contato">
                      <label className="field"><span>Nome</span><input value={editingAccessUser.display_name || ""} readOnly aria-readonly="true" /></label>
                      <label className="field"><span>E-mail</span><input value={editingAccessUser.email || ""} readOnly aria-readonly="true" /></label>
                      <label className="field"><span>WhatsApp</span><input name="whatsapp_number" defaultValue={editingAccessUser.whatsapp_number || ""} inputMode="tel" placeholder="País + DDD + número" /></label>
                      <label className="master-option"><input name="is_master" type="checkbox" checked={editingUserMaster} onChange={(event) => setEditingUserMaster(event.target.checked)} /><span><b>Master</b><small>Acesso administrativo integral. Somente um usuário pode ser Master.</small></span></label>
                    </FormSection>
                    <FormSection title="Empresas permitidas" description={editingUserMaster ? "O Master possui acesso administrativo integral; concessões individuais ficam desabilitadas." : "Marque somente as Empresas necessárias para este usuário."}>
                      <div className="tenant-access-list user-form-tenant-list" aria-disabled={editingUserMaster}>
                        {tenants.map((item) => <label key={item.id}><input name="tenant_ids" type="checkbox" value={item.id} disabled={editingUserMaster} defaultChecked={(editingAccessUser.tenant_ids || []).map(String).includes(String(item.id))} /><span>{item.name}</span></label>)}
                      </div>
                    </FormSection>
                    {!editingUserMaster && <FormSection title="Departamentos permitidos" description={`Escopo departamental dentro de ${tenant?.name||"Empresa selecionada"}.`}><div className="tenant-access-list user-form-tenant-list">{departments.filter((item)=>item.active).map((item)=><label key={item.id}><input name="department_ids" type="checkbox" value={item.id} defaultChecked={(editingAccessUser.department_ids||[]).map(String).includes(String(item.id))}/><span>{item.name}</span></label>)}</div>{!departments.length&&<p className="empty-state">Nenhum departamento ativo cadastrado.</p>}</FormSection>}
                    <FormActions onCancel={closeUserForm} submitting={userSubmitting} submitLabel="Salvar usuário" />
                  </form>
                </FormPage>
              )}
              {view === "admin-users" && !editingAccessUser && (
              <section className="panel wide-panel">
                <div className="section-head">
                  <div>
                    <h2>Cadastro de usuários</h2>
                    <p>O Master administra todo o workspace. Para os demais usuários, marque somente as Empresas autorizadas.</p>
                  </div>
                </div>
                {!currentUser?.is_master && (
                  <p className="info-banner">A gestão de acesso a Empresas é exclusiva do usuário Master.</p>
                )}
                <DataGrid
                  columns={[
                    { key: "display_name", label: "Usuário" },
                    { key: "email", label: "E-mail" },
                    { key: "whatsapp_number", label: "WhatsApp", render: (r) => r.whatsapp_number || "—" },
                    {
                      key: "access",
                      label: "Acesso",
                      render: (r) => r.is_master ? <Status value="Master" /> : `${(r.tenant_ids || []).length} Empresa(s)`,
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
                      {currentUser?.is_master && (
                        <button onClick={() => openUserForm(u)}>
                          Editar usuário
                        </button>
                      )}
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
              )}
              {view === "admin" && (<>
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
              </>)}
            </div>
          )}
          {["settings-email", "settings-whatsapp"].includes(view) &&
            (tenantId ? (
              <NotificationChannels
                channel={view === "settings-email" ? "email" : "whatsapp"}
                settings={notificationSettings}
                tenantId={tenantId}
                token={token}
                submit={submit}
                refresh={refresh}
                run={run}
              />
            ) : (
              <section className="panel">
                <Empty>
                  Selecione uma empresa para acessar a parametrização dos
                  canais de notificação da plataforma.
                </Empty>
              </section>
            ))}{" "}
        </main>
      </div>
      {pendingDelete && <div className="confirm-overlay" role="presentation" onMouseDown={(event)=>{if(event.target===event.currentTarget&&!deleteSubmitting)setPendingDelete(null);}}><section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title" aria-describedby="delete-description"><div className="confirm-icon" aria-hidden="true">!</div><div><h2 id="delete-title">Inativar registro?</h2><p id="delete-description"><b>{pendingDelete.label}</b> deixará de participar das operações ativas da Empresa. O histórico e as referências serão preservados.</p></div><div className="form-actions"><button type="button" className="secondary" onClick={()=>setPendingDelete(null)} disabled={deleteSubmitting}>Cancelar</button><button type="button" className="danger" onClick={confirmDelete} disabled={deleteSubmitting} autoFocus>{deleteSubmitting?"Inativando...":"Confirmar inativação"}</button></div></section></div>}
      <Toast notice={notice} onClose={() => setNotice(null)} />
    </AppShell>
  );
}
createRoot(document.getElementById("root")).render(<App />);
