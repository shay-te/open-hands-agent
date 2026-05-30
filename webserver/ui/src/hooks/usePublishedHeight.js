import { useLayoutEffect } from 'react';

// Publish an element's live rendered height as a CSS custom property on
// its parent (#session-detail), kept in sync via ResizeObserver and
// cleared on unmount. The scrollable #event-log reads these
// (--composer-h, --queued-h) to reserve bottom room so its last entry —
// the working indicator — is never hidden behind the floating composer
// capsule or the queued-message list stacked above it.
//
// ``active`` lets a conditionally-rendered element (the queued list
// unmounts when empty) attach/detach the observer as it appears and
// disappears; while inactive the variable is left unset (0 fallback).
export function usePublishedHeight(cssVar, ref, active = true) {
  useLayoutEffect(() => {
    const el = active ? ref.current : null;
    const target = el && el.parentElement;
    if (!el || !target || typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const publish = () => {
      target.style.setProperty(cssVar, `${el.offsetHeight}px`);
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(el);
    return () => {
      observer.disconnect();
      target.style.removeProperty(cssVar);
    };
  }, [cssVar, ref, active]);
}
