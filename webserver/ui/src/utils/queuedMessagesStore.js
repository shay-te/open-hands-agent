// Per-task outgoing-message (steer) queue, kept in a module-level Map so it
// survives SessionDetail remounting on task switch. SessionDetail is keyed by
// task (App.jsx), so React drops its local state when you switch tabs — which
// lost the operator's queued/steer messages ("steer messages disappear when
// moving between tasks"). Mirroring the queue here lets it restore on return.
//
// In-memory (NOT localStorage) on purpose: queued items can carry pasted images
// (base64 data URLs) that are too large to mirror to localStorage on every
// mutation. So the queue survives task switches within a session; a full page
// reload starts fresh (matching the live, ephemeral nature of a steer queue).

const _byTask = new Map();

export function readQueuedMessages(taskId) {
  if (!taskId) { return []; }
  const items = _byTask.get(taskId);
  return Array.isArray(items) ? items : [];
}

export function writeQueuedMessages(taskId, items) {
  if (!taskId) { return; }
  if (Array.isArray(items) && items.length > 0) {
    _byTask.set(taskId, items);
  } else {
    // Empty queue → drop the entry so the Map doesn't grow unbounded with
    // drained tasks.
    _byTask.delete(taskId);
  }
}

// Test-only: the store is module-level and persists across remounts by design,
// so tests that mount SessionDetail must reset it between cases for isolation.
export function _resetQueuedMessagesStore() {
  _byTask.clear();
}
