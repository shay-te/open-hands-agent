import { useCallback, useRef, useState } from 'react';

// Wraps an async action with an in-flight ``busy`` flag so a double-tap
// can't fire it twice. ``run(...args)`` no-ops while busy or when
// ``enabled`` is false; otherwise it does
//   setBusy(true) → await action(...args) → setBusy(false) → onDone(result) → return result
// (the exact order the hand-written push/pull/approve callbacks used).
// ``action`` and ``onDone`` are read through refs so ``run`` stays
// referentially stable — it only changes identity when busy/enabled flip.
export function useBusyAction(action, { enabled = true, onDone } = {}) {
  const [busy, setBusy] = useState(false);
  const actionRef = useRef(action);
  actionRef.current = action;
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const run = useCallback(async (...args) => {
    if (busy || !enabled) { return null; }
    setBusy(true);
    const result = await actionRef.current(...args);
    setBusy(false);
    if (onDoneRef.current) { onDoneRef.current(result); }
    return result;
  }, [busy, enabled]);

  return [busy, run];
}
