import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { agentStatusStore } from './agentStatusStore.js';

beforeEach(() => { agentStatusStore.clearAll(); });

// Read the current snapshot by subscribing once and immediately unsubscribing
// (subscribe fires the snapshot synchronously — the pubsub contract).
function _snapshot() {
  let snap = null;
  const unsub = agentStatusStore.subscribe((s) => { snap = s; });
  unsub();
  return snap;
}

test('setStatus stores an entry and emits a new snapshot reference', () => {
  const seen = [];
  const unsub = agentStatusStore.subscribe((s) => seen.push(s));
  agentStatusStore.setStatus('T1', { lifecycle: 'closed' });
  unsub();
  assert.equal(agentStatusStore.getStatus('T1').lifecycle, 'closed');
  // initial fire + one emit
  assert.equal(seen.length, 2);
  assert.notEqual(seen[0], seen[1]); // new top-level reference on change
});

test('setStatus is a no-op (no emit) when nothing changed', () => {
  agentStatusStore.setStatus('T1', { lifecycle: 'idle', turnInFlight: false });
  let emits = 0;
  const unsub = agentStatusStore.subscribe(() => { emits += 1; });
  // first call is the initial subscribe fire
  agentStatusStore.setStatus('T1', { lifecycle: 'idle', turnInFlight: false });
  unsub();
  assert.equal(emits, 1); // only the initial fire; the equal setStatus did not emit
});

test('pendingPermission is coerced to a boolean', () => {
  agentStatusStore.setStatus('T1', { lifecycle: 'streaming', pendingPermission: { tool: 'bash' } });
  assert.equal(agentStatusStore.getStatus('T1').pendingPermission, true);
  agentStatusStore.setStatus('T1', { lifecycle: 'streaming', pendingPermission: 0 });
  assert.equal(agentStatusStore.getStatus('T1').pendingPermission, false);
});

test('getStatus returns null for unknown task', () => {
  assert.equal(agentStatusStore.getStatus('nope'), null);
  assert.equal(agentStatusStore.getStatus(''), null);
});

test('clearStatus removes ONLY that task, leaves others, emits once', () => {
  agentStatusStore.setStatus('A', { lifecycle: 'closed' });
  agentStatusStore.setStatus('B', { lifecycle: 'streaming' });
  let emits = 0;
  const unsub = agentStatusStore.subscribe(() => { emits += 1; });
  agentStatusStore.clearStatus('A');
  unsub();
  assert.equal(agentStatusStore.getStatus('A'), null);
  assert.equal(agentStatusStore.getStatus('B').lifecycle, 'streaming');
  assert.equal(emits, 2); // initial fire + the clear
});

test('clearStatus on unknown / blank key is a no-op (no emit)', () => {
  agentStatusStore.setStatus('A', { lifecycle: 'idle' });
  let emits = 0;
  const unsub = agentStatusStore.subscribe(() => { emits += 1; });
  agentStatusStore.clearStatus('missing');
  agentStatusStore.clearStatus('');
  unsub();
  assert.equal(emits, 1); // only the initial fire
});

test('setStatus ignores a blank taskId', () => {
  agentStatusStore.setStatus('', { lifecycle: 'closed' });
  assert.deepEqual(_snapshot(), {});
});

test('a throwing subscriber cannot break other subscribers or setStatus', () => {
  const good = [];
  agentStatusStore.subscribe(() => { throw new Error('boom'); });
  agentStatusStore.subscribe((s) => good.push(s));
  assert.doesNotThrow(() => agentStatusStore.setStatus('T1', { lifecycle: 'closed' }));
  assert.ok(good.length >= 1);
});
