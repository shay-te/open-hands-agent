import { useCallback, useEffect } from 'react';

// Auto-grow a textarea to fit its content: reset ``height`` to its
// measuring baseline, then set it to ``scrollHeight`` so the box is
// exactly as tall as the typed text. Re-runs whenever ``value``
// changes (typing, draft hydration, fragment paste).
//
// Two call shapes are folded into one hook:
//   - MessageForm passes ``emptyHeight`` (a fixed single-line height)
//     so a trimmed-empty draft collapses back to one row instead of
//     growing from a stale scrollHeight.
//   - CommentWidgets passes nothing — plain ``auto`` → ``scrollHeight``,
//     with the CSS ``max-height`` cap doing the rest.
//
// The empty-check reads the LIVE DOM ``el.value`` (not the React
// ``value`` arg) so the imperative caret-restoration path in
// MessageForm — which calls the returned resize fn directly, before
// React has re-rendered — measures the textarea's actual contents.
//
// RETURNS the resize function so callers with a second, imperative
// call site (MessageForm's caret-restore useLayoutEffect) can invoke
// it on demand without duplicating the measure logic.
export function useAutoSizeTextarea(ref, value, { emptyHeight } = {}) {
  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) { return; }
    if (emptyHeight && !String(el.value || '').trim()) {
      el.style.height = emptyHeight;
      return;
    }
    // Reset first so shrinking on backspace works (scrollHeight only
    // grows otherwise).
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [ref, emptyHeight]);

  useEffect(() => { resize(); }, [value, resize]);

  return resize;
}
