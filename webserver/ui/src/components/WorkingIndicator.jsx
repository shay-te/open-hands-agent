import { useEffect, useState } from 'react';
import { cx } from '../utils/cx.js';

const PHRASES = [
  'thinking',
  'hardening',
  'reading',
  'planning',
  'editing',
  'verifying',
  'tracing',
  'pondering',
  'cross-referencing',
  'spelunking',
  'compiling thoughts',
  'untangling',
  'wrangling',
  'sketching',
  'rebasing ideas',
];

const CYCLE_MS = 2200;
const TICK_MS = 1000;

// Above this many seconds of total silence (no server events at all),
// switch the indicator from cheerful "thinking" mode into a "may be
// stalled" warning. 45s was far too eager — a legitimate long tool
// run (big test suite, slow install, deep Read sweep, a long model
// "thinking" stretch) routinely goes minutes between streamed
// events, so the old threshold flagged Claude as "stalled" while it
// was plainly still working. 3 minutes is past any normal quiet
// gap, so the warning now means something.
const STALL_THRESHOLD_SECONDS = 180;

function pickDifferent(previous) {
  const choices = PHRASES.filter((p) => p !== previous);
  return choices[Math.floor(Math.random() * choices.length)];
}

export default function WorkingIndicator({
  active,
  waitingForApproval = false,
  lastEventAt = 0,
  onContinue,
}) {
  const [phrase, setPhrase] = useState(() => pickDifferent(''));
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) { return undefined; }
    setPhrase((prev) => pickDifferent(prev));
    const handle = setInterval(() => {
      setPhrase((prev) => pickDifferent(prev));
    }, CYCLE_MS);
    return () => clearInterval(handle);
  }, [active]);

  useEffect(() => {
    if (!active) { return undefined; }
    setNow(Date.now());
    const handle = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(handle);
  }, [active]);

  if (!active) { return null; }

  const idleSeconds = lastEventAt > 0
    ? Math.max(0, Math.floor((now - lastEventAt) / 1000))
    : 0;
  const stalled = lastEventAt > 0 && idleSeconds >= STALL_THRESHOLD_SECONDS;
  const className = cx(
    'working-indicator',
    stalled && 'is-stalled',
    waitingForApproval && 'is-waiting-approval',
  );
  const activityText = lastEventAt > 0
    ? `last activity ${formatSeconds(idleSeconds)} ago`
    : '';

  if (waitingForApproval) {
    return (
      <div className={className} aria-live="polite" role="status">
        <span className="working-indicator-glyph" aria-hidden="true">!</span>
        <span className="working-indicator-phrase">waiting for approval</span>
        <span className="working-indicator-progress" aria-hidden="true" />
        {activityText && (
          <span className="working-indicator-activity">{activityText}</span>
        )}
      </div>
    );
  }

  if (stalled) {
    return (
      <div className={className} aria-live="polite" role="status">
        <span className="working-indicator-glyph" aria-hidden="true">⚠</span>
        <span className="working-indicator-phrase">
          may be stalled — no activity for {formatSeconds(idleSeconds)}
        </span>
        {typeof onContinue === 'function' && (
          // The Claude VS Code plugin case: the agent sometimes
          // surfaces an error and just waits for the human to type
          // "continue". One click sends exactly that so the
          // operator doesn't have to retype it every time.
          <button
            type="button"
            className="working-indicator-continue"
            onClick={onContinue}
          >
            Nudge: send “continue”
          </button>
        )}
      </div>
    );
  }
  return (
    <div className={className} aria-live="polite" role="status">
      <span className="working-indicator-glyph" aria-hidden="true">✻</span>
      <span className="working-indicator-phrase">{phrase}</span>
      <span className="working-indicator-ellipsis" aria-hidden="true">…</span>
      {activityText && (
        <span className="working-indicator-activity">{activityText}</span>
      )}
    </div>
  );
}

function formatSeconds(seconds) {
  const safe = Math.max(0, Math.floor(seconds));
  if (safe < 60) { return `${safe}s`; }
  const minutes = Math.floor(safe / 60);
  const remaining = safe % 60;
  return remaining === 0
    ? `${minutes}m`
    : `${minutes}m${String(remaining).padStart(2, '0')}s`;
}
