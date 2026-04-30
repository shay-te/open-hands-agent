// Scrollable list of recent kato log entries. Auto-scrolls to bottom
// on every new entry — the parent owns the buffer; we just render it.
import { useEffect, useRef } from 'react';

const LEVEL_CLASS = {
  ERROR: 'error',
  WARNING: 'warn',
  WARN: 'warn',
};

export default function StatusBarHistory({ history }) {
  const containerRef = useRef(null);
  useEffect(() => {
    const node = containerRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [history.length]);

  return (
    <div id="status-bar-history" ref={containerRef}>
      {history.map((entry) => (
        <HistoryRow key={entry.sequence} entry={entry} />
      ))}
    </div>
  );
}

function HistoryRow({ entry }) {
  const levelClass = LEVEL_CLASS[(entry.level || '').toUpperCase()] || '';
  const ts = new Date(entry.epoch * 1000).toLocaleTimeString();
  return (
    <div className={`row ${levelClass}`.trim()}>
      <span className="ts">{ts}</span>
      <span className="lvl">{(entry.level || '').slice(0, 4)}</span>
      <span className="msg">{entry.message}</span>
    </div>
  );
}
