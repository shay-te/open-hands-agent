// Tiny draggable boundary div shared by both pane layouts. The ``id``
// is load-bearing for CSS selectors (#left-pane-resizer / #right-pane-resizer
// differ on left:-3px vs right:-3px), so callers pass it explicitly.
export default function PaneResizer({ id, onPointerDown }) {
  return (
    <div
      id={id}
      onMouseDown={onPointerDown}
      title="Drag to resize"
    />
  );
}
