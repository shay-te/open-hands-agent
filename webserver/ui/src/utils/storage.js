// Shared localStorage resolver. Returns ``window.localStorage`` in the
// browser (or the injected fake a test passes via each caller's
// ``storage`` arg), or ``null`` when storage is unavailable — SSR,
// private mode, or a browser with storage disabled.
//
// This is the single copy of the ``defaultStorage()`` body that used to
// be duplicated verbatim across composerDraft, notificationsStorage,
// pinnedTabs, resizableStorage, and (inlined) useToolMemory. Each of
// those keeps its own key-shaping / clamp / JSON-validation logic and
// just leans on this for "hand me a storage object or null".
export function resolveStorage() {
  if (typeof window !== 'undefined' && window.localStorage) {
    return window.localStorage;
  }
  return null;
}

// Read a raw string from storage, returning ``fallback`` when storage
// is unavailable, the key is missing, or ``getItem`` throws (private
// mode / disabled storage / quota). This is the single copy of the
// ``resolveStorage() + try { getItem } catch { fallback }`` boilerplate
// that callers used to hand-roll; each caller keeps its own value
// shaping (``=== 'on'`` checks, ``parseInt``, JSON parsing, etc.) and
// just leans on this for "hand me the stored string or the fallback".
export function readStorageString(key, fallback = '', storage) {
  const store = storage || resolveStorage();
  if (!store) { return fallback; }
  try {
    const raw = store.getItem(key);
    return raw === null || raw === undefined ? fallback : raw;
  } catch (_err) {
    return fallback;
  }
}

// Write a value to storage, swallowing the private-mode / quota errors
// ``setItem`` / ``removeItem`` can throw. A truthy ``value`` is stored
// verbatim (callers pre-shape it — ``'on'``/``'off'``, ``String(n)``,
// ``JSON.stringify(...)``); a falsy ``value`` removes the key so a
// round-trip read returns the caller's fallback. Best-effort by
// design: a failed write degrades to "next read shows the default",
// never a crash.
export function writeStorageItem(key, value, storage) {
  const store = storage || resolveStorage();
  if (!store) { return; }
  try {
    if (value) {
      store.setItem(key, value);
    } else {
      store.removeItem(key);
    }
  } catch (_err) {
    // Best-effort persistence — swallow.
  }
}
