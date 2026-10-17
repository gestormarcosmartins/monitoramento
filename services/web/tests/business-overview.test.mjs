import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import {PAGE_SIZES, STATUS_ORDER, activeFilters, buildKpis, buildQuery, describeValue, formatDateTime, formatDecimal, formatIndicatorValue,
  formatLimit, pageRange, sourceLine, statusMeta} from "../src/business-overview-lib.js";

const screen = fs.readFileSync(new URL("../src/business-overview.jsx", import.meta.url), "utf8");
const shell = fs.readFileSync(new URL("../src/business-monitor.jsx", import.meta.url), "utf8");
const main = fs.readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");

test("BRL em pt-BR com moeda, sem passar por float", () => {
  assert.equal(formatIndicatorValue("49180486.93", "BRL"), "R$ 49.180.486,93");
  assert.equal(formatIndicatorValue("950.50", "BRL"), "R$ 950,50");
  assert.equal(formatIndicatorValue("20000", "R$"), "R$ 20.000,00");
  assert.equal(formatIndicatorValue("0", "BRL"), "R$ 0,00");
  assert.equal(formatIndicatorValue("-1234.5", "BRL"), "-R$ 1.234,50");
  assert.equal(formatIndicatorValue("1234.565", "BRL"), "R$ 1.234,57");           // meio arredonda para longe do zero
  assert.equal(formatIndicatorValue("9007199254740993.01", "BRL"), "R$ 9.007.199.254.740.993,01"); // além da precisão de um float
  assert.doesNotMatch(formatIndicatorValue("49180486.93", "BRL"), /49180486/);
});

test("outras unidades não inventam símbolo nem trocam a posição", () => {
  assert.equal(formatIndicatorValue("1234.5", "unidades"), "1.234,5 unidades");
  assert.equal(formatIndicatorValue("12.50", "%"), "12,5%");
  assert.equal(formatIndicatorValue("3", "dias"), "3 dias");
  assert.equal(formatIndicatorValue("1000", ""), "1.000");
  assert.equal(formatIndicatorValue("1000", null), "1.000");
  assert.equal(formatIndicatorValue("0.123456", "kg"), "0,123456 kg");
  assert.doesNotMatch(formatIndicatorValue("10", "unidades"), /R\$|\$/);
});

test("valor ausente ou inválido é traço, nunca zero", () => {
  for (const v of [null, undefined, "", "abc", "1,5", "1e3"]) assert.equal(formatIndicatorValue(v, "BRL"), "—");
  assert.equal(formatDecimal("-0.001", {minFrac: 2, maxFrac: 2}), "0,00"); // sem "-0,00"
});

test("limite não cadastrado é 'Não configurado', e zero é um limite válido", () => {
  assert.equal(formatLimit(null, "BRL"), "Não configurado");
  assert.equal(formatLimit(undefined, "BRL"), "Não configurado");
  assert.equal(formatLimit("0.00", "BRL"), "R$ 0,00");
  assert.equal(formatLimit("10000.00", "BRL"), "R$ 10.000,00");
});

test("datas dd/mm/aaaa hh:mm no horário de Brasília", () => {
  assert.equal(formatDateTime("2026-10-17T14:34:18-03:00"), "17/10/2026 14:34");
  assert.equal(formatDateTime("2026-10-14T14:58:06Z"), "14/10/2026 11:58");
  assert.equal(formatDateTime("2026-10-17T00:05:00-03:00"), "17/10/2026 00:05");
  for (const v of [null, undefined, "", "ontem"]) assert.equal(formatDateTime(v), "—");
});

const valid = {statusPrincipal: "CRITICO", unidade: "BRL", valorAtual: "49180486.93", ultimoValorConhecido: "49180486.93", coletadoEm: "2026-10-17T14:34:18-03:00"};

test("só leitura válida é 'Valor atual'; sem dados e desatualizado mostram o último valor conhecido com rótulo e data", () => {
  assert.deepEqual(describeValue(valid), {label: "Valor atual", text: "R$ 49.180.486,93", lastKnown: null});
  const stale = describeValue({statusPrincipal: "DESATUALIZADO", unidade: "BRL", valorAtual: null, ultimoValorConhecido: "950.50", coletadoEm: "2026-10-14T11:58:06-03:00"});
  assert.equal(stale.text, "—");
  assert.deepEqual(stale.lastKnown, {label: "Último valor conhecido", text: "R$ 950,50", at: "14/10/2026 11:58"});
  const none = describeValue({statusPrincipal: "SEM_DADOS", unidade: "BRL", valorAtual: null, ultimoValorConhecido: null, coletadoEm: null});
  assert.deepEqual(none, {label: "Valor atual", text: "—", lastKnown: null});
  // Sem regra tem leitura válida: o valor é exibido
  assert.equal(describeValue({statusPrincipal: "SEM_REGRA", unidade: "unidades", valorAtual: "5.00", ultimoValorConhecido: "5.00"}).text, "5 unidades");
});

test("fonte e última coleta como texto secundário", () => {
  assert.equal(sourceLine({fonte: "Contas a receber", coletadoEm: "2026-10-17T14:34:18-03:00"}), "Fonte: Contas a receber · Última coleta: 17/10/2026 14:34");
  assert.equal(sourceLine({fonte: null, coletadoEm: null}), "Última coleta: —");
});

