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

import { readStorageString, writeStorageItem } from './storage.js';

export function readPersistedWidth(storageKey, storage) {
  if (!storageKey) { return null; }
  // Unavailable / throwing storage → null fallback (private-browsing /
  // disabled-storage path); the caller then uses its default width.
  const raw = readStorageString(storageKey, null, storage);
  if (raw === null || raw === undefined || raw === '') { return null; }
  const parsed = parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

export function writePersistedWidth(storageKey, width, storage) {
  if (!storageKey) { return; }
  if (!Number.isFinite(width)) { return; }
  // ``String`` of a finite number is always truthy, so this always
  // setItem's. A failed write is swallowed — the next mount just uses
  // the default width.
  writeStorageItem(storageKey, String(width), storage);
}
