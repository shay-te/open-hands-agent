// Three-column grid: tabs (left), session detail (middle), right pane.
// Width-of-right-pane is driven by a CSS variable so the resizer can
// update it without re-rendering the whole tree.
export default function Layout({ left, center, right, rightWidth }) {
  return (
    <div
      id="layout"
      style={{ '--right-pane-width': `${rightWidth}px` }}
    >
      {left}
      {center}
      {right}
    </div>
  );
}
