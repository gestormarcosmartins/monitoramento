import assert from 'node:assert/strict';
import test from 'node:test';
import { ALERT_FILTERS, ENVIRONMENT_FILTERS, SERVICE_FILTERS, alertBreakdown, checkedText, inventoryParams, inventoryStatusLabel, kindLabel, monitorTypeLabel, serviceBreakdown, MAX_BACKOFF_MS, POLL_MS, STATUS, ago, alertParams, alertStatusLabel, badgeFrom, nextDelay, serviceState, severityLabel } from '../src/monitoring-logic.ts';

const dashboard = (critical, attention) => ({ totals: { critical_alerts: critical, attention_alerts: attention } });

test('selo: sem dados ou sem alertas abertos nao mostra selo', () => {
  assert.deepEqual(badgeFrom(null), { count: 0, critical: 0, level: 'none' });
  assert.deepEqual(badgeFrom(undefined), { count: 0, critical: 0, level: 'none' });
  assert.equal(badgeFrom(dashboard(0, 0)).level, 'none');
});

test('selo: vermelho quando ha critico, amarelo so com atencao, e o numero soma os alertas abertos', () => {
  assert.deepEqual(badgeFrom(dashboard(2, 3)), { count: 5, critical: 2, level: 'critical' });
  assert.deepEqual(badgeFrom(dashboard(0, 4)), { count: 4, critical: 0, level: 'attention' });
});

test('selo: valores ausentes ou nulos contam como zero', () => {
  assert.equal(badgeFrom({ totals: { critical_alerts: null, attention_alerts: undefined } }).count, 0);
});

test('atualizacao: 60 s, dobra a cada falha ate 5 min e volta a 60 s no sucesso', () => {
  assert.equal(nextDelay(POLL_MS, true), 120_000);
  assert.equal(nextDelay(120_000, true), 240_000);
  assert.equal(nextDelay(240_000, true), MAX_BACKOFF_MS);
  assert.equal(nextDelay(MAX_BACKOFF_MS, true), MAX_BACKOFF_MS);
  assert.equal(nextDelay(MAX_BACKOFF_MS, false), POLL_MS);
});

test('servico: sem dados e inativo nunca sao saudaveis', () => {
  assert.equal(serviceState({ active: true, last_status: 'up' }).label, 'Saudável');
  assert.equal(serviceState({ active: true, last_status: 'down' }).label, 'Indisponível');
  assert.equal(serviceState({ active: true, last_status: 'unknown' }).label, 'Sem dados');
  assert.equal(serviceState({ active: true, last_status: 'qualquer-coisa' }).label, 'Sem dados');
  assert.equal(serviceState({ active: true, last_status: null }).label, 'Sem dados');
  assert.equal(serviceState({ active: false, last_status: 'up' }).label, 'Inativo');
  assert.notEqual(serviceState({ active: true, last_status: 'unknown' }).color, STATUS.healthy);
});

test('ha quanto tempo: agora, minutos, horas, dias e data invalida', () => {
  const now = Date.parse('2026-10-12T12:00:00Z');
  assert.equal(ago('2026-10-12T11:59:40Z', now), 'agora');
  assert.equal(ago('2026-10-12T11:45:00Z', now), 'há 15 min');
  assert.equal(ago('2026-10-12T09:00:00Z', now), 'há 3 h');
  assert.equal(ago('2026-10-09T12:00:00Z', now), 'há 3 d');
  assert.equal(ago('2026-10-12T12:30:00Z', now), 'agora', 'relogio adiantado nao gera tempo negativo');
  assert.equal(ago('nao-e-data', now), '—');
});

test('alertas: Com problemas lista so os abertos; Todos nao filtra o estado', () => {
  assert.equal(alertParams({ filter: 'problems' }), 'page_size=25&status=open');
  assert.equal(alertParams({ filter: 'all' }), 'page_size=25&status=all');
});

test('alertas: Criticos = high,disaster e Atencao = warning,average, sempre so abertos', () => {
  assert.equal(alertParams({ filter: 'critical' }), 'page_size=25&status=open&severity=high%2Cdisaster');
  assert.equal(alertParams({ filter: 'attention' }), 'page_size=25&status=open&severity=warning%2Caverage');
});

