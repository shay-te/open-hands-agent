import { useCallback, useEffect, useRef, useState } from 'react';
import { readStorageString, writeStorageItem } from '../utils/storage.js';
import { parseJsonOr } from '../utils/json.js';

// Where the operator's "always allow / always deny" choices live in
// localStorage. Keyed by tool name (e.g. ``Bash``, ``Edit``, ``Write``)
// so the same approval covers every invocation of that tool — same
// granularity Claude Code's own "remember" checkbox uses. Persisting
// across kato restarts is the whole point: re-prompting for git after
// every server restart was the operator pain that drove this. If a
// future need for finer-grained scoping appears (per-task,
// per-command-pattern), bump the key suffix and migrate.
const STORAGE_KEY = 'kato.toolDecisions.v1';


// Exported for unit tests so the persistence layer can be verified
// independent of React's hook plumbing. Underscore-prefixed names
// signal "test surface, not part of the public API"; consumers
// should still go through ``useToolMemory``.
export const _readPersistedForTest = readPersisted;
export const _writePersistedForTest = writePersisted;

function readPersisted() {
  // Unavailable / throwing storage → null fallback, which fails the
  // ``!parsed`` guard and returns {} — same as the old no-store /
  // catch returns. Arrays pass ``typeof === 'object'`` and are
  // returned as-is (existing tolerated behavior).
  const parsed = parseJsonOr(readStorageString(STORAGE_KEY, null), null);
  if (!parsed || typeof parsed !== 'object') { return {}; }
  return parsed;
}


function writePersisted(decisions) {
  // ``JSON.stringify`` of the decisions object is always a truthy
  // string, so this always setItem's; quota / private-mode failures
  // are swallowed (non-fatal).
  writeStorageItem(STORAGE_KEY, JSON.stringify(decisions), undefined);
}


export function useToolMemory() {
  // ``decisionsRef`` holds the live source of truth so ``recall`` can
  // be a synchronous lookup; ``version`` bumps on every mutation so
  // consumers that depend on the recall result re-render. The recall
  // callback returns the same value across renders for the same
  // tool, so React's identity-based memoization stays sane.
  const decisionsRef = useRef(readPersisted());
  const [version, setVersion] = useState(0);

  // Cross-tab sync: another browser tab persisting a decision should
  // immediately affect this tab too — otherwise the operator clicks
  // "remember" once and the *other* open tab still shows the prompt.
  useEffect(() => {
    function onStorage(event) {
      if (event.key !== STORAGE_KEY) { return; }
      decisionsRef.current = readPersisted();
      setVersion((n) => n + 1);
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const remember = useCallback((toolName, allow) => {
    if (!toolName) { return; }
    const next = { ...decisionsRef.current, [toolName]: allow ? 'allow' : 'deny' };
    decisionsRef.current = next;
    writePersisted(next);
    setVersion((n) => n + 1);
  }, []);

  const recall = useCallback((toolName) => {
    if (!toolName) { return null; }
    return decisionsRef.current[toolName] || null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const forget = useCallback((toolName) => {
    if (!toolName) {
      decisionsRef.current = {};
      writePersisted({});
      setVersion((n) => n + 1);
      return;
    }
    if (!(toolName in decisionsRef.current)) { return; }
    const next = { ...decisionsRef.current };
    delete next[toolName];
    decisionsRef.current = next;
    writePersisted(next);
    setVersion((n) => n + 1);
  }, []);

  return { remember, recall, forget };
}
