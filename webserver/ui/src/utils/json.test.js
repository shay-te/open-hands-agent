import assert from 'node:assert/strict';
import test from 'node:test';

import { parseJsonOr } from './json.js';


test('parseJsonOr: parses valid JSON', () => {
  assert.deepEqual(parseJsonOr('{"a":1}', null), { a: 1 });
  assert.deepEqual(parseJsonOr('[1,2,3]', null), [1, 2, 3]);
});

test('parseJsonOr: returns fallback on empty / nullish input', () => {
  assert.equal(parseJsonOr('', 'fb'), 'fb');
  assert.equal(parseJsonOr(null, 'fb'), 'fb');
  assert.equal(parseJsonOr(undefined, 'fb'), 'fb');
});

test('parseJsonOr: returns fallback on malformed JSON (never throws)', () => {
  assert.deepEqual(parseJsonOr('{not json', { ok: false }), { ok: false });
  assert.equal(parseJsonOr('undefined', 'fb'), 'fb');
});
