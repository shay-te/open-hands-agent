import assert from 'node:assert/strict';
import test from 'node:test';

import { apiErrorMessage } from './apiError.js';


test('apiErrorMessage: prefers body.error over top-level error', () => {
  assert.equal(
    apiErrorMessage({ body: { error: 'from body' }, error: 'from top' }, 'fb'),
    'from body',
  );
});

test('apiErrorMessage: falls back to top-level error when no body.error', () => {
  assert.equal(apiErrorMessage({ error: 'transport boom' }, 'fb'), 'transport boom');
  assert.equal(apiErrorMessage({ body: {}, error: 'boom' }, 'fb'), 'boom');
});

test('apiErrorMessage: uses fallback when neither is set', () => {
  assert.equal(apiErrorMessage({ body: {} }, 'save failed'), 'save failed');
  assert.equal(apiErrorMessage({}, 'load failed'), 'load failed');
  assert.equal(apiErrorMessage(null, 'load failed'), 'load failed');
  assert.equal(apiErrorMessage(undefined, 'load failed'), 'load failed');
});

test('apiErrorMessage: empty fallback yields empty string, never throws', () => {
  assert.equal(apiErrorMessage(null), '');
  assert.equal(apiErrorMessage({}), '');
});

test('apiErrorMessage: coerces non-string error to string', () => {
  assert.equal(apiErrorMessage({ error: 500 }, 'fb'), '500');
});
