import Icon from './Icon.jsx';
import { cx } from '../utils/cx.js';

/**
 * Top app bar. Carries:
 *   - kato logo + name
 *   - live status line (used to be a separate StatusBar component;
 *     merged in so the operator's eyes don't have to bounce
 *     between two top rows for the same context)
 *   - settings + refresh actions
 *
 * Notification enable/disable used to be a standalone bell button
 * here; it now lives in the Settings drawer's Notifications tab
 * (master toggle + per-kind toggles), so the header stays uncluttered.
 *
 * Status props (``statusLatest``, ``statusStale``, ``statusConnected``)
 * follow the same shape ``StatusBar`` used; rendering lives here now.
 */
export default function Header({
  onRefresh,
  statusLatest,
  statusStale = false,
  statusConnected = false,
  onStatusClick,
  statusActive = false,
  onOpenSettings,
}) {
  const level = String(statusLatest?.level || 'INFO').toUpperCase();
  const statusKind = level === 'ERROR'
    ? 'is-error'
    : (level === 'WARNING' || level === 'WARN' ? 'is-warn' : '');
  const statusClickable = typeof onStatusClick === 'function';
  const statusClassName = cx(
    'header-status',
    statusKind,
    statusStale ? 'is-stale' : '',
    statusClickable ? 'is-clickable' : '',
    statusActive ? 'is-active' : '',
  );

  let statusText;
  if (statusLatest?.message) {
    statusText = statusLatest.message;
  } else if (statusStale) {
    statusText = 'Lost connection to kato. Retrying…';
  } else if (statusConnected) {
    statusText = 'Connected to kato — waiting for the next scan tick.';
  } else {
    statusText = 'Connecting to kato…';
  }

  return (
    <header>
      <img src="/logo.png" alt="Kato" id="kato-logo" />
      <h1>Kato</h1>
      <span className="subtitle">Planning UI</span>
      {statusClickable ? (
        <button
          type="button"
          className={statusClassName}
          onClick={onStatusClick}
          aria-pressed={statusActive ? 'true' : 'false'}
          title={statusActive
            ? 'Hide the orchestrator activity feed and bring the editor back.'
            : 'Click to open the live orchestrator activity feed in the centre pane.'}
        >
          <span className="header-status-pulse" aria-hidden="true" />
          <span className="header-status-text">{statusText}</span>
        </button>
      ) : (
        <span className={statusClassName} title="Live kato activity">
          <span className="header-status-pulse" aria-hidden="true" />
          <span className="header-status-text">{statusText}</span>
        </span>
      )}
      <button
        type="button"
        data-tooltip="Settings — repositories, providers, notifications, and more."
        aria-label="Open settings"
        onClick={onOpenSettings}
        disabled={typeof onOpenSettings !== 'function'}
      >
        <Icon name="gear" />
      </button>
      <button
        type="button"
        data-tooltip="Refresh the task list — re-scans tickets and reloads workspace state."
        aria-label="Refresh sessions"
        onClick={onRefresh}
      >
        <Icon name="refresh" />
      </button>
    </header>
  );
}
