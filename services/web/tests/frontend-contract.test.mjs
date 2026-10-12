import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/nexora.css", import.meta.url), "utf8");

test("CD-01 exposes addressable new and edit routes for pilot lists", () => {
  for (const route of [
    "/administracao/equipes/novo",
    "/monitoramento/servicos/novo",
    "/monitoramento/ambientes/novo",
    "/operacao/fornecedores/novo",
    "/operacao/sla/novo",
    "/administracao/agentes/novo",
    "/configuracoes/notificacoes/nova",
    "/administracao/janelas-manutencao/nova",
    "/empresas/nova",
    "/operacao/catalogo/novo",
    "/administracao/integracoes/nova",
    "/operacao/incidentes-maiores/novo",
    "/operacao/conhecimento/novo",
    "/operacao/problemas/novo",
    "/operacao/mudancas/nova",
    "/operacao/runbooks/novo",
    "/operacao/apontamentos/novo",
    "/operacao/contratos/novo",
    "/operacao/consumo/nova-revisao",
    "/operacao/pre-faturamento/nova-competencia",
    "/operacao/chamados/novo",
    "/monitoramento/topologia/novo-vinculo",
    "/administracao/relacoes-servico/nova",
    "/administracao/concessoes/nova",
    "/administracao/liberacao/nova",
  ]) {
    assert.ok(source.includes(route), `missing route ${route}`);
  }
  for (const pattern of [
    /administracao\\\/equipes\\\/\(\[\^\/\]\+\)\\\/editar/,
    /monitoramento\\\/servicos\\\/\(\[\^\/\]\+\)\\\/editar/,
    /monitoramento\\\/ambientes\\\/\(\[\^\/\]\+\)\\\/editar/,
    /administracao\\\/usuarios\\\/\(\[\^\/\]\+\)\\\/editar/,
    /operacao\\\/fornecedores\\\/\(\[\^\/\]\+\)\\\/editar/,
    /operacao\\\/sla\\\/\(\[\^\/\]\+\)\\\/editar/,
    /administracao\\\/agentes\\\/\(\[\^\/\]\+\)\\\/editar/,
    /configuracoes\\\/notificacoes\\\/\(\[\^\/\]\+\)\\\/editar/,
    /administracao\\\/janelas-manutencao\\\/\(\[\^\/\]\+\)\\\/editar/,
    /empresas\\\/\(\[\^\/\]\+\)\\\/editar/,
    /monitoramento\\\/alertas\\\/\(\[\^\/\]\+\)\\\/editar/,
    /operacao\\\/catalogo\\\/\(\[\^\/\]\+\)\\\/nova-versao/,
  ]) assert.match(source, pattern);
  assert.match(source, /`\/administracao\/equipes\/\$\{encodeURIComponent\(record\.id\)\}\/editar`/);
  assert.match(source, /`\/monitoramento\/servicos\/\$\{encodeURIComponent\(monitor\.id\)\}\/editar`/);
  assert.match(source, /`\/monitoramento\/ambientes\/\$\{encodeURIComponent\(asset\.id\)\}\/editar`/);
  assert.match(source, /`\/administracao\/usuarios\/\$\{encodeURIComponent\(user\.id\)\}\/editar`/);
  assert.match(source, /`\/operacao\/fornecedores\/\$\{encodeURIComponent\(record\.id\)\}\/editar`/);
  assert.match(source, /`\/operacao\/sla\/\$\{encodeURIComponent\(record\.id\)\}\/editar`/);
  assert.match(source, /`\/administracao\/agentes\/\$\{encodeURIComponent\(agent\.id\)\}\/editar`/);
  assert.match(source, /`\/configuracoes\/notificacoes\/\$\{encodeURIComponent\(rule\.id\)\}\/editar`/);
  assert.match(source, /`\/administracao\/janelas-manutencao\/\$\{encodeURIComponent\(window\.id\)\}\/editar`/);
  assert.match(source, /`\/empresas\/\$\{encodeURIComponent\(record\.id\)\}\/editar`/);
  assert.match(source, /`\/monitoramento\/alertas\/\$\{encodeURIComponent\(alert\.id\)\}\/editar`/);
});

