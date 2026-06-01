import assert from 'node:assert/strict';
import test from 'node:test';

import { readQueuedMessages, writeQueuedMessages } from './queuedMessagesStore.js';

function _items(n) {
  return Array.from({ length: n }, (_, i) => ({ id: `q-${i}`, text: `m${i}`, images: [] }));
}

test('read returns [] for an unknown / blank task', () => {
  assert.deepEqual(readQueuedMessages('never-seen'), []);
  assert.deepEqual(readQueuedMessages(''), []);
  assert.deepEqual(readQueuedMessages(null), []);
});

test('write then read round-trips per task (survives a "remount")', () => {
  const a = _items(2);
  writeQueuedMessages('TASK-A', a);
  // A different "mount" reading the same task gets the queue back.
  assert.deepEqual(readQueuedMessages('TASK-A'), a);
});

test('queues are isolated per task (task A never leaks into task B)', () => {
  writeQueuedMessages('TASK-A', _items(2));
  writeQueuedMessages('TASK-B', _items(1));
  assert.equal(readQueuedMessages('TASK-A').length, 2);
  assert.equal(readQueuedMessages('TASK-B').length, 1);
});

test('writing an empty queue drops the entry (no unbounded growth)', () => {
  writeQueuedMessages('TASK-C', _items(1));
  assert.equal(readQueuedMessages('TASK-C').length, 1);
  writeQueuedMessages('TASK-C', []);
  assert.deepEqual(readQueuedMessages('TASK-C'), []);
});

test('blank taskId writes are ignored', () => {
  writeQueuedMessages('', _items(1));
  assert.deepEqual(readQueuedMessages(''), []);
});
