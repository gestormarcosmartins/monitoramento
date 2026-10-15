import test from 'node:test';
import assert from 'node:assert/strict';
import { passwordGrantBody, validateLoginForm, describeLoginFailure, clientForSession, tokenEndpoint } from '../src/password-login.ts';

test('corpo do grant usa o client mobile e só envia totp quando preenchido', () => {
  const sem = new URLSearchParams(passwordGrantBody(' ana ', 'p@ss word', ''));
  assert.equal(sem.get('client_id'), 'nexora-mobile');
  assert.equal(sem.get('grant_type'), 'password');
  assert.equal(sem.get('username'), 'ana');
  assert.equal(sem.get('password'), 'p@ss word');
  assert.equal(sem.has('totp'), false);
  assert.equal(new URLSearchParams(passwordGrantBody('ana', 'x', '123 456')).get('totp'), '123456');
});

test('validação do formulário', () => {
  assert.match(validateLoginForm('', 'x', '', false), /usuário/);
  assert.match(validateLoginForm('a', '', '', false), /senha/);
  assert.match(validateLoginForm('a', 'x', '12', true), /código/);
  assert.equal(validateLoginForm('a', 'x', '', false), null);
  assert.equal(validateLoginForm('a', 'x', '123456', true), null);
});

test('mensagens de erro amigáveis e sem vazar detalhe', () => {
  const bad = describeLoginFailure(401, { error: 'invalid_grant', error_description: 'Invalid user credentials' }, false);
  assert.equal(bad.needsOtp, true);
  assert.doesNotMatch(bad.message, /invalid_grant/);
  assert.equal(describeLoginFailure(401, { error: 'invalid_grant' }, true).needsOtp, false);
  assert.match(describeLoginFailure(400, { error: 'invalid_grant', error_description: 'Account is not fully set up' }, true).message, /navegador/);
  assert.match(describeLoginFailure(400, { error: 'invalid_grant', error_description: 'Account disabled' }, true).message, /bloqueada/);
  assert.match(describeLoginFailure(0, null, false).message, /conexão/);
  assert.match(describeLoginFailure(503, null, false).message, /indisponível/);
});

test('sessão antiga sem marca continua no client do navegador', () => {
  assert.equal(clientForSession(null), 'central-monitor');
  assert.equal(clientForSession('nexora-mobile'), 'nexora-mobile');
  assert.equal(tokenEndpoint('https://x.com/'), 'https://x.com/auth/realms/central-monitor/protocol/openid-connect/token');
});
