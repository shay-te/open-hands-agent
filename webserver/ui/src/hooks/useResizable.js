import { useCallback, useEffect, useRef, useState } from 'react';

// Drag-to-resize handler shared by the right-pane resizer.
// Owns: pointer event lifecycle, clamping, persistence to localStorage.
//
// Caller owns: the visual element (the grab strip) and the rendered
// width via a CSS variable or inline style.
export function useResizable({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
  // Mouse-down side: 'left' means dragging LEFT widens the element
  // (it's anchored to the right of the layout).
  anchor = 'right',
}) {
  const clamp = useCallback(
    (value) => Math.min(maxWidth, Math.max(minWidth, value)),
    [maxWidth, minWidth],
  );

  const [width, setWidth] = useState(() => {
    if (typeof localStorage === 'undefined') { return defaultWidth; }
    const stored = parseInt(localStorage.getItem(storageKey) || '', 10);
    return Number.isFinite(stored) ? clamp(stored) : defaultWidth;
  });

  const startStateRef = useRef(null);

  const onPointerDown = useCallback((event) => {
    event.preventDefault();
    startStateRef.current = { startX: event.clientX, startWidth: width };
    document.body.classList.add('kato-resizing');

    const onMove = (moveEvent) => {
      if (!startStateRef.current) { return; }
      const dx = moveEvent.clientX - startStateRef.current.startX;
      const delta = anchor === 'right' ? -dx : dx;
      setWidth(clamp(startStateRef.current.startWidth + delta));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('kato-resizing');
      startStateRef.current = null;
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [anchor, clamp, width]);

  useEffect(() => {
    if (typeof localStorage === 'undefined') { return; }
    try { localStorage.setItem(storageKey, String(width)); }
    catch (_) { /* private mode / quota */ }
  }, [storageKey, width]);

  return { width, onPointerDown };
}
