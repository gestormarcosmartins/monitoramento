import assert from 'node:assert/strict';
import test from 'node:test';
import { needsCompanyChoice, unreadBadgeText, validSavedTenant } from '../src/session-logic.ts';

const two = [{ id: 'a' }, { id: 'b' }];

test('empresa lembrada que ainda existe volta direto, sem escolha', () => {
  assert.equal(validSavedTenant('b', two), 'b');
  assert.equal(needsCompanyChoice('b', two), false);
});

test('empresa revogada ou desconhecida nunca é reaproveitada', () => {
  assert.equal(validSavedTenant('c', two), null);
  assert.equal(needsCompanyChoice('c', two), true);
  assert.equal(validSavedTenant('c', [{ id: 'a' }]), null);
});

test('consolidado só vale com duas ou mais empresas', () => {
  assert.equal(validSavedTenant('all', two), 'all');
  assert.equal(validSavedTenant('all', [{ id: 'a' }]), null);
});

test('sem empresa lembrada: uma empresa entra direto, várias exigem escolha excepcional', () => {
  assert.equal(needsCompanyChoice(null, [{ id: 'a' }]), false);
  assert.equal(needsCompanyChoice(undefined, two), true);
  assert.equal(needsCompanyChoice(null, []), false);
});

test('contador do sino limita em 99+ e nunca fica negativo', () => {
  assert.equal(unreadBadgeText(0), '0');
  assert.equal(unreadBadgeText(7), '7');
  assert.equal(unreadBadgeText(99), '99');
  assert.equal(unreadBadgeText(100), '99+');
  assert.equal(unreadBadgeText(-3), '0');
});
