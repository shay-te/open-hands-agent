import assert from 'node:assert/strict';
import test from 'node:test';

import { cx } from './cx.js';


test('cx: joins truthy parts with a single space', () => {
  assert.equal(cx('a', 'b', 'c'), 'a b c');
});

test('cx: drops falsy parts (false, "", null, undefined, 0)', () => {
  assert.equal(cx('a', false, '', null, undefined, 0, 'b'), 'a b');
});

test('cx: supports the conditional idioms', () => {
  const active = true;
  const kind = 'tool';
  assert.equal(cx('bubble', active && 'is-active', kind ? `bubble-${kind}` : ''),
    'bubble is-active bubble-tool');
});

test('cx: empty / all-falsy yields empty string', () => {
  assert.equal(cx(), '');
  assert.equal(cx(false, null, ''), '');
});
