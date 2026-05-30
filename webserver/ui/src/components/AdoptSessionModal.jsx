import { useMemo, useState } from 'react';
import { adoptAgentSession, fetchClaudeSessions } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { toast } from '../stores/toastStore.js';
import { copyTextToClipboard } from '../utils/clipboard.js';
import { countNoun } from '../utils/pluralize.js';
import { formatRelativeTime } from '../utils/relativeTime.js';
import { usePickerData } from '../hooks/usePickerData.js';
import SearchPickerModal from './SearchPickerModal.jsx';

// "In use" badge shows when the transcript file was modified recently —
// proxy for "VS Code is still holding this session open." Adopting a
// session VS Code is actively writing to causes split-brain; we don't
// block adoption, but we warn the operator.
const RECENT_ACTIVITY_SECONDS = 30;

// Picker for resuming an existing Claude Code session on this task.
// Search is SERVER-side: ``query`` feeds the fetch (re-fetch on change).
// Config over the shared <SearchPickerModal>.
export default function AdoptSessionModal({ taskId, onClose, onAdopted }) {
  const [query, setQuery] = useState('');
  const { data: sessions, loading, error } = usePickerData(async () => {
    const data = await fetchClaudeSessions(query);
    return Array.isArray(data?.sessions) ? data.sessions : [];
  }, [query], []);

  const nowSeconds = useMemo(() => Date.now() / 1000, [sessions]);

  async function onConfirm(session) {
    const sessionId = session[AGENT_SESSION_ID];
    const result = await adoptAgentSession(taskId, sessionId);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Could not adopt session',
        fallback: 'adoption failed',
        durationMs: 10000,
      });
      return;
    }
    toast.show({
      kind: 'success',
      title: 'Session adopted',
      message: (
        `kato will resume Claude session ${sessionId.slice(0, 8)}… `
        + `for ${taskId} on the next message.`
      ),
      durationMs: 7000,
    });
    if (typeof onAdopted === 'function') { onAdopted(session); }
    onClose();
  }

  return (
    <SearchPickerModal
      ariaLabel="Adopt Claude session"
      title={<>Adopt Claude session for {taskId}</>}
      onClose={onClose}
      helpText={(
        <>
          Pick an existing Claude Code session (e.g. one you started in
          the VS Code extension). Kato will <code>--resume</code> it on
          the next agent spawn for this task instead of starting a fresh
          conversation.{' '}
          <strong>Close the VS Code chat tab for the session you pick</strong>{' '}
          before adopting — two clients on one session causes split-brain.
        </>
      )}
      searchPlaceholder="Search by path or message text…"
      query={query}
      onQueryChange={setQuery}
      items={sessions}
      loading={loading}
      error={error}
      loadingText="Loading sessions…"
      emptyText={`No Claude Code sessions found${query ? ` matching “${query}”` : ''}.`}
      getItemId={(session) => session[AGENT_SESSION_ID]}
      confirmLabel="Adopt selected"
      busyLabel="Adopting…"
      onConfirm={onConfirm}
      renderRow={(session) => {
        const sessionId = session[AGENT_SESSION_ID];
        const isInUse = (
          nowSeconds - session.last_modified_epoch
        ) < RECENT_ACTIVITY_SECONDS;
        const adoptedBy = session.adopted_by_task_id || '';
        return (
          <>
            <div className="adopt-session-row-top">
              <span className="adopt-session-cwd">
                {session.cwd || '(no cwd recorded)'}
              </span>
              <span className="adopt-session-meta">
                {formatRelativeTime(nowSeconds - session.last_modified_epoch)}
                {' · '}
                {countNoun(session.turn_count, 'turn')}
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
                copyTextToClipboard(sessionId).catch(() => {});
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
          </>
        );
      }}
    />
  );
}
