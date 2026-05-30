import { useEffect } from 'react';

// Dismiss a lightweight pop-over (e.g. a path context menu) on the next
// window pointerdown or on Escape. Only listens while ``active`` is
// truthy. Shared by the Files tab path menu and the diff-file header
// path menu, which previously duplicated this effect verbatim.
//
// Note: this intentionally does NOT reuse useEscapeKey — that hook calls
// event.preventDefault() and registers no pointerdown listener, so
// reusing it would change behavior.
export function useDismissOnOutsidePointerOrEscape(active, onDismiss) {
  useEffect(() => {
    if (!active) { return undefined; }
    function onPointerDown() { onDismiss(); }
    function onKeyDown(event) {
      if (event.key === 'Escape') { onDismiss(); }
    }
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [active]);
}
