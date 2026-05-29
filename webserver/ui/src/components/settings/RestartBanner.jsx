// Shown after a settings panel saves a value that only takes effect on
// the next kato boot. ``show`` is the panel's ``savedAt`` stamp (or any
// truthy "just saved" flag).
export default function RestartBanner({ show }) {
  if (!show) { return null; }
  return (
    <div className="settings-drawer-restart-banner">
      ⚠ Restart kato for the change to take effect.
    </div>
  );
}
