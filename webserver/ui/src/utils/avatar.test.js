import { test } from 'node:test';
import assert from 'node:assert/strict';

import { avatarInitials, avatarColor } from './avatar.js';

test('avatarInitials takes first+last initial of a multi-word name', () => {
  assert.equal(avatarInitials('Shay Tessler'), 'ST');
  assert.equal(avatarInitials('operator'), 'OP');
  assert.equal(avatarInitials('reviewer-bot'), 'RB');
  assert.equal(avatarInitials('a.b.c'), 'AC');
});

test('avatarInitials handles blank / single-char input', () => {
  assert.equal(avatarInitials(''), '?');
  assert.equal(avatarInitials(null), '?');
  assert.equal(avatarInitials('x'), 'X');
});

test('avatarColor is deterministic and an hsl() string', () => {
  const a = avatarColor('Shay Tessler');
  assert.equal(a, avatarColor('Shay Tessler'));
  assert.match(a, /^hsl\(\d{1,3} 45% 38%\)$/);
  assert.notEqual(avatarColor('operator'), avatarColor('reviewer'));
});
