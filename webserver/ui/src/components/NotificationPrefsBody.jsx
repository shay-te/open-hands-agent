import { NOTIFICATION_KIND } from '../constants/notificationKind.js';

// The shared body of the notification preferences UI — used both by the
// SettingsDrawer "Notifications" tab (variant="panel") and the header
// gear popover (variant="popover"). Both render the same master toggle +
// per-kind checkboxes + hints; the only differences are the master
// control's markup (a labelled checkbox in the panel vs an on/off button
// in the popover) and the panel's extra "unsupported" hint + section
// heading — both keyed off ``variant``. Every callback is owned upstream
// by the useNotifications hook, so this stays purely presentational.

const KIND_LABELS = {
  [NOTIFICATION_KIND.STARTED]: 'Task started',
  [NOTIFICATION_KIND.STATUS_CHANGE]: 'Task status changed',
  [NOTIFICATION_KIND.COMPLETED]: 'Task finished',
  [NOTIFICATION_KIND.ATTENTION]: 'Approval needed (chat / push)',
  [NOTIFICATION_KIND.ERROR]: 'Task failed / errored',
  [NOTIFICATION_KIND.REPLY]: 'Claude replied',
};

export default function NotificationPrefsBody({
  variant = 'panel',
  enabled,
  supported,
  permission,
  kindPrefs,
  onSetKindEnabled,
  onToggle,
}) {
  const masterLabel = enabled ? 'on' : 'off';
  const masterDisabled = !supported || permission === 'denied';
  const masterTitle = enabled
    ? 'Turn off all browser notifications for kato.'
    : 'Turn on browser notifications so kato can ping you when a task needs you.';

  const permissionHint = permission === 'denied' && (
    <div className="notification-settings-hint">
      Notifications are blocked at the browser level. Enable them
      in your browser site settings, then come back here.
    </div>
  );
  const unsupportedHint = variant === 'panel' && !supported && (
    <div className="notification-settings-hint">
      This browser doesn't expose the Notifications API — toggles
      below are disabled.
    </div>
  );

  function makeKindHandler(kind) {
    return function onKindChange(event) {
      onSetKindEnabled(kind, event.target.checked);
    };
  }

  const kindRows = Object.values(NOTIFICATION_KIND).map((kind) => (
    <label key={kind} className="notification-settings-row">
      <input
        type="checkbox"
        checked={kindPrefs[kind] !== false}
        onChange={makeKindHandler(kind)}
        disabled={!enabled}
      />
      <span>{KIND_LABELS[kind] || kind}</span>
    </label>
  ));

  const master = variant === 'popover' ? (
    <div className="notification-settings-row notification-settings-master">
      <span>Browser notifications</span>
      <button
        type="button"
        data-tooltip={masterTitle}
        onClick={onToggle}
        disabled={masterDisabled}
      >
        {masterLabel}
      </button>
    </div>
  ) : (
    <label
      className="notification-settings-row notification-settings-master"
      title={masterTitle}
    >
      <span className="notification-settings-master-label">
        Browser notifications
        <span className="notification-settings-master-state">{masterLabel}</span>
      </span>
      <input
        type="checkbox"
        checked={enabled}
        onChange={onToggle}
        disabled={masterDisabled}
      />
    </label>
  );

  return (
    <>
      {master}
      {unsupportedHint}
      {permissionHint}
      <div className="notification-settings-divider" />
      {variant === 'panel' && (
        <div className="notifications-settings-kinds-head">
          Choose which task events should ping you:
        </div>
      )}
      {kindRows}
    </>
  );
}
