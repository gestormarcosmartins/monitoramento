import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const tenantId = "11111111-1111-1111-1111-111111111111";
const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
const token = `${encode({ alg: "none", typ: "JWT" })}.${encode({ sub: "visual-test-master", name: "Teste Visual", email: "visual@nexora.test", exp: 4102444800 })}.signature`;
const fixture = (pathname) => {
  if (pathname.endsWith("/auth/me")) return { id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true };
  if (pathname.endsWith("/tenants")) return [{ id: tenantId, name: "Empresa Regras", slug: "empresa-regras", active: true, role: "master" }];
  return [];
};

async function openForm(page, width = 1280) {
  await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
  await page.addInitScript(({ accessToken, selectedTenant }) => {
    sessionStorage.setItem("access_token", accessToken);
    sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
    localStorage.setItem("nexora_tenant", selectedTenant);
  }, { accessToken: token, selectedTenant: tenantId });
  const posts = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/notification-rules") && request.method() === "POST") {
      posts.push(request.postDataJSON());
      return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "r1", ...request.postDataJSON() }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixture(url.pathname)) });
  });
  await page.goto("/configuracoes/notificacoes/nova");
  await expect(page.getByRole("heading", { name: "Nova regra de notificação" })).toBeVisible();
  return posts;
}

test("a regra oferece WhatsApp, com formato do destino e eventos escolhíveis", async ({ page }) => {
  await openForm(page);
  const canal = page.getByLabel("Canal");
  await expect(canal.locator("option")).toHaveText(["E-mail", "WhatsApp", "Webhook"]);
  await canal.selectOption("whatsapp");
  await expect(page.locator("#rule-target-hint")).toContainText("@g.us");
  // padrão novo: prioridade alta e só criado/resolvido
  await expect(page.getByLabel(/Prioridade/)).toHaveValue("high");
  await expect(page.getByLabel("Chamado criado (falha)")).toBeChecked();
  await expect(page.getByLabel("Chamado resolvido (normalizado)")).toBeChecked();
  await expect(page.getByLabel("Chamado em andamento")).not.toBeChecked();
});

test("WhatsApp rejeita destino fora do formato e envia número e grupo válidos", async ({ page }) => {
  const posts = await openForm(page);
  await page.getByLabel("Canal").selectOption("whatsapp");
  await page.getByLabel("Nome").fill("WhatsApp SIMM");
  await page.getByLabel("Destino").fill("abc");
  await page.getByRole("button", { name: "Criar regra" }).click();
  await page.waitForTimeout(300);
  expect(posts).toHaveLength(0); // bloqueado pela validação do formulário

  await page.getByLabel("Destino").fill("5511999999999");
  await page.getByRole("button", { name: "Criar regra" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ name: "WhatsApp SIMM", channel: "whatsapp", target: "5511999999999", minimum_priority: "high" });
  expect(posts[0].events).toEqual(["incident.created", "incident.resolved"]);
});

test("grupo de WhatsApp terminado em @g.us é aceito e os eventos marcados são enviados", async ({ page }) => {
  const posts = await openForm(page);
  await page.getByLabel("Canal").selectOption("whatsapp");
  await page.getByLabel("Nome").fill("Grupo");
  await page.getByLabel("Destino").fill("120363012345678901@g.us");
  await page.getByLabel("Chamado em andamento").check();
  await page.getByLabel("Chamado resolvido (normalizado)").uncheck();
  await page.getByRole("button", { name: "Criar regra" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].events.sort()).toEqual(["incident.created", "incident.in_progress"]);
});

test("sem nenhum evento marcado a regra não é criada e a tela explica", async ({ page }) => {
  const posts = await openForm(page);
  await page.getByLabel("Nome").fill("Sem eventos");
  await page.getByLabel("Destino").fill("alguem@empresa.com.br");
  await page.getByLabel("Chamado criado (falha)").uncheck();
  await page.getByLabel("Chamado resolvido (normalizado)").uncheck();
  await page.getByRole("button", { name: "Criar regra" }).click();
  await expect(page.getByRole("alert").getByText("Selecione pelo menos um evento para a regra.").first()).toBeVisible();
  expect(posts).toHaveLength(0);
});

for (const width of [320, 390, 1280]) {
  test(`formulário de regra acessível e sem rolagem horizontal a ${width}px`, async ({ page }) => {
    await openForm(page, width);
    await page.getByLabel("Canal").selectOption("whatsapp");
    const wide = await page.evaluate(() => [...document.querySelectorAll("body *")].filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !el.closest("#main-navigation")).map((el) => `${el.tagName}.${el.className}`).slice(0, 5));
    expect(wide).toEqual([]);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(result.violations).toEqual([]);
  });
}
