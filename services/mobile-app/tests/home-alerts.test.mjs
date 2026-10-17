import test from 'node:test';
import assert from 'node:assert/strict';
import { SEVERITY_CARDS, cardOf, toggleCard, severityParam, homeListParams, homeCountParams } from '../src/home-alerts.ts';

test('há quatro cards: Críticos, Alto, Médio e Baixo, nessa ordem', () => {
  assert.deepEqual(SEVERITY_CARDS.map((c) => c.label), ['Críticos', 'Alto', 'Médio', 'Baixo']);
});

test('ao abrir nada está selecionado e a lista traz todos os abertos', () => {
  assert.equal(severityParam([]), null);
  const params = homeListParams({});
  assert.match(params, /status=open/);
  assert.match(params, /sort=severity/);
  assert.doesNotMatch(params, /severity=/);
});

test('clicar seleciona e clicar de novo desmarca; vários cards se somam', () => {
  let selected = toggleCard([], 'critical');
  assert.deepEqual(selected, ['critical']);
  selected = toggleCard(selected, 'medium');
  assert.deepEqual(selected, ['critical', 'medium']);
  selected = toggleCard(selected, 'critical');
  assert.deepEqual(selected, ['medium']);
  assert.deepEqual(toggleCard(selected, 'medium'), []);
});

test('a severidade enviada ao servidor é a união dos cards, na ordem fixa', () => {
  assert.equal(severityParam(['low', 'critical']), 'disaster,warning,info');
  assert.equal(severityParam(['high']), 'high');
  assert.equal(severityParam(['medium']), 'average');
  assert.match(homeListParams({ selected: ['critical', 'high'] }), /severity=disaster%2Chigh/);
});

test('a lista da Início nunca pede alertas resolvidos ou ignorados', () => {
  for (const selected of [[], ['critical'], ['low', 'medium', 'high', 'critical']]) {
    assert.match(homeListParams({ selected }), /status=open/);
    assert.doesNotMatch(homeListParams({ selected }), /status=all|status=resolved|status=ignored/);
  }
});

test('escopo de empresa e cursor entram na consulta; "all" não restringe empresa', () => {
  assert.match(homeListParams({ scope: 'abc' }), /tenant_ids=abc/);
  assert.doesNotMatch(homeListParams({ scope: 'all' }), /tenant_ids/);
  assert.match(homeListParams({ cursor: 'x y' }), /cursor=x%20y/);
});

test('o número de cada card conta só abertos daquela severidade e não depende da seleção', () => {
  assert.equal(homeCountParams('critical'), 'page_size=1&status=open&severity=disaster');
  assert.equal(homeCountParams('low', 'abc'), 'page_size=1&status=open&severity=warning%2Cinfo&tenant_ids=abc');
});

test('toda severidade do servidor cai em exatamente um card', () => {
  for (const [severity, key] of [['disaster', 'critical'], ['high', 'high'], ['average', 'medium'], ['warning', 'low'], ['info', 'low'], ['desconhecida', 'low']]) {
    assert.equal(cardOf(severity).key, key, severity);
  }
});
