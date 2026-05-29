import NotificationPrefsBody from './NotificationPrefsBody.jsx';

// The body of the old NotificationSettings popover, extracted for
// reuse inside the SettingsDrawer's "Notifications" tab. Pure
// presentational — every callback is owned upstream by the
// useNotifications hook (toggle / per-kind enable) so this panel
// works the same regardless of where it's rendered. The actual
// controls live in the shared <NotificationPrefsBody> (variant="panel").
export default function NotificationsSettingsPanel(props) {
  return (
    <div className="notifications-settings-panel">
      <NotificationPrefsBody variant="panel" {...props} />
    </div>
  );
}
