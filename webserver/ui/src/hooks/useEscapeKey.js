import { useEffect } from 'react';

// Window-level Escape-key handler. Registers a ``keydown`` listener
// while ``enabled`` is true; on Escape it calls ``event.preventDefault()``
// then invokes ``handler``. Tearing down the listener on cleanup keeps
// other ESC consumers (chat search, drawers, modals) from double-firing
// when they're not active.
//
// ``handler`` is read fresh on every keydown via the effect's dependency,
// so callers can pass an inline closure without stale-callback bugs as
// long as it's stable or listed where they expect re-binding.
export function useEscapeKey(handler, enabled = true) {
  useEffect(() => {
    if (!enabled) { return undefined; }
    function onKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        handler();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handler, enabled]);
}

export default useEscapeKey;