test('alertas: empresa unica envia tenant_ids; todas as empresas nao envia', () => {
  assert.match(alertParams({ filter: 'problems', scope: 'aaaa-1' }), /tenant_ids=aaaa-1$/);
  assert.doesNotMatch(alertParams({ filter: 'problems', scope: 'all' }), /tenant_ids/);
  assert.doesNotMatch(alertParams({ filter: 'problems', scope: '' }), /tenant_ids/);
});

test('alertas: busca e cursor sao codificados; busca em branco e ignorada', () => {
  assert.match(alertParams({ filter: 'problems', text: '  api & banco ' }), /q=api%20%26%20banco/);
  assert.doesNotMatch(alertParams({ filter: 'problems', text: '   ' }), /q=/);
  assert.match(alertParams({ filter: 'problems', cursor: 'abc=/+' }), /cursor=abc%3D%2F%2B$/);
  assert.match(alertParams({ filter: 'all', pageSize: 50 }), /^page_size=50&/);
});

test('alertas: filtros seguem a especificacao e nao incluem "Meus"', () => {
  assert.deepEqual(ALERT_FILTERS.map((item) => item.key), ['problems', 'critical', 'attention', 'all']);
  assert.ok(!ALERT_FILTERS.some((item) => /meus/i.test(item.label)));
});

test('alertas: severidade e estado sempre tem rotulo em texto', () => {
  assert.equal(severityLabel('critical').label, 'Crítico');
  assert.equal(severityLabel('attention').label, 'Atenção');
  assert.equal(severityLabel('info').label, 'Informativo');
  assert.equal(severityLabel('qualquer').label, 'Informativo');
  assert.equal(alertStatusLabel('open'), 'Aberto');
  assert.equal(alertStatusLabel('resolved'), 'Resolvido');
  assert.equal(alertStatusLabel('ignored'), 'Ignorado');
  assert.equal(alertStatusLabel('novo-estado'), 'novo-estado');
});

test('inventario: filtros de ambientes e servicos seguem a especificacao; desconhecido e separado de "Com problemas"', () => {
  assert.deepEqual(ENVIRONMENT_FILTERS.map((item) => item.key), ['problems', 'unavailable', 'critical', 'attention', 'unknown', 'healthy', 'all']);
  assert.deepEqual(SERVICE_FILTERS.map((item) => item.key), ['problems', 'unknown', 'healthy', 'inactive', 'all']);
  assert.ok(!ENVIRONMENT_FILTERS.find((item) => item.key === 'problems').values.includes('unknown'));
  assert.ok(!SERVICE_FILTERS.find((item) => item.key === 'problems').values.includes('unknown'));
});

test('inventario: parametros de ambientes usam status; os de servicos usam state', () => {
  assert.equal(inventoryParams({ kind: 'environments', filter: 'problems' }), 'page=1&page_size=25&status=unavailable%2Ccritical%2Cattention');
  assert.equal(inventoryParams({ kind: 'services', filter: 'problems' }), 'page=1&page_size=25&state=unavailable');
  assert.equal(inventoryParams({ kind: 'services', filter: 'all' }), 'page=1&page_size=25');
  assert.equal(inventoryParams({ kind: 'environments', filter: 'all', page: 3, pageSize: 50 }), 'page=3&page_size=50');
});

test('inventario: busca, empresa unica e ambiente (so servicos) sao enviados; todas as empresas nao envia tenant_ids', () => {
  assert.match(inventoryParams({ kind: 'services', filter: 'all', text: ' banco & fila ', scope: 'aaaa-1', assetId: 'ee-9' }), /asset_id=ee-9&q=banco%20%26%20fila&tenant_ids=aaaa-1$/);
  assert.doesNotMatch(inventoryParams({ kind: 'services', filter: 'all', scope: 'all' }), /tenant_ids/);
  assert.doesNotMatch(inventoryParams({ kind: 'environments', filter: 'all', assetId: 'ee-9' }), /asset_id/);
  assert.doesNotMatch(inventoryParams({ kind: 'environments', filter: 'all', text: '   ' }), /q=/);
});

