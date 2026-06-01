// Single source of truth for LIVE agent (Claude/Codex) status, keyed by task id.
//
// The header chip reads the live SSE stream (useSessionStream) for the active
// task, but the tab dot/badge only ever saw the 5s-polled /api/sessions fields
// — so they disagreed (UNA-2492: chip said "closed" while the tab said
// "working"). The active task's SessionDetail publishes its live stream state
// here; the tabs subscribe, so every agent-status surface derives from the same
// value via utils/agentStatus.js. Plain pub/sub (like toastStore) — no React,
// no context — so the producer and all consumers share one value.
//
// Holds ONLY agent-subprocess liveness. NOT the comment-run status
// (kato_status) and NOT the workspace/task status — those are different axes
// and stay where they are.

import { createPubSub } from './pubsub.js';

// { [taskId]: { lifecycle, turnInFlight, pendingPermission } } — the live SSE
// facts the active task's SessionDetail owns. ``needsAttention`` is NOT stored:
// every consumer already receives it as a prop (App's attention tracking), so
// it stays a render-time input to deriveAgentStatus rather than a stored fact.
let _statuses = {};

const _pubsub = createPubSub(() => _statuses);
const _emit = _pubsub.emit;

function sameStatus(a, b) {
  if (a === b) { return true; }
  if (!a || !b) { return false; }
  return a.lifecycle === b.lifecycle
    && a.turnInFlight === b.turnInFlight
    && a.pendingPermission === b.pendingPermission;
}

export const agentStatusStore = {
  subscribe: _pubsub.subscribe,

  // SessionDetail publishes the active task's live stream state. A no-op (no
  // emit) when nothing changed, so a re-render that recomputes the same value
  // can't cascade into a render loop. ``pendingPermission`` is coerced to a
  // boolean — surfaces only need "is there a pending request", and storing the
  // raw event object would defeat the equality guard (new identity each tick).
  setStatus(taskId, {
    lifecycle = '',
    turnInFlight = false,
    pendingPermission = false,
  } = {}) {
    if (!taskId) { return; }
    const next = {
      lifecycle,
      turnInFlight: !!turnInFlight,
      pendingPermission: !!pendingPermission,
    };
    if (sameStatus(_statuses[taskId], next)) { return; }
    _statuses = { ..._statuses, [taskId]: next };
    _emit();
  },

  getStatus(taskId) {
    return (taskId && _statuses[taskId]) || null;
  },

  // Remove ONLY this task's entry (SessionDetail unmount / tab switch). Other
  // tasks' entries are untouched. Emits only when something was removed.
  clearStatus(taskId) {
    if (!taskId || !(taskId in _statuses)) { return; }
    const next = { ..._statuses };
    delete next[taskId];
    _statuses = next;
    _emit();
  },

  clearAll() {
    if (Object.keys(_statuses).length === 0) { return; }
    _statuses = {};
    _emit();
  },
};
