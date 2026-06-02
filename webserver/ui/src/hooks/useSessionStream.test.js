// Adversarial regression tests for ``useSessionStream`` reducer bugs
// surfaced by the deep audit:
//
// Bug A: ``ACTION_LIFECYCLE`` transitioning to CLOSED / IDLE / MISSING
//        must reset ``turnInFlight`` to false. Otherwise the
//        WorkingIndicator stays "Claude is thinking…" forever on a
//        subprocess that has actually died, with no way for the
//        operator to recover except a full tab restart.
//
// Bug B: On reconnect (tab remount, SSE re-open), the hook dispatches
//        ``ACTION_HYDRATE`` with the cached state — but currently
//        forces ``lifecycle: CONNECTING`` regardless of what the
//        cache said. If the cache was STREAMING (subprocess alive,
//        mid-turn) the user sees a spurious "Connecting…" banner
//        before any new events arrive. Status should reflect what's
//        actually true; a remount during a live stream should remain
//        STREAMING until proven otherwise.

import assert from 'node:assert/strict';
import test from 'node:test';

import { SESSION_LIFECYCLE, reducer } from './useSessionStream.js';


// ---------------------------------------------------------------------------
// Bug A: turnInFlight stuck on CLOSED / IDLE / MISSING
// ---------------------------------------------------------------------------

function _midTurnState() {
  return {
    events: [],
    lifecycle: SESSION_LIFECYCLE.STREAMING,
    turnInFlight: true,
    pendingPermission: null,
    lastEventAt: Date.now(),
    streamGeneration: 0,
  };
}

test('Bug A: turnInFlight resets to false when lifecycle goes CLOSED', function () {
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.CLOSED });
  assert.equal(
    next.turnInFlight, false,
    'WorkingIndicator will stay "Claude is thinking…" on a dead subprocess',
  );
});

test('Bug A: turnInFlight resets to false when lifecycle goes IDLE', function () {
  // Subprocess exited cleanly without a final RESULT event (e.g., timeout
  // from kato's side). The UI must transition out of the "working" state.
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.IDLE });
  assert.equal(next.turnInFlight, false);
});

test('Bug A: turnInFlight resets to false when lifecycle goes MISSING', function () {
  // Record disappeared from the server (very rare — manual cleanup,
  // disk wipe). UI must not pretend the tab is still working.
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.MISSING });
  assert.equal(next.turnInFlight, false);
});

test('Bug A: pendingPermission also clears on CLOSED (existing contract)', function () {
  // Don't regress the existing behavior in fixing turnInFlight: the
  // permission modal must still vanish when the session closes.
  const state = { ..._midTurnState(), pendingPermission: { request_id: 'r1' } };
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.CLOSED });
  assert.equal(next.pendingPermission, null);
});

test('Bug A: lifecycle CONNECTING does NOT touch turnInFlight (reconnect mid-turn)', function () {
  // Negative: only terminal lifecycle states should reset turnInFlight.
  // A reconnect during a turn must NOT pretend the turn ended.
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.CONNECTING });
  assert.equal(next.turnInFlight, true);
});

test('Bug A: lifecycle STREAMING does NOT touch turnInFlight', function () {
  // Negative: STREAMING preserves whatever turnInFlight was.
  const state = { ..._midTurnState(), turnInFlight: false };
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.STREAMING });
  assert.equal(next.turnInFlight, false);
});


// ---------------------------------------------------------------------------
// Bug B: HYDRATE with cached STREAMING shouldn't be silently overridden
// to CONNECTING. The hook currently does:
//   dispatch({ type: HYDRATE, value: { ...cached, lifecycle: CONNECTING }})
// which means even a cache that says "the session is alive mid-stream"
// becomes "Connecting…" on every remount. This test pins the reducer
// contract: HYDRATE preserves whatever value is passed. The hook fix
// (in useSessionStream's useEffect) will pass the cached lifecycle
// when it's STREAMING.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Bug: PERMISSION_RESPONSE clears pendingPermission on mismatched/empty id.
// A synthetic or malformed response (request_id='') would wipe a legitimate
// pending modal, leaving the operator without a way to approve.
// ---------------------------------------------------------------------------

