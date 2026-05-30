import { useEffect, useRef } from 'react';
import Icon from './Icon.jsx';
import { scrollToBottom } from '../utils/scrollUtils.js';

const LEVEL_CLASS = {
  ERROR: 'error',
  WARNING: 'warn',
  WARN: 'warn',
};

function FeedHeader({ onClose }) {
  return (
    <header className="orchestrator-feed-header">
      <span className="orchestrator-feed-title">orchestrator activity</span>
      {typeof onClose === 'function' && (
        <button
          type="button"
          className="orchestrator-feed-close tooltip-end"
          onClick={onClose}
          data-tooltip="Close the activity feed and go back to the file preview."
          aria-label="Close orchestrator activity"
        >
          <Icon name="xmark" />
        </button>
      )}
    </header>
  );
}

export default function OrchestratorActivityFeed({ history, onClose }) {
  const containerRef = useRef(null);

  useEffect(() => {
    scrollToBottom(containerRef.current);
  }, [history?.length]);

  const isEmpty = !history || history.length === 0;
  if (isEmpty) {
    return (
      <div className="orchestrator-feed">
        <FeedHeader onClose={onClose} />
        <div className="orchestrator-feed-empty">
          No activity yet. Scan ticks, task transitions, and warnings will
          appear here as kato runs. Pick a task on the left to inspect its
          files and diff.
        </div>
      </div>
    );
  }

  const rows = history.map((entry) => {
    return <FeedRow key={entry.sequence} entry={entry} />;
  });
  return (
    <div className="orchestrator-feed">
      <FeedHeader onClose={onClose} />
      <div className="orchestrator-feed-body" ref={containerRef}>
        {rows}
      </div>
      <footer className="orchestrator-feed-footer">
        Pick a task on the left to inspect its files and diff.
      </footer>
    </div>
  );
}

function FeedRow({ entry }) {
  const levelRaw = (entry.level || '').toUpperCase();
  const levelClass = LEVEL_CLASS[levelRaw] || '';
  const ts = entry.epoch
    ? new Date(entry.epoch * 1000).toLocaleTimeString()
    : '';
  return (
    <div className={`orchestrator-feed-row ${levelClass}`.trim()}>
      <span className="ts">{ts}</span>
      <span className={`lvl lvl-${levelRaw.toLowerCase() || 'info'}`}>
        {(entry.level || '').slice(0, 4)}
      </span>
      <span className="msg">{renderColorizedMessage(entry.message || '')}</span>
    </div>
  );
}


// Tokenize the message body so URLs / file paths / numbers / counts
// can render in distinct colours. The regex matches the union of
// patterns; each capture group corresponds to ONE token kind so we
// can switch on the matched group index. Anything that doesn't match
// is rendered as plain text.
//
// Order matters: URLs include slashes + colons, so they have to be
// tried before bare paths and "host:port" forms.
const TOKEN_RE = new RegExp(
  [
    '(https?://[^\\s]+)',                           // 1: url
    '(/(?:[\\w.\\-]+/)*[\\w.\\-]+)',                // 2: abs path
    '((?:\\d{1,3}\\.){3}\\d{1,3}(?::\\d+)?)',       // 3: ip / ip:port
    '(\\b\\d{2}:\\d{2}:\\d{2}\\b)',                 // 4: hh:mm:ss
    '(\\(\\d+/\\d+\\))',                            // 5: progress (n/n)
    '(\\b\\d+\\b)',                                 // 6: bare integer
    '(`[^`]+`)',                                    // 7: backtick code
  ].join('|'),
  'g',
);

function renderColorizedMessage(text) {
  if (!text) { return null; }
  const out = [];
  let lastIndex = 0;
  let keyCounter = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    const [whole] = match;
    const idx = match.index ?? 0;
    if (idx > lastIndex) {
      out.push(text.slice(lastIndex, idx));
    }
    let className = '';
    if (match[1]) { className = 'tok-url'; }
    else if (match[2]) { className = 'tok-path'; }
    else if (match[3]) { className = 'tok-addr'; }
    else if (match[4]) { className = 'tok-time'; }
    else if (match[5]) { className = 'tok-progress'; }
    else if (match[6]) { className = 'tok-num'; }
    else if (match[7]) {
      // Strip the backticks before rendering — the colour does the
      // visual lift the backticks were giving in monospace logs.
      out.push(
        <span key={`tok-${keyCounter++}`} className="tok-code">
          {whole.slice(1, -1)}
        </span>,
      );
      lastIndex = idx + whole.length;
      continue;
    }
    out.push(
      <span key={`tok-${keyCounter++}`} className={className}>{whole}</span>,
    );
    lastIndex = idx + whole.length;
  }
  if (lastIndex < text.length) {
    out.push(text.slice(lastIndex));
  }
  return out;
}
