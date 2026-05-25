// Per-task chat-input draft persistence.
//
// SessionDetail keys MessageForm on ``activeTaskId``, so React unmounts
// the composer when the operator switches tabs and the in-memory
// textarea value is dropped. Mirroring every keystroke to localStorage
// (and reading it back on mount) is what makes the draft survive
// tab switches — the same behavior VS Code's chat composer has.
//
// Pure functions only (no React, no DOM imports beyond the injectable
// ``storage`` arg). Keeps the module unit-testable in node:test without
// jsdom.

export const DRAFT_STORAGE_PREFIX = 'kato.composer.draft.';

export function draftStorageKey(taskId) {
  return taskId ? `${DRAFT_STORAGE_PREFIX}${taskId}` : '';
}

// ``storage`` defaults to window.localStorage in the browser. Tests
// pass a Map-backed fake so the draft logic can be exercised without
// jsdom and without leaking state across cases.
function defaultStorage() {
  if (typeof window !== 'undefined' && window.localStorage) {
    return window.localStorage;
  }
  return null;
}

// Generic key-based variants. Used by callers that own their own
// key shape (e.g. CommentForm: ``comment.<task>.<repo>.<path>.<line>.<replyTo>``).
// The ``taskId``-shaped helpers below are thin wrappers that just
// supply the chat-composer prefix.
export function readDraftByKey(key, storage) {
  if (!key) { return ''; }
  const store = storage || defaultStorage();
  if (!store) { return ''; }
  try {
    return store.getItem(key) || '';
  } catch (_err) {
    // localStorage can throw in private browsing / quota-exceeded /
    // disabled-storage environments. Callers must still work —
    // fall through with empty draft.
    return '';
  }
}

export function writeDraftByKey(key, value, storage) {
  if (!key) { return; }
  const store = storage || defaultStorage();
  if (!store) { return; }
  try {
    if (value) {
      store.setItem(key, value);
    } else {
      store.removeItem(key);
    }
  } catch (_err) {
    // Swallow — draft persistence is best-effort. A failed write
    // means the next mount shows a blank composer, not a crash.
  }
}

export function readDraft(taskId, storage) {
  return readDraftByKey(draftStorageKey(taskId), storage);
}

export function writeDraft(taskId, value, storage) {
  writeDraftByKey(draftStorageKey(taskId), value, storage);
}

export function clearDraft(taskId, storage) {
  writeDraft(taskId, '', storage);
}
