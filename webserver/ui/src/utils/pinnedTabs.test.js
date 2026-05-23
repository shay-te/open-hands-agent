// Unit tests for the pinned-tabs persistence helper. Pure functions,
// Map-backed storage stand-in — no jsdom, no DOM, no React.
//
// Runs on node:test (see package.json's ``test:node`` script). The
// helper is pure and matches the same convention as the
// composer-draft helper next to it.

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PINNED_TABS_STORAGE_KEY,
  isPinned,
  orderByPinned,
  readPinnedIds,
  togglePinned,
  writePinnedIds,
} from './pinnedTabs.js';


function fakeStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    _dump: () => Object.fromEntries(map),
  };
}


// ---------------------------------------------------------------------
// readPinnedIds
// ---------------------------------------------------------------------

test('readPinnedIds: returns [] when storage is null', () => {
  assert.deepEqual(readPinnedIds(null), []);
});

test('readPinnedIds: returns [] when key is missing', () => {
  assert.deepEqual(readPinnedIds(fakeStorage()), []);
});

test('readPinnedIds: returns [] when payload is malformed JSON', () => {
  const s = fakeStorage({ [PINNED_TABS_STORAGE_KEY]: '{not json' });
  assert.deepEqual(readPinnedIds(s), []);
});

test('readPinnedIds: returns [] when payload is not an array', () => {
  const s = fakeStorage({ [PINNED_TABS_STORAGE_KEY]: '"T1"' });
  assert.deepEqual(readPinnedIds(s), []);
});

test('readPinnedIds: preserves order of valid string ids', () => {
  const s = fakeStorage({
    [PINNED_TABS_STORAGE_KEY]: JSON.stringify(['T1', 'T2', 'T3']),
  });
  assert.deepEqual(readPinnedIds(s), ['T1', 'T2', 'T3']);
});

test('readPinnedIds: strips non-string and blank entries', () => {
  const s = fakeStorage({
    [PINNED_TABS_STORAGE_KEY]: JSON.stringify(
      ['T1', '', '  ', null, 42, { x: 1 }, 'T2'],
    ),
  });
  assert.deepEqual(readPinnedIds(s), ['T1', 'T2']);
});

test('readPinnedIds: deduplicates keeping first occurrence', () => {
  const s = fakeStorage({
    [PINNED_TABS_STORAGE_KEY]: JSON.stringify(['T1', 'T2', 'T1', 'T3', 'T2']),
  });
  assert.deepEqual(readPinnedIds(s), ['T1', 'T2', 'T3']);
});

test('readPinnedIds: swallows storage exceptions gracefully', () => {
  const s = { getItem: () => { throw new Error('storage off'); } };
  assert.deepEqual(readPinnedIds(s), []);
});


// ---------------------------------------------------------------------
// writePinnedIds
// ---------------------------------------------------------------------

test('writePinnedIds: persists a clean list', () => {
  const s = fakeStorage();
  writePinnedIds(['T1', 'T2'], s);
  assert.deepEqual(
    JSON.parse(s.getItem(PINNED_TABS_STORAGE_KEY)),
    ['T1', 'T2'],
  );
});

test('writePinnedIds: strips junk before writing', () => {
  const s = fakeStorage();
  writePinnedIds(['T1', '', null, 'T1', 42, 'T2'], s);
  assert.deepEqual(
    JSON.parse(s.getItem(PINNED_TABS_STORAGE_KEY)),
    ['T1', 'T2'],
  );
});

test('writePinnedIds: no-op when storage is unavailable', () => {
  assert.doesNotThrow(() => writePinnedIds(['T1'], null));
});

test('writePinnedIds: swallows quota errors silently', () => {
  const s = { setItem: () => { throw new Error('QuotaExceededError'); } };
  assert.doesNotThrow(() => writePinnedIds(['T1'], s));
});


// ---------------------------------------------------------------------
// isPinned
// ---------------------------------------------------------------------

test('isPinned: true when id is in the list', () => {
  assert.equal(isPinned('T1', ['T1', 'T2']), true);
});