test('inventario: todo estado tem rotulo em texto e desconhecido nunca vira saudavel', () => {
  const states = ['healthy', 'attention', 'critical', 'unavailable', 'unknown', 'inactive'];
  assert.deepEqual(states.map((state) => inventoryStatusLabel(state).label), ['Saudável', 'Atenção', 'Crítico', 'Indisponível', 'Sem dados', 'Inativo']);
  assert.equal(inventoryStatusLabel('qualquer-coisa').label, 'Sem dados');
  assert.equal(inventoryStatusLabel(undefined).label, 'Sem dados');
  assert.notEqual(inventoryStatusLabel('unknown').color, inventoryStatusLabel('healthy').color);
  assert.notEqual(inventoryStatusLabel('unavailable').color, inventoryStatusLabel('critical').color);
});

test('inventario: tipos, ultima coleta e resumos', () => {
  assert.equal(kindLabel('database'), 'Banco de dados');
  assert.equal(kindLabel('novo'), 'novo');
  assert.equal(monitorTypeLabel('event_deadman'), 'Sinal de vida');
  const now = Date.parse('2026-10-12T12:00:00Z');
  assert.equal(checkedText(null, now), 'Nunca coletado');
  assert.equal(checkedText('2026-10-12T09:00:00Z', now), 'Última coleta há 3 h');
  assert.equal(serviceBreakdown({ total: 0, up: 0, down: 0, unknown: 0, inactive: 0 }), 'Sem serviços');
  assert.equal(serviceBreakdown({ total: 8, up: 6, down: 1, unknown: 1, inactive: 0 }), '8 serviço(s) · 6 saudável(is) · 1 indisponível(is) · 1 sem dados');
  assert.equal(alertBreakdown({ critical: 0, attention: 0, info: 0 }), 'Sem alertas abertos');
  assert.equal(alertBreakdown({ critical: 2, attention: 1, info: 0 }), '2 crítico(s) · 1 de atenção');
});

import { NOC_FILTERS, filterNoc, nocPath, nocServiceLabel, nocStatusLabel } from '../src/monitoring-logic.ts';

const noc = [
  { status: 'critical', environment: 'Produção', company: 'Chaveou' },
  { status: 'attention', environment: 'Homologação', company: 'Chaveou' },
  { status: 'operational', environment: 'Intranet', company: 'Açaí Ltda' },
  { status: 'no_communication', environment: 'Filial', company: 'Açaí Ltda' },
];

test('NOC: caminho respeita o escopo (todas = sem tenant_ids)', () => {
  assert.equal(nocPath('all'), '/api/v1/dashboard/noc');
  assert.equal(nocPath(''), '/api/v1/dashboard/noc');
  assert.equal(nocPath('abc-1'), '/api/v1/dashboard/noc?tenant_ids=abc-1');
});

test('NOC: filtros por situacao', () => {
  assert.equal(filterNoc(noc, 'all').length, 4);
  assert.deepEqual(filterNoc(noc, 'problems').map((i) => i.status), ['critical', 'attention', 'no_communication']);
  assert.deepEqual(filterNoc(noc, 'critical').map((i) => i.environment), ['Produção']);
  assert.deepEqual(filterNoc(noc, 'no_communication').map((i) => i.environment), ['Filial']);
  assert.equal(NOC_FILTERS.length, 4);
});

test('NOC: busca ignora acentos e caixa, em ambiente e empresa', () => {
  assert.deepEqual(filterNoc(noc, 'all', 'producao').map((i) => i.environment), ['Produção']);
  assert.equal(filterNoc(noc, 'all', 'ACAI').length, 2);
  assert.equal(filterNoc(noc, 'problems', 'acai').length, 1);
  assert.equal(filterNoc(noc, 'all', 'zzz').length, 0);
});

test('NOC: rotulos sempre em texto; desconhecido vira sem comunicacao/sem dados', () => {
  assert.equal(nocStatusLabel('critical').label, 'Crítico');
  assert.equal(nocStatusLabel('operational').label, 'Operacional');
  assert.equal(nocStatusLabel('qualquer').label, 'Sem comunicação');
  assert.equal(nocServiceLabel('failed').label, 'Falha');
  assert.equal(nocServiceLabel(undefined).label, 'Sem dados');
});