test("KPIs: total confere com a soma, extras só quando existem e sempre mantêm o total global", () => {
  const totais = {total: 9, CRITICO: 3, ATENCAO: 1, DESATUALIZADO: 1, SEM_DADOS: 1, SEM_REGRA: 1, NORMAL: 2};
  const kpis = buildKpis(totais);
  assert.deepEqual(kpis.map((k) => k.label), ["Total", "Críticos", "Em atenção", "Normais", "Sem dados", "Desatualizados", "Sem regra"]);
  assert.equal(kpis[0].value, 9);
  assert.equal(kpis.slice(1).reduce((n, k) => n + k.value, 0), kpis[0].value);
  const base = buildKpis({total: 2, CRITICO: 1, ATENCAO: 0, DESATUALIZADO: 0, SEM_DADOS: 1, SEM_REGRA: 0, NORMAL: 0});
  assert.deepEqual(base.map((k) => k.label), ["Total", "Críticos", "Em atenção", "Normais", "Sem dados"]);
  assert.equal(base.find((k) => k.label === "Em atenção").value, 0);   // zero confirmado aparece como 0
  assert.ok(buildKpis({total: 1, NORMAL: 1}, "SEM_REGRA").some((k) => k.label === "Sem regra")); // filtro ativo mantém o cartão
  assert.deepEqual(buildKpis(null), []);
});

test("consulta ao servidor: busca, status e paginação 10/25/50", () => {
  assert.equal(buildQuery(), "page=1&page_size=10");
  assert.equal(buildQuery({q: " vencido ", status: "CRITICO", page: 2, pageSize: 25}), "q=vencido&status=CRITICO&page=2&page_size=25");
  assert.equal(buildQuery({status: "QUALQUER", page: 0, pageSize: 7}), "page=1&page_size=10");
  assert.deepEqual(PAGE_SIZES, [10, 25, 50]);
  assert.deepEqual(STATUS_ORDER, ["CRITICO", "ATENCAO", "DESATUALIZADO", "SEM_DADOS", "SEM_REGRA", "NORMAL"]);
  assert.deepEqual(activeFilters({q: "x", status: "NORMAL"}).map((c) => c.label), ["Busca: x", "Status: Normal"]);
  assert.deepEqual(activeFilters({}), []);
  assert.deepEqual(pageRange(3, 10, 23), {from: 21, to: 23, total: 23});
  assert.deepEqual(pageRange(1, 10, 0), {from: 0, to: 0, total: 0});
});

test("cada status tem texto próprio, ícone e tom (nunca só cor)", () => {
  const icons = new Set();
  for (const status of STATUS_ORDER) {
    const meta = statusMeta(status);
    assert.ok(meta.label && meta.icon && meta.tone, status);
    icons.add(meta.icon);
  }
  assert.equal(icons.size, STATUS_ORDER.length);
  assert.equal(statusMeta("ATENCAO").label, "Em atenção");
  assert.equal(statusMeta("???").label, "Indefinido");
});

test("a tela reutiliza os componentes compartilhados e segue o contrato de listagem", () => {
  assert.match(screen, /ListPage/);
  assert.match(screen, /MetricGroup/);
  assert.match(screen, /FilterBar/);
  assert.match(screen, /DataGrid sortable=\{false\}/);       // ordem e paginação vêm do servidor
  assert.match(screen, /\/api\/v1\/business\/overview/);
  assert.match(screen, /Tentar novamente/);
  assert.match(screen, /Limpar/);
  assert.match(screen, /Remover filtro/);
  assert.match(screen, /não são em tempo real/);
  assert.match(screen, /Última avaliação/);
  assert.match(screen, /bm-ov-skeleton/);
  assert.match(screen, /aria-live="polite"/);
  for (const column of ["Indicador", "Valor atual", "Alerta", "Crítico", "Status", "Última coleta"]) assert.ok(screen.includes(`label: '${column}'`), column);
  assert.match(screen, /Itens por página/);
  assert.match(screen, /setTimeout\(\(\) => setFilters[^\n]*300\)/); // busca com ~300 ms
});

test("a tela não reimplementa a classificação nem usa modal, painel lateral ou dados de exemplo", () => {
  const lib = fs.readFileSync(new URL("../src/business-overview-lib.js", import.meta.url), "utf8");
  for (const source of [screen, lib]) {
    assert.doesNotMatch(source, /(limite\w+|valorAtual)\s*(>=|<=|>|<)\s*\w/);   // comparar limite e valor é papel do backend
    assert.doesNotMatch(source, /role="dialog"|aria-modal|window\.(confirm|prompt|alert)|Drawer|drawer/);
  }
  assert.doesNotMatch(screen, /49180486|vencido_90|contas_vencidas/);
});

test("KPI clicável usa aria-pressed e o shell abre a edição existente em página própria", () => {
  assert.match(main, /aria-pressed=\{Boolean\(item\.selected\)\}/);
  assert.match(main, /sortable = true/);
  assert.match(shell, /BusinessOverview/);
  assert.match(shell, /openEdit\(item,'visao-geral'\)/);
  assert.match(shell, /monitoramentos\/\$\{i\.id\}\/editar/);
  assert.match(shell, /backToList/);
});

test("ações vêm do servidor: sem EDITAR não há botão nem clique de linha", () => {
  assert.match(screen, /acoesPermitidas \|\| \[\]\)\.includes\('EDITAR'\)/);
  assert.match(screen, /canOpen\(item\) && onOpen\(item\)/);
});
