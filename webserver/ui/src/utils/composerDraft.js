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

import { resolveStorage } from './storage.js';

export const DRAFT_STORAGE_PREFIX = 'kato.composer.draft.';

export function draftStorageKey(taskId) {
  return taskId ? `${DRAFT_STORAGE_PREFIX}${taskId}` : '';
}

export const COMMENT_DRAFT_PREFIX = 'kato.comment.draft.';

// Draft-storage key for an inline review-comment form. ``lineSegment`` is
// the gutter line key (or the literal 'file' for the file-level form);
// ``replyTo`` is the id of the comment being replied to, or falsy for a
// top-level (root) comment. Centralised here so the gutter form and the
// file-level form can't drift in prefix/separator and silently split a draft.
export function commentDraftKey(taskId, repoId, path, lineSegment, replyTo) {
  return `${COMMENT_DRAFT_PREFIX}${taskId}|${repoId}|${path}|${lineSegment}|${replyTo || 'root'}`;
}

// Generic key-based variants. Used by callers that own their own
// key shape (e.g. CommentForm: ``comment.<task>.<repo>.<path>.<line>.<replyTo>``).
// The ``taskId``-shaped helpers below are thin wrappers that just
// supply the chat-composer prefix.
export function readDraftByKey(key, storage) {
  if (!key) { return ''; }
  const store = storage || resolveStorage();
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
  const store = storage || resolveStorage();
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
