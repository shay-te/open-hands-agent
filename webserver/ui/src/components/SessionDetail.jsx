import { useEffect, useRef, useState } from 'react';
import EventLog from './EventLog.jsx';
import MessageForm from './MessageForm.jsx';
import PermissionModal from './PermissionModal.jsx';
import SessionHeader from './SessionHeader.jsx';
import { useSessionStream, SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { postSession } from '../api.js';

// Owns one tab's chat experience. Wires the SSE stream to the log and
// the message/permission forms to the server. Per-task state (the
// "remember this tool" map) is local to this component, so switching
// tabs gets a clean slate by remounting via the `key` upstream.
export default function SessionDetail({ session, onActivity, onFileClicked }) {
  const taskId = session?.task_id;
  const sessionToolDecisionsRef = useRef({});
  const [transientBubbles, setTransientBubbles] = useState([]);

  const stream = useSessionStream(taskId, (raw) => {
    onActivity?.(raw, taskId);
  });

  // When a control_request arrives and the user has already chosen
  // "always allow" for this tool, auto-respond + skip the modal.
  useEffect(() => {
    const raw = stream.pendingPermission;
    if (!raw) { return; }
    const remembered = rememberedDecision(raw, sessionToolDecisionsRef.current);
    if (remembered) {
      respondToPermission({
        allow: remembered === 'allow',
        rationale: '',
        remember: false,
        requestId: extractRequestId(raw),
        toolName: extractToolName(raw),
        silent: true,
      });
    }
  }, [stream.pendingPermission]);

  // Listen for "click a tree row" events from the right pane. The chat
  // textarea is owned by MessageForm via internal state, so we mediate
  // with a small message channel: append the path to whatever's there.
  useEffect(() => {
    function handler(event) {
      const path = event?.detail?.path;
      if (!path) { return; }
      const textarea = document.getElementById('message-input');
      if (!textarea) { return; }
      const start = textarea.selectionStart ?? textarea.value.length;
      const end = textarea.selectionEnd ?? textarea.value.length;
      const before = textarea.value.slice(0, start);
      const after = textarea.value.slice(end);
      const needsLeadingSpace = before && !/\s$/.test(before);
      const fragment = (needsLeadingSpace ? ' ' : '') + path;
      const next = before + fragment + after;
      // Set the value via the native setter so React's controlled-input
      // tracking actually picks it up.
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value',
      ).set;
      setter.call(textarea, next);
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      textarea.focus();
      const cursor = before.length + fragment.length;
      textarea.setSelectionRange(cursor, cursor);
    }
    window.addEventListener('kato:file-clicked', handler);
    return () => window.removeEventListener('kato:file-clicked', handler);
  }, []);

  if (!session) {
    return (
      <main id="session-pane">
        <section id="session-placeholder" className="placeholder">
          Select a tab to chat with the bound Claude session.
        </section>
      </main>
    );
  }

  async function onSendMessage(text) {
    setTransientBubbles((prev) => [
      ...prev, { kind: 'user', text },
    ]);
    stream.setTurnInFlight(true);
    const result = await postSession(taskId, 'messages', { text });
    if (result.ok) {
      setTransientBubbles((prev) => [
        ...prev, { kind: 'system', text: '✓ delivered' },
      ]);
    } else {
      setTransientBubbles((prev) => [
        ...prev, { kind: 'error', text: `send failed: ${result.error}` },
      ]);
      stream.setTurnInFlight(false);
    }
  }

  async function respondToPermission({ allow, rationale, remember, requestId, toolName, silent = false }) {
    if (remember && toolName) {
      sessionToolDecisionsRef.current[toolName] = allow ? 'allow' : 'deny';
    }
    stream.clearPendingPermission();
    const result = await postSession(taskId, 'permission', {
      request_id: requestId,
      allow,
      rationale,
    });
    if (!result.ok) {
      setTransientBubbles((prev) => [
        ...prev,
        { kind: 'error', text: `permission send failed: ${result.error}` },
      ]);
      return;
    }
    if (!silent) {
      setTransientBubbles((prev) => [
        ...prev,
        {
          kind: 'system',
          text: `${allow ? '✓ approved' : '✗ denied'} permission ${requestId}`
            + (remember && toolName ? ` (remembered for ${toolName})` : ''),
        },
      ]);
    } else {
      setTransientBubbles((prev) => [
        ...prev,
        {
          kind: 'system',
          text: `(auto-${allow ? 'allow' : 'deny'}ed for ${toolName} — remembered for this session)`,
        },
      ]);
    }
  }

  async function onStopped(result) {
    setTransientBubbles((prev) => [
      ...prev,
      result.ok
        ? { kind: 'system', text: '✗ session stopped' }
        : { kind: 'error', text: `stop failed: ${result.error}` },
    ]);
  }

  return (
    <main id="session-pane">
      <section id="session-detail">
        <SessionHeader session={session} onStopped={onStopped} />
        <EventLog
          events={stream.events}
          banner={banner(stream.lifecycle, taskId)}
          // Render transient bubbles by appending synthetic "system"-like
          // events. We do it via an extra prop to avoid mutating the
          // server-side event stream.
        />
        {transientBubbles.length > 0 && (
          <TransientBubbles bubbles={transientBubbles} />
        )}
        <MessageForm
          turnInFlight={stream.turnInFlight}
          onSubmit={onSendMessage}
        />
      </section>
      {stream.pendingPermission && !rememberedDecision(stream.pendingPermission, sessionToolDecisionsRef.current) && (
        <PermissionModal
          raw={stream.pendingPermission}
          onDecide={respondToPermission}
        />
      )}
    </main>
  );
}

function TransientBubbles({ bubbles }) {
  // Rendered alongside EventLog so chat additions show inline. Kept
  // simple: a flat list, no autoscroll (EventLog handles that).
  return (
    <div className="transient-bubbles">
      {bubbles.map((b, i) => (
        <div key={i} className={`bubble ${b.kind}`}>{b.text}</div>
      ))}
    </div>
  );
}

function banner(lifecycle, taskId) {
  switch (lifecycle) {
    case SESSION_LIFECYCLE.CONNECTING:
      return `Connecting to session for ${taskId}…`;
    case SESSION_LIFECYCLE.IDLE:
      return '(no live subprocess for this tab — chat will resume when kato re-spawns it)';
    case SESSION_LIFECYCLE.MISSING:
      return 'No record for this task on the server.';
    case SESSION_LIFECYCLE.CLOSED:
      return '(session ended)';
    default:
      return null;
  }
}

function rememberedDecision(raw, decisions) {
  const tool = extractToolName(raw);
  return decisions[tool];
}

function extractRequestId(raw) {
  return String(raw?.request_id || raw?.id || '');
}

function extractToolName(raw) {
  const nested = (raw && typeof raw.request === 'object' && raw.request) || {};
  return String(
    raw?.tool_name || raw?.tool || nested.tool_name || nested.tool || '',
  );
}
