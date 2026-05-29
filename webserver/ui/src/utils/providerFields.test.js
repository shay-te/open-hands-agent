import assert from 'node:assert/strict';
import test from 'node:test';

import { isSecretKey, buildDraftFor } from './providerFields.js';


test('isSecretKey: true for token/secret/password keys (case-insensitive)', () => {
  assert.equal(isSecretKey('API_TOKEN'), true);
  assert.equal(isSecretKey('client_secret'), true);
  assert.equal(isSecretKey('Password'), true);
  assert.equal(isSecretKey('app_password_hash'), true);
});

test('isSecretKey: false for non-secret keys', () => {
  assert.equal(isSecretKey('host'), false);
  assert.equal(isSecretKey('username'), false);
  assert.equal(isSecretKey('base_url'), false);
});

test('buildDraftFor: maps each field key to its current value', () => {
  const providers = {
    bitbucket: { fields: { user: { value: 'me' }, token: { value: 't' } } },
  };
  assert.deepEqual(buildDraftFor(providers, 'bitbucket'), { user: 'me', token: 't' });
});

test('buildDraftFor: empty string for missing values; empty object for unknown provider', () => {
  const providers = { github: { fields: { token: {} } } };
  assert.deepEqual(buildDraftFor(providers, 'github'), { token: '' });
  assert.deepEqual(buildDraftFor(providers, 'nope'), {});
  assert.deepEqual(buildDraftFor(null, 'github'), {});
});