test('Bug PERM-1: PERMISSION_RESPONSE with empty id does NOT clear matched pending', function () {
  const stateWithPending = {
    events: [],
    eventKeys: new Set(),
    lifecycle: SESSION_LIFECYCLE.STREAMING,
    turnInFlight: false,
    pendingPermission: { type: 'permission_request', request_id: 'req-A', tool_name: 'Bash' },
    lastEventAt: Date.now(),
    streamGeneration: 0,
  };
  // Unrelated synthetic response with no request_id.
  const next = reducer(stateWithPending, {
    type: 'incoming_event',
    event: { type: 'permission_response', request_id: '' },
    receivedAtEpoch: Date.now(),
  });
  assert.deepEqual(
    next.pendingPermission?.request_id, 'req-A',
    'permission for req-A should still be pending — an empty-id response is not a match',
  );
});

test('Bug PERM-1: PERMISSION_RESPONSE with WRONG id does NOT clear pending', function () {
  const stateWithPending = {
    events: [],
    eventKeys: new Set(),
    lifecycle: SESSION_LIFECYCLE.STREAMING,
    turnInFlight: false,
    pendingPermission: { type: 'permission_request', request_id: 'req-A', tool_name: 'Bash' },
    lastEventAt: Date.now(),
    streamGeneration: 0,
  };
  const next = reducer(stateWithPending, {
    type: 'incoming_event',
    event: { type: 'permission_response', request_id: 'req-B' },
    receivedAtEpoch: Date.now(),
  });
  assert.deepEqual(
    next.pendingPermission?.request_id, 'req-A',
    'mismatched response should not clear the pending of a different request',
  );
});

test('PERMISSION_RESPONSE with MATCHING id clears pending (normal case)', function () {
  const stateWithPending = {
    events: [],
    eventKeys: new Set(),
    lifecycle: SESSION_LIFECYCLE.STREAMING,
    turnInFlight: false,
    pendingPermission: { type: 'permission_request', request_id: 'req-A', tool_name: 'Bash' },
    lastEventAt: Date.now(),
    streamGeneration: 0,
  };
  const next = reducer(stateWithPending, {
    type: 'incoming_event',
    event: { type: 'permission_response', request_id: 'req-A' },
    receivedAtEpoch: Date.now(),
  });
  assert.equal(next.pendingPermission, null);
});


// ---------------------------------------------------------------------------
// Working status must kick in at session start, not lag until the first
// ``assistant`` event. Autonomous task prompts go to Claude's stdin (never
// echoed as ``user``) and partial ``stream_event`` deltas are disabled, so
// ``system/init`` is the earliest wire signal that a turn has begun.
// ---------------------------------------------------------------------------

function _freshState() {
  return {
    events: [],
    eventKeys: new Set(),
    lifecycle: SESSION_LIFECYCLE.STREAMING,
    turnInFlight: false,
    pendingPermission: null,
    lastEventAt: Date.now(),
    streamGeneration: 0,
  };
}

test('system/init flips turnInFlight true so "working" shows at session start', function () {
  const next = reducer(_freshState(), {
    type: 'incoming_event',
    event: { type: 'system', subtype: 'init', session_id: 'b5e62b1c' },
    receivedAtEpoch: Date.now(),
  });
  assert.equal(next.turnInFlight, true);
});

test('system/preflight does NOT flip turnInFlight (still provisioning)', function () {
  const next = reducer(_freshState(), {
    type: 'incoming_event',
    event: { type: 'system', subtype: 'preflight', message: 'cloning 1/3' },
    receivedAtEpoch: Date.now(),
  });
  assert.equal(next.turnInFlight, false);
});