test("shared form primitives are used by operational pilot forms", () => {
  for (const component of ["FormPage", "FormSection", "FormActions"]) {
    assert.match(source, new RegExp(`function ${component}\\(`));
  }
  assert.match(source, /title=\{editingMonitor \? "Editar serviço" : "Novo serviço"\}/);
  assert.match(source, /title=\{editingAsset \? "Editar ambiente" : "Novo ambiente"\}/);
  assert.match(source, /if \(monitorSubmitting\) return/);
  assert.match(source, /if \(assetSubmitting\) return/);
  assert.match(source, /if \(userSubmitting\) return/);
  assert.match(source, /if \(supplierSubmitting \|\| !tenantId\) return/);
  assert.match(source, /if\(slaSubmitting\|\|!tenantId\)return/);
  assert.match(source, /if\(agentSubmitting\|\|!tenantId\)return/);
  assert.match(source, /if\(ruleSubmitting\|\|!tenantId\)return/);
  assert.match(source, /if\(maintenanceSubmitting\|\|!tenantId\)return/);
  assert.match(source, /if\(catalogSubmitting\|\|!tenantId\)return/);
  assert.match(source, /if\(integrationSubmitting\|\|!tenantId\)return/);
  assert.match(source, /if\(operationalSubmitting\)return/);
});

test("form layout stacks and exposes touch-sized actions on mobile", () => {
  assert.match(css, /@media \(max-width: 767px\)/);
  assert.match(css, /\.form-section-grid \{ grid-template-columns: 1fr; \}/);
  assert.match(css, /\.form-actions button \{ width: 100%; min-height: 44px; \}/);
});

test("CD-08 ERP confirmation is an addressable form without native prompt", () => {
  assert.match(source, /operacao\\\/pre-faturamento\\\/\(\[\^\/\]\+\)\\\/confirmar-erp/);
  assert.match(source, /`\/operacao\/pre-faturamento\/\$\{encodeURIComponent\(cycle\.id\)\}\/confirmar-erp`/);
  assert.match(source, /title="Confirmar retorno do ERP"/);
  assert.match(source, /if \(billingSubmitting \|\| !tenantId\) return/);
  assert.doesNotMatch(source, /ask\("Referência do ERP"\)/);
});

test("CD-09 quarantine mapping uses an explicit addressable asset selection", () => {
  assert.match(source, /monitoramento\\\/quarentena\\\/\(\[\^\/\]\+\)\\\/mapear/);
  assert.match(source, /`\/monitoramento\/quarentena\/\$\{encodeURIComponent\(event\.id\)\}\/mapear`/);
  assert.match(source, /title="Mapear evento em quarentena"/);
  assert.match(source, /if \(quarantineSubmitting \|\| !tenantId\) return/);
  assert.doesNotMatch(source, /ask\("ID do ativo Nexora para mapear"\)/);
});

