import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const widths = [320, 360, 390, 768, 1024, 1366, 1920];
const tenantId = "11111111-1111-1111-1111-111111111111";
const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
const token = `${encode({ alg: "none", typ: "JWT" })}.${encode({
  sub: "visual-test-master",
  name: "Teste Visual",
  email: "visual@nexora.test",
  exp: 4102444800,
})}.signature`;

const fixture = (pathname) => {
  if (pathname.endsWith("/auth/me")) return { id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true };
  if (pathname.endsWith("/tenants")) return [{ id: tenantId, name: "Empresa Responsiva", slug: "empresa-responsiva", active: true, role: "master" }];
  if (pathname.includes("/dashboard")) return { totals: { tenants: 1, active_assets: 3, open_alerts: 2, open_incidents: 1 }, companies: [{ id: tenantId, name: "Empresa Responsiva", slug: "empresa-responsiva", services: 3, open_alerts: 2, open_incidents: 1 }] };
  return [];
};

for (const width of widths) {
  test(`shell Nexora remains usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await page.addInitScript(({ accessToken, selectedTenant }) => {
      sessionStorage.setItem("access_token", accessToken);
      sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
      localStorage.setItem("nexora_tenant", selectedTenant);
    }, { accessToken: token, selectedTenant: tenantId });
    await page.route("**/api/v1/**", async (route) => {
      const url = new URL(route.request().url());
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixture(url.pathname)) });
    });
    await page.goto("/visao-geral");
    await expect(page.locator(".app-shell")).toBeVisible();
    await expect(page.locator("main.content")).toBeVisible();
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("navigation", { name: "Navegação principal" })).toHaveCount(1);
    await expect(page.getByText("Visão Geral", { exact: true }).last()).toBeVisible();
    const accessibility = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blockingViolations = accessibility.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact),
    );
    expect(blockingViolations, `WCAG violations at ${width}px`).toEqual([]);
    const overflow = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      content: Math.max(0, document.querySelector("main.content").scrollWidth - document.querySelector("main.content").clientWidth),
    }));
    expect(overflow.document, `document overflow at ${width}px`).toBeLessThanOrEqual(1);
    expect(overflow.content, `content overflow at ${width}px`).toBeLessThanOrEqual(1);
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toHaveCount(1);
    const focusStyle = await focused.evaluate((element) => ({
      tag: element.tagName,
      outline: getComputedStyle(element).outlineStyle,
    }));
    expect(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"]).toContain(focusStyle.tag);
    expect(focusStyle.outline).not.toBe("none");
    if (width <= 390) {
      const menuButton = page.locator("button.mobile-menu-button");
      await expect(menuButton).toBeVisible();
      const box = await menuButton.boundingBox();
      expect(box?.width || 0).toBeGreaterThanOrEqual(44);
      expect(box?.height || 0).toBeGreaterThanOrEqual(44);
      await menuButton.focus();
      await page.keyboard.press("Enter");
      await expect(menuButton).toHaveAttribute("aria-expanded", "true");
      await expect(page.locator("#main-navigation")).toHaveClass(/mobile-open/);
      await page.keyboard.press("Escape");
      await expect(menuButton).toHaveAttribute("aria-expanded", "false");
      await expect(page.locator("#main-navigation")).not.toHaveClass(/mobile-open/);
    }
  });
}

test("a delayed response from the previous company cannot replace the active company", async ({ page }) => {
  const companyA = "11111111-1111-1111-1111-111111111111";
  const companyB = "22222222-2222-2222-2222-222222222222";
  let releaseCompanyA;
  let markCompanyARequested;
  const companyARequested = new Promise((resolve) => { markCompanyARequested = resolve; });
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: companyA });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/auth/me")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true }) });
    }
    if (url.pathname.endsWith("/tenants")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([
        { id: companyA, name: "Empresa Anterior", active: true, role: "master" },
        { id: companyB, name: "Empresa Atual", active: true, role: "master" },
      ]) });
    }
    if (url.pathname.endsWith("/alerts")) {
      const requestedCompany = request.headers()["x-tenant-id"];
      if (requestedCompany === companyA) {
        markCompanyARequested();
        await new Promise((resolve) => {
          releaseCompanyA = async () => {
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: "a", title: "Alerta da empresa anterior", severity: "critical", status: "open" }]) });
            resolve();
          };
        });
        return;
      }
      if (requestedCompany === companyB) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: "b", title: "Alerta da empresa atual", severity: "info", status: "open" }]) });
      }
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/monitoramento/alertas");
  await companyARequested;
  await page.getByLabel("Selecionar empresa").selectOption(companyB);
  await expect(page.getByText("Alerta da empresa atual", { exact: true })).toBeVisible();
  await releaseCompanyA();
  await page.waitForTimeout(100);
  await expect(page.getByText("Alerta da empresa atual", { exact: true })).toBeVisible();
  await expect(page.getByText("Alerta da empresa anterior", { exact: true })).toHaveCount(0);
});

test("quality and profitability keep missing bases explicit instead of zero", async ({ page }) => {
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: tenantId });
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/auth/me")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true }) });
    if (pathname.endsWith("/tenants")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: tenantId, name: "Empresa Responsiva", active: true, role: "master" }]) });
    if (pathname.endsWith("/quality")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ metrics: { sample: 0, first_response_sla_pct: null, resolution_sla_pct: null, reopened: 0 }, csat: { sample: 0, average: null }, source: "Eventos confirmados", definition: "Bases ausentes permanecem nulas.", cutoff: "2026-10-11T12:00:00Z" }) });
    if (pathname.endsWith("/profitability")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{ id: "contract-1", competence: "2026-10", name: "Contrato sem base", revenue: null, cost: null, margin: null, sample: 1 }], data_as_of: "2026-10-11T12:00:00Z", definition: "Margem depende de bases confiáveis." }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/operacao/qualidade");
  await expect(page.getByText("CSAT (0 respostas)", { exact: true })).toBeVisible();
  await expect(page.getByText("—", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Fonte:", { exact: true })).toBeVisible();
  await page.goto("/operacao/rentabilidade");
  await expect(page.getByText("Contrato sem base", { exact: true })).toBeVisible();
  await expect(page.getByText("Base ausente", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Base insuficiente", { exact: true })).toBeVisible();
});

test("a quality source failure is visible and never replaced by synthetic indicators", async ({ page }) => {
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: tenantId });
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/auth/me")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true }) });
    if (pathname.endsWith("/tenants")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: tenantId, name: "Empresa Responsiva", active: true, role: "master" }]) });
    if (pathname.endsWith("/quality")) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fonte de indicadores indisponível" }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/operacao/qualidade");
  await expect(page.getByRole("alert")).toContainText("Fonte de indicadores indisponível");
  await expect(page.getByText("100", { exact: true })).toHaveCount(0);
});

test("ticket detail and edit open directly and return to the list route", async ({ page }) => {
  const ticketId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: tenantId });
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/auth/me")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true }) });
    if (pathname.endsWith("/me/context")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { is_master: true }, companies: [{ id: tenantId }], permissions_by_company: { [tenantId]: [] }, feature_modes_by_company: { [tenantId]: {} } }) });
    if (pathname.endsWith("/tenants")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: tenantId, name: "Empresa Responsiva", active: true, role: "master" }]) });
    if (pathname.endsWith(`/incidents/${ticketId}/detail`)) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ incident: { id: ticketId, title: "Falha endereçável", status: "new", priority: "high", version: 1 }, allowed_tabs: ["service", "history"], comments: [], history: [] }) });
    if (pathname.endsWith(`/portal/tickets/${ticketId}`)) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ticket: { id: ticketId, title: "Solicitação endereçável", status: "new", description: "Detalhe público" }, entries: [], solutions: [] }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto(`/operacao/chamados/${ticketId}`);
  await expect(page.getByRole("heading", { name: "Falha endereçável" })).toBeVisible();
  await page.goto(`/operacao/chamados/${ticketId}/editar`);
  await expect(page.getByRole("heading", { name: "Editar chamado" })).toBeVisible();
  await expect(page.getByText("Falha endereçável", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /Voltar para incidentes/ }).click();
  await expect(page).toHaveURL(/\/incidentes$/);
  await page.goto(`/portal/solicitacoes/${ticketId}`);
  await expect(page.getByRole("heading", { name: "Solicitação endereçável" })).toBeVisible();
  await page.getByRole("button", { name: /Voltar/ }).click();
  await expect(page).toHaveURL(/\/portal\/solicitacoes$/);
});

test("ticket list paginates and restores the current page after viewing details", async ({ page }) => {
  const tickets = Array.from({ length: 23 }, (_, index) => ({
    id: `ticket-${String(index + 1).padStart(2, "0")}`,
    title: `Chamado ${String(index + 1).padStart(2, "0")}`,
    status: "new",
    priority: index === 0 ? "critical" : "medium",
    sla_status: index === 1 ? "breached" : "on_track",
    sync_status: "synced",
    version: 1,
  }));
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: tenantId });
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/auth/me")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true }) });
    if (pathname.endsWith("/me/context")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { is_master: true }, companies: [{ id: tenantId }], permissions_by_company: { [tenantId]: [] }, feature_modes_by_company: { [tenantId]: {} } }) });
    if (pathname.endsWith("/tenants")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: tenantId, name: "Empresa Responsiva", active: true, role: "master" }]) });
    if (pathname.endsWith("/incidents")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(tickets) });
    const detailMatch = pathname.match(/\/incidents\/(ticket-\d+)\/detail$/);
    if (detailMatch) {
      const ticket = tickets.find((item) => item.id === detailMatch[1]);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ incident: ticket, allowed_tabs: ["service"], comments: [] }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/incidentes");
  await expect(page.getByText("Mostrando 1 a 10 de 23 registros", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Próxima página de chamados" }).click();
  await expect(page.getByText("Mostrando 11 a 20 de 23 registros", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Detalhes" }).first().click();
  await expect(page).toHaveURL(/\/operacao\/chamados\/ticket-11$/);
  await page.getByRole("button", { name: /Voltar para incidentes/ }).click();
  await expect(page.getByText("Mostrando 11 a 20 de 23 registros", { exact: true })).toBeVisible();
});

test("returning from team edit restores list filters sorting and reading position", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 500 });
  const teams = Array.from({ length: 30 }, (_, index) => ({
    id: `team-${index + 1}`,
    name: `Equipe ${String(index + 1).padStart(2, "0")}`,
    active: index % 3 !== 0,
    emails: [`equipe${index + 1}@nexora.test`],
    whatsapp_numbers: [],
    whatsapp_group_ids: [],
    member_user_ids: [],
    member_names: "",
  }));
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: tenantId });
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/auth/me")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true }) });
    if (pathname.endsWith("/me/context")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { is_master: true }, companies: [{ id: tenantId }], permissions_by_company: { [tenantId]: [] }, feature_modes_by_company: { [tenantId]: {} } }) });
    if (pathname.endsWith("/tenants")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: tenantId, name: "Empresa Responsiva", active: true, role: "master" }]) });
    if (pathname.endsWith("/teams")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(teams) });
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/administracao/equipes");
  await page.addStyleTag({ content: ".team-list-page .grid-wrap { max-height: 280px; overflow-y: auto; }" });
  await page.getByLabel("Buscar").fill("Equipe");
  await page.getByLabel("Situação").selectOption("active");
  await page.locator(".team-list-page .sort-button").first().click();
  await page.locator(".team-list-page .grid-wrap").evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const before = await page.locator(".team-list-page .grid-wrap").evaluate((element) => element.scrollTop);
  expect(before).toBeGreaterThan(0);
  const scrollStorageKey = `nexora:grid:${tenantId}:teams:scroll`;
  await expect.poll(() => page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) || "{}")?.grid || 0, scrollStorageKey)).toBeGreaterThan(0);
  await page.getByRole("button", { name: /Editar equipe Equipe 02/ }).click();
  await expect(page).toHaveURL(/\/administracao\/equipes\/team-2\/editar$/);
  await page.getByRole("button", { name: /Voltar/ }).click();
  await expect(page).toHaveURL(/\/administracao\/equipes$/);
  await expect(page.getByLabel("Buscar")).toHaveValue("Equipe");
  await expect(page.getByLabel("Situação")).toHaveValue("active");
  await expect(page.locator(".team-list-page th[aria-sort]").first()).toHaveAttribute("aria-sort", "descending");
  await expect.poll(() => page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) || "{}")?.grid || 0, scrollStorageKey)).toBeGreaterThan(0);
  await expect.poll(() => page.locator(".team-list-page .grid-wrap").evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});