test('idle-session reconnect settles back to idle: init then result', function () {
  // Backlog replay flows through the same live (incoming_event) path and
  // always ends with the turn's ``result``. The transient init→true must
  // be cleared by the trailing result so a reconnect to a finished session
  // does not get stuck showing "working".
  const afterInit = reducer(_freshState(), {
    type: 'incoming_event',
    event: { type: 'system', subtype: 'init' },
    receivedAtEpoch: Date.now(),
  });
  assert.equal(afterInit.turnInFlight, true);
  const afterResult = reducer(afterInit, {
    type: 'incoming_event',
    event: { type: 'result' },
    receivedAtEpoch: Date.now(),
  });
  assert.equal(afterResult.turnInFlight, false);
});


test('Bug B: HYDRATE preserves the lifecycle value it is given', function () {
  // The reducer itself is correct — it simply replaces state with the
  // hydrated value. The bug is in the *caller* (useEffect). This test
  // documents the reducer contract: pass in what you want, get it back.
  const cached = _midTurnState();
  const next = reducer({}, { type: 'hydrate', value: cached });
  assert.equal(next.lifecycle, SESSION_LIFECYCLE.STREAMING);
  assert.equal(next.turnInFlight, true);
});


// ---------------------------------------------------------------------------
// Bug C: the chat transcript shrinks / blanks on tab switch ("history prompt
// disappears when switching between tasks").
//
// ``appendEntryIfNew`` mutates the ``eventKeys`` Set IN PLACE, and the module
// cache stores reducer state BY REFERENCE — so a cached snapshot's ``events``
// array can lag its (shared, already-advanced) Set when a newer append's state
// object never reached the async cache-write effect before the tab unmounted.
// On switch-back HYDRATE restored that snapshot verbatim, and the phantom keys
// made the server's history re-replay dedupe AWAY the very entries the snapshot
// was missing — so past prompts vanished with nothing to restore them. HYDRATE
// must rebuild eventKeys from the hydrated events so the replay can re-add them.
// ---------------------------------------------------------------------------

function _historyRaw(uuid) {
  return { uuid, type: 'assistant', message: { id: uuid } };
}

function _emptyState() {
  return {
    events: [], eventKeys: new Set(), lifecycle: SESSION_LIFECYCLE.CONNECTING,
    turnInFlight: false, pendingPermission: null, lastEventAt: 0,
  };
}

test('Bug C: HYDRATE rebuilds eventKeys from events so a drifted cache cannot blank the re-replay', function () {
  // Build a real one-entry history snapshot via the reducer, then simulate the
  // shared-Set drift: a later append (h2) mutated the SAME Set in place, but
  // this older cached snapshot's events array only ever held h1.
  const cached = reducer(_emptyState(), { type: 'incoming_history', event: _historyRaw('h1') });
  assert.deepEqual(cached.events.map((e) => e.raw.uuid), ['h1']);
  cached.eventKeys.add('history:u:h2'); // phantom key — events still only h1

  // Switch back → hydrate from the drifted cache.
  let state = reducer(cached, {
    type: 'hydrate',
    value: { ...cached, lifecycle: SESSION_LIFECYCLE.CONNECTING },
  });
  // The server re-replays the FULL history (h1 + h2) on reconnect.
  state = reducer(state, { type: 'incoming_history', event: _historyRaw('h1') });
  state = reducer(state, { type: 'incoming_history', event: _historyRaw('h2') });

  assert.deepEqual(
    state.events.map((e) => e.raw.uuid), ['h1', 'h2'],
    'h2 must be restored by the replay — a phantom cached key must not blank it',
  );
});

test('Bug C: a consistent cached history still dedupes on re-replay (no doubling)', function () {
  // Companion guarantee: when the cache is consistent, re-replaying the same
  // frames must NOT double them.
  let cached = reducer(_emptyState(), { type: 'incoming_history', event: _historyRaw('h1') });
  cached = reducer(cached, { type: 'incoming_history', event: _historyRaw('h2') });

  let state = reducer(cached, {
    type: 'hydrate',
    value: { ...cached, lifecycle: SESSION_LIFECYCLE.CONNECTING },
  });
  state = reducer(state, { type: 'incoming_history', event: _historyRaw('h1') });
  state = reducer(state, { type: 'incoming_history', event: _historyRaw('h2') });

  assert.deepEqual(state.events.map((e) => e.raw.uuid), ['h1', 'h2']);
});