test("destructive actions use the accessible application confirmation dialog", () => {
  assert.match(source, /role="alertdialog"/);
  assert.match(source, /aria-labelledby="delete-title"/);
  assert.match(source, /if \(!pendingDelete \|\| deleteSubmitting\) return/);
  assert.doesNotMatch(source, /confirm\(`Excluir/);
});

test("frontend does not use native prompt or confirm dialogs", () => {
  assert.doesNotMatch(source, /\bask\(/);
  assert.doesNotMatch(source, /\bconfirm\(/);
  assert.doesNotMatch(source, /\bprompt\(/);
  assert.match(source, /label="Destino do teste"/);
});

test("legacy grid creation forms were migrated to addressable pages", () => {
  assert.doesNotMatch(source, /className="service-create-form/);
  for (const title of [
    "Novo chamado",
    "Novo vínculo de ativo",
    "Nova relação de serviço",
    "Nova concessão de acesso",
    "Configurar liberação gradual",
  ]) assert.ok(source.includes(`title="${title}"`), `missing dedicated form ${title}`);
});

test("CD-04 T30 exposes reconciliation freshness and unresolved drift", () => {
  assert.match(source, /key:"last_reconciled_at",label:"Última reconciliação"/);
  assert.match(source, /key:"open_reconciliation_findings",label:"Divergências"/);
  assert.match(source, /sem divergências/);
});

test("CD-07 queue administration uses dedicated addressable forms", () => {
  assert.ok(source.includes('queues: "/operacao/filas"'));
  assert.ok(source.includes('path === "/operacao/filas/nova"'));
  assert.match(source, /operacao\\\/filas\\\/\(\[\^\/\]\+\)\\\/editar/);
  assert.match(source, /title=\{editingQueue\.id \? "Editar fila de atendimento" : "Nova fila de atendimento"\}/);
  assert.match(source, /if\(queueSubmitting\|\|!tenantId\)return/);
  assert.match(source, /key:"backlog",label:"Backlog"/);
});

test("OIDC authorization uses PKCE, state and nonce and validates the returned nonce", () => {
  assert.match(source, /code_challenge_method: "S256"/);
  assert.match(source, /state: s, nonce: n/);
  assert.match(source, /parseToken\(t\.id_token\)\.nonce !== expectedNonce/);
  assert.match(source, /sessionStorage\.removeItem\("oidc_nonce"\)/);
});

test("CD-01 exposes and uses the shared shell, list primitives and interface states", () => {
  for (const component of [
    "AppShell", "PageHeader", "ListPage", "MetricGroup", "FilterBar", "DataGrid",
    "FormPage", "FormSection", "FormActions", "Loading", "NoResults", "Unavailable",
    "Conflict", "Forbidden", "SessionExpired",
  ]) assert.match(source, new RegExp(`function ${component}\\(`), `missing shared primitive ${component}`);
  assert.match(source, /<AppShell>/);
  assert.match(source, /view === "admin-pending" && <ListPage/);
  assert.match(source, /<MetricGroup items=/);
  assert.match(source, /<FilterBar>/);
  assert.match(source, /<Unavailable title="Não foi possível carregar a análise\."/);
  assert.match(css, /\.shared-filter-fields \{ display: grid;/);
  assert.match(css, /\.state-view \{ display: flex;/);
});

test("the uniform API error contract selects actionable shared states", () => {
  assert.match(source, /class ApiError extends Error/);
  assert.match(source, /correlationId: b\?\.correlation_id/);
  assert.match(source, /invalidFields: Array\.isArray\(b\?\.invalid_fields\)/);
  assert.match(source, /errorStatus === 401[\s\S]*<SessionExpired/);
  assert.match(source, /errorStatus === 403[\s\S]*<Forbidden/);
  assert.match(source, /errorStatus === 409[\s\S]*<Conflict/);
});

test("feature flags and capabilities control navigation, deep links and actions", () => {
  assert.match(source, /const viewPermissionRequirements = \{/);
  assert.match(source, /api\("\/api\/v1\/me\/context", token\)\.then\(setAuthContext\)/);
  assert.match(source, /configuredSubmenu\?\.filter\(\(\[sk\]\) => viewAvailable\(sk\)\)/);
  assert.match(source, /if \(tenantId && authContext && !viewAvailable\(nextView\)\)/);
  assert.match(source, /featureEnabled\("action:integration-command\.retry"\)/);
  assert.match(source, /<option value="view:billing"\/>/);
});

test("pilot lists restore filters, sorting, pagination and reading position", () => {
  assert.match(source, /function useSessionState\(key, initialValue\)/);
  assert.match(source, /nexora:list:teams:\$\{tenantId\}:search/);
  assert.match(source, /nexora:list:monitors:\$\{tenantId\}:filters/);
  assert.match(source, /nexora:list:monitors:\$\{tenantId\}:page/);
  assert.match(source, /nexora:list:alerts:\$\{tenantId\}:filters/);
  assert.match(source, /nexora:grid:\$\{stateKey\}:sort/);
  assert.match(source, /nexora:grid:\$\{stateKey\}:scroll/);
  assert.match(source, /stateKey=\{`\$\{tenantId\}:teams`\}/);
  assert.match(source, /stateKey=\{`\$\{tenantId\}:monitors`\}/);
});

test("CD-05 ticket detail and edit are directly addressable", () => {
  assert.ok(source.includes('path.match(/^\\/operacao\\/chamados\\/([^/]+)\\/editar$/)'));
  assert.ok(source.includes('path.match(/^\\/operacao\\/chamados\\/([^/]+)$/)'));
  assert.match(source, /ticketMode: "detail"/);
  assert.match(source, /ticketMode: "edit"/);
  assert.match(source, /`\/operacao\/chamados\/\$\{encodeURIComponent\(i\.id\)\}\$\{suffix\}`/);
  assert.match(source, /const closeIncidentDetail = \(\) =>/);
  assert.match(source, /history\.pushState\(\{\}, "", viewPaths\.incidents\)/);
});

test("CD-06 requester ticket detail is directly addressable", () => {
  assert.ok(source.includes('path.match(/^\\/portal\\/solicitacoes\\/([^/]+)$/)'));
  assert.match(source, /portalTicketId: decodeURIComponent/);
  assert.match(source, /const openPortalTicket = \(ticket, navigate = true\)/);
  assert.match(source, /`\/portal\/solicitacoes\/\$\{encodeURIComponent\(ticket\.id\)\}`/);
  assert.match(source, /const closePortalTicket = \(\) =>/);
});

test("CD-05 T02 has metrics, persistent filters and pagination", () => {
  assert.match(source, /nexora:list:incidents:\$\{tenantId\}:filters/);
  assert.match(source, /nexora:list:incidents:\$\{tenantId\}:page-size/);
  assert.match(source, /incidentPageCount = Math\.max/);
  assert.match(source, /pagedIncidents = filteredIncidents\.slice/);
  assert.match(source, /stateKey=\{`\$\{tenantId\}:incidents`\}/);
  assert.match(source, /label: "SLA vencido"/);
  assert.match(source, /Página anterior de chamados/);
  assert.match(source, /Próxima página de chamados/);
});

