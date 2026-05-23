// Per-operator pinned-task persistence.
//
// Pinned tabs render at the LEFT of the strip and stay visible while
// the rest of the strip scrolls (CSS ``position: sticky``). The
// operator pins from a small button inside the tab pill; toggle is
// purely client-side because it's a UI preference, not state the
// backend needs to know about (mirrors the composer-draft pattern).
//
// Ordering: pinned tabs render in the order they were pinned — the
// first task pinned sits leftmost. Repinning a task moves it to the
// end of the pinned list (so it lands rightmost of the pinned group)
// rather than silently no-op'ing the click — easier to understand
// than "click was ignored".
//
// Pure functions only (no React, no DOM beyond the injectable
// ``storage`` arg). Tests pass a Map-backed fake so the logic
// stays exercisable without jsdom.

export const PINNED_TABS_STORAGE_KEY = 'kato.tabs.pinned';

function defaultStorage() {
  if (typeof window !== 'undefined' && window.localStorage) {
    return window.localStorage;
  }
  return null;
}

// Read the pinned-task-id list from storage. Defensive against:
// missing storage, missing key, malformed JSON, non-array payload,
// non-string entries (filtered out), and duplicates (dedup, keeping
// first occurrence). Returns ``[]`` for any failure mode.
export function readPinnedIds(storage) {
  const store = storage || defaultStorage();
  if (!store) { return []; }
  let raw;
  try {
    raw = store.getItem(PINNED_TABS_STORAGE_KEY);
  } catch {
    return [];
  }
  if (!raw) { return []; }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) { return []; }
  const seen = new Set();
  const out = [];
  for (const entry of parsed) {
    const id = typeof entry === 'string' ? entry.trim() : '';
    if (!id || seen.has(id)) { continue; }
    seen.add(id);
    out.push(id);
  }
  return out;
}

// Replace the pinned-task-id list. Filters non-strings / blanks /
// dupes the same way readPinnedIds does so the round-trip is stable.
export function writePinnedIds(ids, storage) {
  const store = storage || defaultStorage();
  if (!store) { return; }
  const seen = new Set();
  const sanitized = [];
  for (const entry of ids || []) {
    const id = typeof entry === 'string' ? entry.trim() : '';
    if (!id || seen.has(id)) { continue; }
    seen.add(id);
    sanitized.push(id);
  }
  try {
    store.setItem(PINNED_TABS_STORAGE_KEY, JSON.stringify(sanitized));
  } catch {
    // Quota errors / disabled storage — silently no-op. Pinning is
    // a convenience; losing it is preferable to crashing the strip.
  }
}

export function isPinned(taskId, ids) {
  if (!taskId) { return false; }
  return (ids || []).includes(taskId);
}

// Toggle the pinned state for ``taskId``. Returns the NEW id list
// (caller can re-render with it AND persist via writePinnedIds).
// Pinning a task appends it to the end of the pinned list (rightmost
// pinned position). Unpinning removes it. Returns a new array — never
// mutates the input.
export function togglePinned(taskId, ids) {
  const id = typeof taskId === 'string' ? taskId.trim() : '';
  if (!id) { return [...(ids || [])]; }
  const current = [...(ids || [])];
  const idx = current.indexOf(id);
  if (idx >= 0) {
    current.splice(idx, 1);
    return current;
  }
  current.push(id);
  return current;
}

// Order ``sessions`` so pinned tasks come first (in pinned order)
// and everything else preserves its original order. Pinned ids that
// don't match any session are silently ignored (stale pin from a
// deleted task).
export function orderByPinned(sessions, pinnedIds) {
  if (!Array.isArray(sessions) || sessions.length === 0) { return []; }
  if (!Array.isArray(pinnedIds) || pinnedIds.length === 0) {
    return [...sessions];
  }
  const byId = new Map();
  for (const session of sessions) {
    const id = String(session?.task_id || '').trim();
    if (id) { byId.set(id, session); }
  }
  const pinnedSet = new Set();
  const pinned = [];
  for (const id of pinnedIds) {
    const session = byId.get(id);
    if (session && !pinnedSet.has(id)) {
      pinned.push(session);
      pinnedSet.add(id);
    }
  }
  const rest = sessions.filter(
    (s) => !pinnedSet.has(String(s?.task_id || '').trim()),
  );
  return [...pinned, ...rest];
}