test('isPinned: false when id is missing', () => {
  assert.equal(isPinned('T3', ['T1', 'T2']), false);
});

test('isPinned: false for blank id', () => {
  assert.equal(isPinned('', ['T1', 'T2']), false);
});

test('isPinned: false when list is empty or undefined', () => {
  assert.equal(isPinned('T1', []), false);
  assert.equal(isPinned('T1', undefined), false);
});


// ---------------------------------------------------------------------
// togglePinned
// ---------------------------------------------------------------------

test('togglePinned: appends an unpinned id to the end', () => {
  assert.deepEqual(togglePinned('T3', ['T1', 'T2']), ['T1', 'T2', 'T3']);
});

test('togglePinned: removes an already-pinned id', () => {
  assert.deepEqual(togglePinned('T2', ['T1', 'T2', 'T3']), ['T1', 'T3']);
});

test('togglePinned: returns a new array — does not mutate input', () => {
  const input = ['T1', 'T2'];
  const result = togglePinned('T3', input);
  assert.deepEqual(input, ['T1', 'T2']);
  assert.notStrictEqual(result, input);
});

test('togglePinned: ignores blank task id', () => {
  assert.deepEqual(togglePinned('', ['T1']), ['T1']);
  assert.deepEqual(togglePinned('   ', ['T1']), ['T1']);
});

test('togglePinned: handles undefined ids gracefully', () => {
  assert.deepEqual(togglePinned('T1', undefined), ['T1']);
});


// ---------------------------------------------------------------------
// orderByPinned
// ---------------------------------------------------------------------

const sessions = [
  { task_id: 'T1' },
  { task_id: 'T2' },
  { task_id: 'T3' },
  { task_id: 'T4' },
];

test('orderByPinned: empty pinned list preserves original order', () => {
  assert.deepEqual(
    orderByPinned(sessions, []).map((s) => s.task_id),
    ['T1', 'T2', 'T3', 'T4'],
  );
});

test('orderByPinned: pinned tasks come first in pin order', () => {
  assert.deepEqual(
    orderByPinned(sessions, ['T3', 'T1']).map((s) => s.task_id),
    ['T3', 'T1', 'T2', 'T4'],
  );
});

test('orderByPinned: unpinned tasks preserve their original relative order', () => {
  assert.deepEqual(
    orderByPinned(sessions, ['T2']).map((s) => s.task_id),
    ['T2', 'T1', 'T3', 'T4'],
  );
});

test('orderByPinned: stale pinned ids (no matching session) are skipped', () => {
  assert.deepEqual(
    orderByPinned(sessions, ['T99', 'T2', 'T-removed']).map((s) => s.task_id),
    ['T2', 'T1', 'T3', 'T4'],
  );
});

test('orderByPinned: handles empty / missing sessions', () => {
  assert.deepEqual(orderByPinned([], ['T1']), []);
  assert.deepEqual(orderByPinned(null, ['T1']), []);
});

test('orderByPinned: returns a new array (does not mutate input)', () => {
  const input = [...sessions];
  const result = orderByPinned(input, ['T2']);
  assert.deepEqual(
    input.map((s) => s.task_id),
    ['T1', 'T2', 'T3', 'T4'],
  );
  assert.notStrictEqual(result, input);
});


// ---------------------------------------------------------------------
// round-trip
// ---------------------------------------------------------------------

test('round-trip: toggle → write → read returns the same list', () => {
  const s = fakeStorage();
  let pinned = readPinnedIds(s);
  pinned = togglePinned('T1', pinned);
  pinned = togglePinned('T2', pinned);
  writePinnedIds(pinned, s);
  assert.deepEqual(readPinnedIds(s), ['T1', 'T2']);
});

test('round-trip: unpin → re-pin lands at the end (rightmost pinned)', () => {
  const s = fakeStorage();
  writePinnedIds(['T1', 'T2', 'T3'], s);
  let pinned = togglePinned('T1', readPinnedIds(s)); // unpin
  pinned = togglePinned('T1', pinned);               // re-pin
  writePinnedIds(pinned, s);
  assert.deepEqual(readPinnedIds(s), ['T2', 'T3', 'T1']);
});
