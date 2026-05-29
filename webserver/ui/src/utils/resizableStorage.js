// Persisted pane-width storage for ``useResizable``.
//
// Pulled out of the hook so the parse/clamp/write logic can be unit
// tested without React or jsdom. The hook composes these helpers; the
// helpers know nothing about React.
//
// Width is stored as a string of base-10 digits under an
// operator-supplied key. Garbage values (non-numeric, missing,
// unreadable storage) fall back to ``null`` so the caller can apply
// its default.

import { resolveStorage } from './storage.js';

export function readPersistedWidth(storageKey, storage) {
  if (!storageKey) { return null; }
  const store = storage || resolveStorage();
  if (!store) { return null; }
  try {
    const raw = store.getItem(storageKey);
    if (raw === null || raw === undefined || raw === '') { return null; }
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : null;
  } catch (_err) {
    // Private-browsing / disabled-storage path — caller falls back to
    // its default width.
    return null;
  }
}

export function writePersistedWidth(storageKey, width, storage) {
  if (!storageKey) { return; }
  if (!Number.isFinite(width)) { return; }
  const store = storage || resolveStorage();
  if (!store) { return; }
  try {
    store.setItem(storageKey, String(width));
  } catch (_err) {
    // Best-effort persistence — a failed write just means the next
    // mount uses the default width.
  }
}
