import { useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import NotificationPrefsBody from './NotificationPrefsBody.jsx';

// Header gear-button popover for notification prefs. This component owns
// only the popover chrome — the gear toggle, open state, and
// outside-click-to-close; the controls themselves live in the shared
// <NotificationPrefsBody variant="popover">.
export default function NotificationSettings({
  enabled,
  supported,
  permission,
  kindPrefs,
  onSetKindEnabled,
  onToggle,
}) {
  const [open, setOpen] = useState(false);
  const popoverRef = useRef(null);

  useEffect(() => {
    if (!open) { return; }
    function onClickOutside(event) {
      if (popoverRef.current && !popoverRef.current.contains(event.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  return (
    <div className="notification-settings" ref={popoverRef}>
      <button
        type="button"
        data-tooltip="Notification settings — choose which task events should ping you."
        aria-label="Notification settings"
        onClick={() => setOpen((v) => !v)}
        disabled={!supported}
      >
        <Icon name="gear" />
      </button>
      {open && (
        <div className="notification-settings-popover">
          <NotificationPrefsBody
            variant="popover"
            enabled={enabled}
            supported={supported}
            permission={permission}
            kindPrefs={kindPrefs}
            onSetKindEnabled={onSetKindEnabled}
            onToggle={onToggle}
          />
        </div>
      )}
    </div>
  );
}
