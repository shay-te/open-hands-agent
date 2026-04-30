import { useState } from 'react';
import StatusBarHistory from './StatusBarHistory.jsx';

// One-line live readout of the kato process. Click the chevron to
// expand the recent history. Pure presentation — `latest`, `history`,
// `stale` come from useStatusFeed via the parent.
export default function StatusBar({ latest, history, stale }) {
  const [open, setOpen] = useState(false);

  const level = (latest?.level || 'INFO').toUpperCase();
  const modifier = level === 'ERROR'
    ? 'is-error'
    : (level === 'WARNING' || level === 'WARN' ? 'is-warn' : '');
  const className = [
    'status-bar',
    modifier,
    stale ? 'is-stale' : '',
  ].filter(Boolean).join(' ');

  return (
    <>
      <div id="status-bar" className={className} title="Live kato activity">
        <span id="status-bar-pulse" aria-hidden="true" />
        <span id="status-bar-text">
          {latest?.message || 'Waiting for kato…'}
        </span>
        <button
          id="status-bar-toggle"
          type="button"
          title="Show recent activity"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? '▴' : '▾'}
        </button>
      </div>
      {open && <StatusBarHistory history={history} />}
    </>
  );
}
