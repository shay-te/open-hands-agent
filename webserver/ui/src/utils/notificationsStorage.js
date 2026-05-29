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
import { resolveStorage } from './storage.js';
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
  const store = storage || resolveStorage();
  if (!store) { return false; }
  try {
    return store.getItem(ENABLED_STORAGE_KEY) === 'on';
  } catch (_err) {
    return false;
  }
}

export function writeEnabled(value, storage) {
  const store = storage || resolveStorage();
  if (!store) { return; }
  try {
    store.setItem(ENABLED_STORAGE_KEY, value ? 'on' : 'off');
  } catch (_err) {
    // Private mode / quota — best effort.
  }
}

export function readKindPrefs(storage) {
  const store = storage || resolveStorage();
  if (!store) { return defaultKindPrefs(); }
  let raw;
  try {
    raw = store.getItem(KIND_STORAGE_KEY);
  } catch (_err) {
    return defaultKindPrefs();
  }
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
  const store = storage || resolveStorage();
  if (!store) { return; }
  try {
    store.setItem(KIND_STORAGE_KEY, JSON.stringify(prefs));
  } catch (_err) {
    // Private mode / quota — best effort.
  }
}
