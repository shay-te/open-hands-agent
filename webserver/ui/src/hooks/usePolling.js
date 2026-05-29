import { useEffect, useRef } from 'react';

// Run ``fn`` immediately, then every ``intervalMs`` until unmount. A
// ``cancelled`` guard stops a queued tick from firing after teardown.
// ``deps`` are the values that should restart polling when they change
// (like a useEffect dependency array — e.g. ``[taskId]``). Pass
// ``{ enabled: false }`` to skip polling entirely (e.g. no task yet).
// ``fn`` is read through a ref so an inline/unstable callback doesn't
// thrash the interval on every render.
export function usePolling(fn, intervalMs, deps = [], { enabled = true } = {}) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) { return undefined; }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) { return; }
      await fnRef.current();
    };
    tick();
    const handle = window.setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, enabled, ...deps]);
}
