import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveStorage } from './storage.js';


test('resolveStorage: returns null when window is undefined (node / SSR)', () => {
  // node:test runs without a DOM, so window is undefined here.
  assert.equal(typeof window === 'undefined', true);
  assert.equal(resolveStorage(), null);
});

test('resolveStorage: returns window.localStorage when present', () => {
  const fake = { getItem() {}, setItem() {}, removeItem() {} };
  globalThis.window = { localStorage: fake };
  try {
    assert.equal(resolveStorage(), fake);
  } finally {
    delete globalThis.window;
  }
});

test('resolveStorage: returns null when window has no localStorage', () => {
  globalThis.window = {};
  try {
    assert.equal(resolveStorage(), null);
  } finally {
    delete globalThis.window;
  }
});
