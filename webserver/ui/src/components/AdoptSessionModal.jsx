import { useEffect, useMemo, useState } from 'react';
import { adoptAgentSession, fetchClaudeSessions } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { formatRelativeTime } from '../utils/relativeTime.js';
import ModalShell from './ModalShell.jsx';
import ModalFooterActions from './ModalFooterActions.jsx';

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
  const selectedSession = sessions.find((s) => s[AGENT_SESSION_ID] === selectedId);

  async function onAdopt() {
    if (!selectedSession || adopting) { return; }
    setAdopting(true);
    const selectedSessionId = selectedSession[AGENT_SESSION_ID];
    const result = await adoptAgentSession(taskId, selectedSessionId);
    setAdopting(false);
    if (!result.ok) {
      const message = apiErrorMessage(result, 'adoption failed');
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
        `kato will resume Claude session ${selectedSessionId.slice(0, 8)}… `
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
    <ModalShell
      ariaLabel="Adopt Claude session"
      title={<>Adopt Claude session for {taskId}</>}
      onClose={onClose}
    >
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
            const sessionId = session[AGENT_SESSION_ID];
            const isSelected = sessionId === selectedId;
            const isInUse = (
              nowSeconds - session.last_modified_epoch
            ) < RECENT_ACTIVITY_SECONDS;
            const adoptedBy = session.adopted_by_task_id || '';
            return (
              <button
                type="button"
                key={sessionId}
                className={[
                  'adopt-session-row',
                  isSelected ? 'is-selected' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setSelectedId(sessionId)}
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
                  title={`Full session id: ${sessionId}\nMatches Claude Code /status output. Click to copy.`}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (navigator.clipboard) {
                      navigator.clipboard.writeText(sessionId);
                    }
                  }}
                >
                  id: {sessionId}
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
        <ModalFooterActions
          onCancel={onClose}
          onConfirm={onAdopt}
          busy={adopting}
          canConfirm={!!selectedSession}
          confirmLabel="Adopt selected"
          busyLabel="Adopting…"
        />
    </ModalShell>
  );
}
