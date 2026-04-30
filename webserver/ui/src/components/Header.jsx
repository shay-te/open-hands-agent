// Top bar: brand + global controls. Stateless — owns no behavior beyond
// click delegation. The notification toggle's actual state lives in the
// useNotifications hook upstream.
export default function Header({
  notificationsEnabled,
  notificationsSupported,
  onToggleNotifications,
  onRefresh,
}) {
  return (
    <header>
      <img src="/logo.png" alt="Kato" id="kato-logo" />
      <h1>Kato</h1>
      <span className="subtitle">Planning UI</span>
      <button
        type="button"
        title={notificationsEnabled
          ? 'Browser notifications: on (click to disable)'
          : 'Browser notifications: off (click to enable)'}
        onClick={onToggleNotifications}
        disabled={!notificationsSupported}
      >
        {notificationsEnabled ? '🔔' : '🔕'}
      </button>
      <button type="button" title="Refresh sessions" onClick={onRefresh}>
        ↻
      </button>
    </header>
  );
}
