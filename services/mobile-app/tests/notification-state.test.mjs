import assert from 'node:assert/strict';
import test from 'node:test';
import { applyRead, itemKey, markTenantsRead, unreadCount, visibleItems } from '../src/notification-state.ts';

const items = () => [
  { id: 'n1', tenant_id: 'A', opened_at: null },
  { id: 'n2', tenant_id: 'A', opened_at: null },
  { id: 'n3', tenant_id: 'B', opened_at: null },
  { id: 'n4', tenant_id: 'B', opened_at: '2026-10-15T10:00:00Z' },
];

test('conta só as pendentes', () => assert.equal(unreadCount(items()), 3));

test('marcar uma como lida tira só ela das pendentes e não mexe nas outras', () => {
  const next = applyRead(items(), [itemKey({ id: 'n1', tenant_id: 'A' })], '2026-10-15T12:00:00Z');
  assert.equal(unreadCount(next), 2);
  assert.equal(next[0].opened_at, '2026-10-15T12:00:00Z');
  assert.equal(next[1].opened_at, null);
});

test('mesmo id em empresas diferentes não se confunde', () => {
  const list = [{ id: 'x', tenant_id: 'A', opened_at: null }, { id: 'x', tenant_id: 'B', opened_at: null }];
  assert.equal(unreadCount(applyRead(list, ['A:x'])), 1);
});

test('é idempotente: repetir não muda a data de leitura de quem já estava lido', () => {
  const once = applyRead(items(), ['A:n1'], 'T1');
  const twice = applyRead(once, ['A:n1'], 'T2');
  assert.equal(twice[0].opened_at, 'T1');
  assert.equal(twice[3].opened_at, '2026-10-15T10:00:00Z');
});

test('leitura em lote só vale nas empresas confirmadas pelo servidor', () => {
  const next = markTenantsRead(items(), ['A'], 'T');
  assert.equal(unreadCount(next), 1); // a B (não confirmada) continua pendente
  assert.equal(next[2].opened_at, null);
});

test('lote sem nenhuma confirmação não marca nada (falha de rede não perde estado)', () => {
  assert.equal(unreadCount(markTenantsRead(items(), [])), 3);
});

test('lista de pendentes esvazia depois da leitura em lote total', () => {
  const next = markTenantsRead(items(), ['A', 'B']);
  assert.deepEqual(visibleItems(next, 'unread'), []);
  assert.equal(visibleItems(next, 'all').length, 4); // nada é apagado, só sai das pendentes
});
