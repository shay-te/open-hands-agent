// A settings-panel status line — loading / empty / error. ``error``
// toggles the red ``is-error`` styling. Shared by every settings panel.
export default function PanelMessage({ error = false, children }) {
  return (
    <p className={`settings-drawer-message${error ? ' is-error' : ''}`}>
      {children}
    </p>
  );
}
