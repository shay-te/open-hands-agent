// The header every settings panel opens with: an h3 title followed by
// the panel's description copy. The copy is passed as ``children`` (a
// <p>…</p>) because it varies per panel — only the wrapper is shared.
export default function SettingsPanelHead({ title, children }) {
  return (
    <header className="settings-drawer-panel-head">
      <h3>{title}</h3>
      {children}
    </header>
  );
}
