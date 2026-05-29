// Persisted browser-notification preferences for ``useNotifications``.
//
// Two storage keys:
//   - ``kato.notifications``        → 'on' | 'off'  (master toggle)
//   - ``kato.notifications.kinds``  → JSON map of NOTIFICATION_KIND → bool
//
// Pulled out of the hook so the read/write/merge logic can be unit
// tested without jsdom or React. The hook composes these helpers;
// the helpers know nothing about React.

import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { readStorageString, writeStorageItem } from './storage.js';
import { parseJsonOr } from './json.js';

export const ENABLED_STORAGE_KEY = 'kato.notifications';
export const KIND_STORAGE_KEY = 'kato.notifications.kinds';

const ALL_KINDS = Object.values(NOTIFICATION_KIND);

// Sensible defaults: notify only on actionable events (task start,
// end, approval needed, errors). The chatty kinds (every Claude
// reply, every platform-state transition) are off by default — they
// spam the bell during normal task flow.
export const DEFAULT_KIND_PREFS = Object.freeze({
  [NOTIFICATION_KIND.STARTED]: true,
  [NOTIFICATION_KIND.STATUS_CHANGE]: false,
  [NOTIFICATION_KIND.COMPLETED]: true,
  [NOTIFICATION_KIND.ATTENTION]: true,
  [NOTIFICATION_KIND.ERROR]: true,
  [NOTIFICATION_KIND.REPLY]: false,
});

export function defaultKindPrefs() {
  return { ...DEFAULT_KIND_PREFS };
}

export function readEnabled(storage) {
  // Master toggle is the literal 'on'; anything else (missing key,
  // unavailable / throwing storage → '' fallback) reads as off.
  return readStorageString(ENABLED_STORAGE_KEY, '', storage) === 'on';
}

export function writeEnabled(value, storage) {
  // Both 'on' and 'off' are truthy strings, so this always setItem's.
  writeStorageItem(ENABLED_STORAGE_KEY, value ? 'on' : 'off', storage);
}

export function readKindPrefs(storage) {
  // Unavailable / throwing storage → null fallback, which fails the
  // ``!parsed`` guard below and funnels into ``defaultKindPrefs()`` —
  // same as the old explicit no-store / catch returns.
  const raw = readStorageString(KIND_STORAGE_KEY, null, storage);
  const parsed = parseJsonOr(raw, null);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return defaultKindPrefs();
  }
  // Operator's stored prefs take priority; fall back to default for
  // any kind they haven't explicitly set yet.
  return Object.fromEntries(
    ALL_KINDS.map((k) => [
      k,
      parsed[k] !== undefined ? parsed[k] !== false : DEFAULT_KIND_PREFS[k] !== false,
    ]),
  );
}

export function writeKindPrefs(prefs, storage) {
  // ``JSON.stringify`` of the prefs object is always a truthy string,
  // so this always setItem's (best-effort: swallows quota / private-
  // mode throws).
  writeStorageItem(KIND_STORAGE_KEY, JSON.stringify(prefs), storage);
}
