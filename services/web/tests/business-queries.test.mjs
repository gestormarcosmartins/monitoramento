import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import {parseColumns, parseParameters, columnsText} from "../src/business-queries-lib.js";

const source = fs.readFileSync(new URL("../src/business-queries.jsx", import.meta.url), "utf8");
const shell = fs.readFileSync(new URL("../src/business-monitor.jsx", import.meta.url), "utf8");

test("query monitor column and parameter parsing follow the server contract", () => {
  assert.deepEqual(parseColumns("documento:string\n valor_vencido : decimal \n").columns,
    [{name: "documento", type: "string"}, {name: "valor_vencido", type: "decimal"}]);
  assert.match(parseColumns("documento").error, /nome:tipo/);
  assert.match(parseColumns("a:money").error, /Linha inválida/);
  assert.match(parseColumns("1a:string").error, /Linha inválida/);
  assert.ok(parseColumns("").error);
  assert.deepEqual(parseParameters("").parameters, {});
  assert.deepEqual(parseParameters('{"hoje":"$today"}').parameters, {hoje: "$today"});
  assert.ok(parseParameters("[1]").error && parseParameters("{oops").error && parseParameters("5").error);
  assert.equal(columnsText([{name: "a", type: "string"}, {name: "b", type: "date"}]), "a:string\nb:date");
});

test("query monitor screens use dedicated addressable pages, never modals, prompts or sample data", () => {
  for (const route of ["consultas/novo", "revisao", "situacao", "execucoes", "workers/novo", "revogar", "fontes/novo", "reprocessar", "descartar", "atualizacoes/novo", "promover", "anel"]) {
    assert.ok(source.includes(route) || shell.includes(route), `route ${route}`);
  }
  assert.doesNotMatch(source, /window\.(confirm|prompt|alert)|\bconfirm\(|\bprompt\(|role="dialog"|aria-modal/);
  assert.match(source, /FormPage/);
  assert.match(source, /ListPage/);
  assert.match(source, /MetricGroup/);
  assert.match(source, /stateKey=/);
});

test("worker token is shown once and never persisted in browser storage", () => {
  assert.match(source, /exibido uma única vez/);
  assert.doesNotMatch(source, /(local|session)Storage[^\n]*(token|worker_token)/i);
});

test("the Master governance shell does not expose business data pages without business.view", () => {
  assert.match(shell, /governance && !has\(allowed,'business\.view'\)/);
  assert.match(shell, /Primeiro acesso/);
  assert.match(shell, /MasterQuickGrant/);
  assert.match(shell, /grant administrator without 'business\.view'/);
});
