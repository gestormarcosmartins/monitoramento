import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const widths = [320, 390, 768, 1280];
const tenantId = "11111111-1111-1111-1111-111111111111";
const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
const token = `${encode({ alg: "none", typ: "JWT" })}.${encode({ sub: "visual-test-master", name: "Teste Visual", email: "visual@nexora.test", exp: 4102444800 })}.signature`;
const notification = (n, over = {}) => ({
  id: `00000000-0000-0000-0000-00000000000${n}`, domain: "INFRA", severity: "P0", category: "indisponibilidade",
  title: "Nexora · Incidente P0", body_safe: "Novo incidente exige atenção. Toque para ver.", action_type: "INCIDENT",
  action_ref: "22222222-2222-2222-2222-222222222222", require_ack: true, created_at: "2026-10-14T10:00:00Z", expires_at: null,
  opened_at: null, acknowledged_at: null, ...over,
});
const items = [notification(1), notification(2, { severity: "P1", require_ack: false, opened_at: "2026-10-14T10:05:00Z" })];
const fixture = (pathname) => {
  if (pathname.endsWith("/auth/me")) return { id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true };
  if (pathname.endsWith("/tenants")) return [{ id: tenantId, name: "Empresa Responsiva", slug: "empresa-responsiva", active: true, role: "master" }];
  if (pathname.endsWith("/api/v1/notificacoes")) return { totals: { total: 2, critical: 2, unread: 1, awaiting_ack: 1 }, items };
  if (pathname.endsWith("/dispositivos-push")) return [{ id: "d1", platform: "android", app_version: "1.0.12", enabled: true, permission_status: "granted", last_seen_at: "2026-10-14T10:00:00Z", reason: null, token: "ExponentPushToken[…abcd" }];
  if (pathname.endsWith("/entregas")) return { summary: [{ state: "PROVIDER_ACCEPTED", total: 3 }, { state: "FAILED_FINAL", total: 1 }], queue_age_seconds: 0, items: [{ id: "e1", state: "PROVIDER_ACCEPTED", attempt_no: 1, provider_error_code: null, receipt_checked_at: "2026-10-14T10:20:00Z", created_at: "2026-10-14T10:00:00Z" }] };
  return [];
};

for (const width of widths) {
  test(`Central de notificações utilizável e acessível a ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await page.addInitScript(({ accessToken, selectedTenant }) => {
      sessionStorage.setItem("access_token", accessToken);
      sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
      localStorage.setItem("nexora_tenant", selectedTenant);
    }, { accessToken: token, selectedTenant: tenantId });
    await page.route("**/api/v1/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixture(new URL(route.request().url()).pathname)) }));

    await page.goto("/notificacoes/central");
    await expect(page.getByRole("heading", { name: "Central de notificações" })).toBeVisible();
    await expect(page.getByText("Aguardando confirmação", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Mostrando 1 a 2 de 2 registros")).toBeVisible();
    const wide = await page.evaluate(() => [...document.querySelectorAll("body *")].filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !el.closest(".grid-wrap") && !el.closest("#main-navigation")).map((el) => `${el.tagName}.${el.className}`).slice(0, 5));
    expect(wide, "elementos que estouram a largura (fora do grid)").toEqual([]);

    const central = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(central.violations).toEqual([]);

    await page.getByRole("button", { name: "Meus dispositivos" }).click();
    await expect(page.getByRole("heading", { name: "Meus dispositivos" })).toBeVisible();
    await expect(page.getByText("Android")).toBeVisible();

    await page.getByRole("button", { name: "Saúde das entregas" }).click();
    await expect(page.getByRole("heading", { name: "Saúde das entregas" })).toBeVisible();
    const deliveries = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(deliveries.violations).toEqual([]);
  });
}
