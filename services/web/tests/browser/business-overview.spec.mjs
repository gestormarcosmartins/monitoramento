import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Visão Geral de Negócios: API simulada (nada é gravado nem consultado em produção).
const tenantId = "11111111-1111-1111-1111-111111111111";
const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
const token = `${encode({ alg: "none", typ: "JWT" })}.${encode({ sub: "visual-test-master", name: "Teste Visual", email: "visual@nexora.test", exp: 4102444800 })}.signature`;

const item = (over) => ({ id: "i", codigo: "x", nome: "X", descricao: "", unidade: "BRL", direcao: "MAIOR_PIORA", limiteAlerta: "10000.00", limiteCritico: "20000.00",
  versaoRegra: 1, origemRegra: "INDICADOR", configurada: true, validadeColetaSegundos: 1800, valorAtual: null, ultimoValorConhecido: null, coletadoEm: null, avaliadoEm: null,
  fonte: null, qualidadeColeta: "VALIDA", motivoQualidade: null, severidade: null, statusPrincipal: "NORMAL", estadoPersistido: null, acoesPermitidas: ["VISUALIZAR", "EDITAR"], ...over });
const items = [
  item({ id: "a", codigo: "ind_critico", nome: "Indicador crítico de teste", valorAtual: "49180486.93", ultimoValorConhecido: "49180486.93", coletadoEm: "2026-10-17T14:34:18-03:00", fonte: "Fonte de teste", severidade: "CRITICO", statusPrincipal: "CRITICO" }),
  item({ id: "b", codigo: "ind_antigo", nome: "Indicador desatualizado de teste", ultimoValorConhecido: "950.50", coletadoEm: "2026-10-14T11:58:06-03:00", qualidadeColeta: "DESATUALIZADA", statusPrincipal: "DESATUALIZADO", motivoQualidade: "Última coleta há 3 d, fora do prazo de validade de 30 min deste indicador." }),
  item({ id: "c", codigo: "ind_vazio", nome: "Indicador sem dados de teste", limiteAlerta: null, limiteCritico: null, qualidadeColeta: "SEM_DADOS", statusPrincipal: "SEM_DADOS", motivoQualidade: "Nenhuma coleta válida registrada para este indicador." }),
];
const totais = { total: 3, CRITICO: 1, ATENCAO: 0, DESATUALIZADO: 1, SEM_DADOS: 1, SEM_REGRA: 0, NORMAL: 0 };

for (const width of [360, 768, 1280]) {
  test(`Visão Geral de Negócios utilizável a ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 500 ? 800 : 900 });
    await page.addInitScript(({ accessToken, selectedTenant }) => {
      sessionStorage.setItem("access_token", accessToken);
      sessionStorage.setItem("refresh_token", "visual-test-refresh-token");
      localStorage.setItem("nexora_tenant", selectedTenant);
    }, { accessToken: token, selectedTenant: tenantId });
    const queries = [];
    await page.route("**/api/v1/**", async (route) => {
      const url = new URL(route.request().url());
      let body = [];
      if (url.pathname.endsWith("/auth/me")) body = { id: "visual-test-master", display_name: "Teste Visual", email: "visual@nexora.test", is_master: true };
      else if (url.pathname.endsWith("/tenants")) body = [{ id: tenantId, name: "Empresa Responsiva", slug: "empresa-responsiva", active: true, role: "master" }];
      else if (url.pathname.endsWith("/business/me")) body = { enabled: true, capabilities: ["business.view", "business.monitor.update"], scopes: {} };
      else if (url.pathname.endsWith("/business/overview")) {
        queries.push(url.search);
        const status = url.searchParams.get("status");
        const shown = status ? items.filter((i) => i.statusPrincipal === status) : items;
        body = { empresaId: tenantId, consultadoEm: "2026-10-17T18:00:00+00:00", ultimaAvaliacaoEm: "2026-10-17T17:59:00+00:00", indicadoresSuspensos: 0,
          ordemPadrao: [], totais, totalFiltrado: shown.length, pagina: 1, tamanhoPagina: 10, totalPaginas: 1, itens: shown };
      } else if (url.pathname.includes("/dashboard")) body = { totals: {}, companies: [] };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/negocios/visao-geral");
    await expect(page.getByRole("heading", { name: "Visão Geral de Negócios" })).toBeVisible();
    await expect(page.getByText("Empresa: Empresa Responsiva")).toBeVisible();
    await expect(page.getByText("R$ 49.180.486,93").first()).toBeVisible();
    await expect(page.getByText("Não configurado").first()).toBeVisible();
    await expect(page.getByText("Último valor conhecido").first()).toBeVisible();
    await expect(page.getByText("17/10/2026 14:34").first()).toBeVisible();

    // KPI filtra pelo servidor, mantém os totais globais e fica marcado como selecionado.
    const critical = page.getByRole("button", { name: /Críticos/ });
    await expect(critical).toHaveAttribute("aria-pressed", "false");
    await critical.click();
    await expect(critical).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Indicador desatualizado de teste")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Total/ })).toContainText("3");
    expect(queries.at(-1)).toContain("status=CRITICO");
    await expect(page.getByText("Status: Crítico")).toBeVisible();
    await page.getByRole("button", { name: "Limpar", exact: true }).click();
    await expect(page.getByText("Indicador desatualizado de teste")).toBeVisible();

    const overflow = await page.evaluate(() => {
      const limit = document.documentElement.clientWidth;
      const clipped = (el) => { for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) if (getComputedStyle(n).overflowX !== "visible") return true; return false; };
      const name = (el) => `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}.${String(el.getAttribute("class") || "").split(" ").join(".").slice(0, 50)}`;
      const culprits = [...document.querySelectorAll("body *")].filter((el) => el.getBoundingClientRect().right > limit + 1 && !clipped(el))
        .slice(0, 4).map((el) => `${name(el)} < ${name(el.parentElement)}`);
      return { extra: document.documentElement.scrollWidth - limit, culprits };
    });
    expect(overflow.extra, `overflow horizontal da página a ${width}px: ${overflow.culprits.join(" | ")}`).toBeLessThanOrEqual(1);
    for (const button of await page.locator(".metric-action, .bm-ov-open, .bm-pager button").all()) {
      const box = await button.boundingBox();
      expect(box?.height || 0).toBeGreaterThanOrEqual(43.5);
    }
    if (width <= 720) await expect(page.locator(".bm-ov-cards li")).toHaveCount(3);
    else await expect(page.locator("table.data-grid tbody tr")).toHaveCount(3);
    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(axe.violations.filter((v) => ["serious", "critical"].includes(v.impact)), `WCAG a ${width}px`).toEqual([]);
  });
}
