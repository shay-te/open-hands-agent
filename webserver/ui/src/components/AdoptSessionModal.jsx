import { useEffect, useMemo, useState } from 'react';
import { adoptAgentSession, fetchClaudeSessions } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { formatRelativeTime } from '../utils/relativeTime.js';

// "In use" badge shows when the transcript file was modified
// recently — proxy for "VS Code is still holding this session open."
// Adopting a session VS Code is actively writing to causes split-brain;
// we don't block adoption, but we warn the operator.
const RECENT_ACTIVITY_SECONDS = 30;

export default function AdoptSessionModal({ taskId, onClose, onAdopted }) {
  const [query, setQuery] = useState('');
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [adopting, setAdopting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchClaudeSessions(query)
      .then((data) => {
        if (cancelled) { return; }
        setSessions(Array.isArray(data?.sessions) ? data.sessions : []);
      })
      .catch((err) => {
        if (cancelled) { return; }
        setError(String(err?.message || err) || 'failed to list sessions');
        setSessions([]);
      })
      .finally(() => {
        if (!cancelled) { setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [query]);

  const nowSeconds = useMemo(() => Date.now() / 1000, [sessions]);
  const selectedSession = sessions.find((s) => s.session_id === selectedId);

  async function onAdopt() {
    if (!selectedSession || adopting) { return; }
    setAdopting(true);
    const result = await adoptAgentSession(taskId, selectedSession.session_id);
    setAdopting(false);
    if (!result.ok) {
      const message = (result.body && result.body.error)
        || result.error
        || 'adoption failed';
      toast.show({
        kind: 'error',
        title: 'Could not adopt session',
        message,
        durationMs: 10000,
      });
      return;
    }
    toast.show({
      kind: 'success',
      title: 'Session adopted',
      message: (
        `kato will resume Claude session ${selectedSession.session_id.slice(0, 8)}… `
        + `for ${taskId} on the next message.`
      ),
      durationMs: 7000,
    });
    if (typeof onAdopted === 'function') {
      onAdopted(selectedSession);
    }
    onClose();
  }

  return (
    <div
      className="adopt-session-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Adopt Claude session"
      onClick={(e) => {
        if (e.target === e.currentTarget) { onClose(); }
      }}
    >
      <div className="adopt-session-modal">
        <header className="adopt-session-modal-header">
          <h2>Adopt Claude session for {taskId}</h2>
          <button
            type="button"
            className="adopt-session-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <p className="adopt-session-modal-help">
          Pick an existing Claude Code session (e.g. one you started in
          the VS Code extension). Kato will <code>--resume</code> it on
          the next agent spawn for this task instead of starting a fresh
          conversation.{' '}
          <strong>Close the VS Code chat tab for the session you pick</strong>{' '}
          before adopting — two clients on one session causes split-brain.
        </p>
        <input
          type="text"
          className="adopt-session-search"
          placeholder="Search by path or message text…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        <div className="adopt-session-list">
          {loading && (
            <div className="adopt-session-empty">Loading sessions…</div>
          )}
          {!loading && error && (
            <div className="adopt-session-empty adopt-session-error">
              {error}
            </div>
          )}
          {!loading && !error && sessions.length === 0 && (
            <div className="adopt-session-empty">
              No Claude Code sessions found
              {query ? ` matching “${query}”` : ''}.
            </div>
          )}
          {!loading && !error && sessions.map((session) => {
            const isSelected = session.session_id === selectedId;
            const isInUse = (
              nowSeconds - session.last_modified_epoch
            ) < RECENT_ACTIVITY_SECONDS;
            const adoptedBy = session.adopted_by_task_id || '';
            return (
              <button
                type="button"
                key={session.session_id}
                className={[
                  'adopt-session-row',
                  isSelected ? 'is-selected' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setSelectedId(session.session_id)}
              >
                <div className="adopt-session-row-top">
                  <span className="adopt-session-cwd">
                    {session.cwd || '(no cwd recorded)'}
                  </span>
                  <span className="adopt-session-meta">
                    {formatRelativeTime(nowSeconds - session.last_modified_epoch)}
                    {' · '}
                    {session.turn_count} turn{session.turn_count === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="adopt-session-preview">
                  {session.last_user_message
                    || session.first_user_message
                    || '(no user messages)'}
                </div>
                <div
                  className="adopt-session-id"
                  title={`Full session id: ${session.session_id}\nMatches Claude Code /status output. Click to copy.`}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (navigator.clipboard) {
                      navigator.clipboard.writeText(session.session_id);
                    }
                  }}
                >
                  id: {session.session_id}
                </div>
                <div className="adopt-session-badges">
                  {isInUse && (
                    <span
                      className="adopt-session-badge in-use"
                      title="Transcript was written to in the last 30 seconds — VS Code may still be holding this session open."
                    >
                      in use
                    </span>
                  )}
                  {adoptedBy && (
                    <span
                      className="adopt-session-badge adopted"
                      title={`Already adopted by kato task ${adoptedBy}.`}
                    >
                      adopted by {adoptedBy}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
        <footer className="adopt-session-modal-footer">
          <button
            type="button"
            className="adopt-session-cancel"
            onClick={onClose}
            disabled={adopting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="adopt-session-confirm"
            onClick={onAdopt}
            disabled={!selectedSession || adopting}
          >
            {adopting ? 'Adopting…' : 'Adopt selected'}
          </button>
        </footer>
      </div>
    </div>
  );
}

