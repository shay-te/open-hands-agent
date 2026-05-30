import { useCallback, useEffect, useRef, useState } from 'react';
import { readStorageString, writeStorageItem } from '../utils/storage.js';
import { parseJsonOr } from '../utils/json.js';

// Per-comment collapse state that SURVIVES a page reload and a kato
// restart — the operator's last collapse/expand choice is remembered
// instead of resetting every time. Persisted to localStorage keyed by
// comment id (same idea as useToolMemory, which keeps tool-approval
// choices across restarts). With no stored choice, ``defaultCollapsed``
// (the status-derived default: a resolved root starts collapsed) wins.
const STORAGE_KEY = 'kato.commentCollapse.v1';

function readMap() {
  const parsed = parseJsonOr(readStorageString(STORAGE_KEY, null), null);
  if (!parsed || typeof parsed !== 'object') { return {}; }
  return parsed;
}

function persistCollapse(commentId, collapsed) {
  if (!commentId) { return; }
  const map = readMap();
  map[commentId] = !!collapsed;
  writeStorageItem(STORAGE_KEY, JSON.stringify(map), undefined);
}

// Exposed for unit tests so the persistence layer can be checked
// directly; consumers should go through ``useCommentCollapse``.
export const _readCollapseMapForTest = readMap;
export const _persistCollapseForTest = persistCollapse;

export function useCommentCollapse(commentId, defaultCollapsed) {
  // Restore the persisted choice on mount; fall back to the
  // status-derived default when this comment has none.
  const [collapsed, setCollapsed] = useState(() => {
    if (!commentId) { return defaultCollapsed; }
    const map = readMap();
    return commentId in map ? !!map[commentId] : defaultCollapsed;
  });

  // Re-sync to the new default ONLY when the status-derived default
  // actually flips after mount (resolve -> collapse, reopen -> expand)
  // — never on the initial render, so the restored value above is not
  // clobbered. The re-synced state is itself persisted.
  const prevDefault = useRef(defaultCollapsed);
  useEffect(() => {
    if (defaultCollapsed === prevDefault.current) { return; }
    prevDefault.current = defaultCollapsed;
    setCollapsed(defaultCollapsed);
    persistCollapse(commentId, defaultCollapsed);
  }, [defaultCollapsed, commentId]);

  const toggle = useCallback(() => {
    setCollapsed((value) => {
      const next = !value;
      persistCollapse(commentId, next);
      return next;
    });
  }, [commentId]);

  return [collapsed, toggle];
}
