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
