// Thin grab strip on the LEFT edge of the right pane. Visual only;
// the parent installs the pointer-down handler from useResizable.
export default function RightPaneResizer({ onPointerDown }) {
  return (
    <div
      id="right-pane-resizer"
      onMouseDown={onPointerDown}
      title="Drag to resize"
    />
  );
}
