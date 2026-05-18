import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ChatSearch from './ChatSearch.jsx';
import EventLog from './EventLog.jsx';
import MessageForm from './MessageForm.jsx';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import RightPaneResizer from './RightPaneResizer.jsx';
import SessionHeader, { SessionHeaderPlaceholder } from './SessionHeader.jsx';
import WorkingIndicator from './WorkingIndicator.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { useSessionStream, SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { useToolMemory } from '../hooks/useToolMemory.js';
import { fetchModels, fetchSessionModel, postChatMessage, postSession, setSessionModel } from '../api.js';

export default function SessionDetail({
  session,
  onActivity,
  onPendingPermissionChange,
  needsAttention = false,
  composerRef = null,
  toolMemory: providedToolMemory = null,
  onResizePointerDown,
  onOpenFile,
  onRegisterReconnect,
}) {
  const taskId = session?.task_id;
  const stream = useSessionStream(taskId, onActivity);

  useEffect(() => {
    if (typeof onRegisterReconnect === 'function') {
      onRegisterReconnect(stream.reconnect);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.reconnect]);

  // The task header (title + action buttons + Claude status + chat
  // search) is hoisted into a full-width bar UNDER the tab strip and
  // ABOVE all three panels. ``#task-header-slot`` is rendered by
  // Layout; we keep ALL wiring (stream, message handlers, search
  // state) here and only PORTAL the rendered header into that slot —
  // nothing is lifted, so the permission-dialog auto-reconnect, the
  // composer queue and the search highlighting stay owned by
  // SessionDetail. Falls back to rendering the header inline (its old
  // in-pane position) when the slot isn't in the DOM (unit tests /
  // the legacy sidebar shell).
  const [headerSlot, setHeaderSlot] = useState(null);
  useEffect(() => {
    setHeaderSlot(
      (typeof document !== 'undefined'
        && document.getElementById('task-header-slot')) || null,
    );
  }, []);

  // Outgoing message queue. While Claude is mid-turn the operator's
  // messages are HELD (not steered into the live turn) and flushed
  // one-at-a-time as the turn finishes — see ``onSendMessage`` and
  // the flush effect below. A ref, not state: nothing renders the
  // queue (the operator gets a "queued" system bubble instead), and
  // the flush effect must read the latest queue without re-subscribing.
  const queuedMessagesRef = useRef([]);
  const prevTurnInFlightRef = useRef(false);
  // The queue belongs to the task it was typed for. SessionDetail is
  // reused across tabs (not remounted per task), so drop anything
  // pending when the bound task changes — never deliver task A's
  // queued text into task B.
  useEffect(() => {
    queuedMessagesRef.current = [];
    prevTurnInFlightRef.current = stream.turnInFlight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const [availableModels, setAvailableModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const modelsLoadedRef = useRef(false);
  useEffect(() => {
    if (modelsLoadedRef.current) { return; }
    modelsLoadedRef.current = true;
    fetchModels().then((result) => {
      if (result && result.models) { setAvailableModels(result.models); }
    }).catch(() => {});
  }, []);
  useEffect(() => {
    if (!taskId) { setSelectedModel(''); return; }
    fetchSessionModel(taskId).then((result) => {
      setSelectedModel((result && result.model) || '');
    }).catch(() => {});
  }, [taskId]);
  const handleModelChange = useCallback(async (modelId) => {
    setSelectedModel(modelId);
    await setSessionModel(taskId, modelId);
  }, [taskId]);
  // Prefer the App-level toolMemory when passed (so the same recall
  // function powers both this modal AND the tab-attention filter);
  // fall back to a local instance for tests / standalone usage.
  const localToolMemory = useToolMemory();
  const memory = providedToolMemory || localToolMemory;

  useEffect(() => {
    if (typeof onPendingPermissionChange !== 'function') { return; }
    onPendingPermissionChange(taskId, !!stream.pendingPermission);
  }, [taskId, stream.pendingPermission, onPendingPermissionChange]);

  // Auto-reconnect when a permission request lands while we're
  // already sitting on this tab but the per-task SSE was closed.
  //
  // ``useSessionStream`` closes the EventSource on ``session_idle``
  // (resource optimisation while Claude sleeps). If a permission
  // request then arrives, the app-wide status feed still flags the
  // tab (``needsAttention`` → gold), but THIS stream is dead so
  // ``stream.pendingPermission`` never updates and the decision
  // dialog never appears — the operator had to click the tab again
  // to force a remount/reconnect even though they were already here.
  //
  // Re-open the stream once per attention period when the session is
  // sleeping and nothing is pending yet. ``needsAttention`` can turn
  // true while the cached lifecycle still says STREAMING; if it flips
  // to IDLE a moment later, the dialog still needs to pop immediately.
  const permissionReconnectAttemptedRef = useRef(false);
  useEffect(() => {
    permissionReconnectAttemptedRef.current = false;
  }, [taskId]);
  useEffect(() => {
    if (!needsAttention || stream.pendingPermission) {
      permissionReconnectAttemptedRef.current = false;
      return;
    }
    const sleeping = (
      stream.lifecycle === SESSION_LIFECYCLE.IDLE
      || stream.lifecycle === SESSION_LIFECYCLE.CLOSED
      || stream.lifecycle === SESSION_LIFECYCLE.MISSING
    );
    if (sleeping && !permissionReconnectAttemptedRef.current) {
      permissionReconnectAttemptedRef.current = true;
      stream.reconnect();
    }
    // stream.reconnect is a fresh closure each render; intentionally
    // excluded so this fires on the attention/lifecycle change only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsAttention, stream.pendingPermission, stream.lifecycle]);

  // Drag handle for the chat column's width. Rendered on the
  // pane's left edge — the resizer is ``position: absolute`` with
  // ``left: -3px``, which only paints correctly when its parent
  // (this <main>) is itself ``position: relative`` (set in CSS).
  const resizer = typeof onResizePointerDown === 'function'
    ? <RightPaneResizer onPointerDown={onResizePointerDown} />
    : null;

  if (!session) {
    return (
      <main id="session-pane">
        {resizer}
        {/* Keep the global header bar present (with a "Select a
            task" title + inert buttons) instead of letting it vanish
            — a header that appears/disappears as you click around is
            jarring and shifts the layout. */}
        {headerSlot
          ? createPortal(<SessionHeaderPlaceholder />, headerSlot)
          : <SessionHeaderPlaceholder />}
        <section id="session-placeholder" className="placeholder">
          Select a tab to chat with the bound Claude session.
        </section>
      </main>
    );
  }

  // Actually deliver a message to Claude now. Optimistic local USER
  // bubble + POST + result handling. The server echoes the user
  // event back shortly after; dedupe (MessageFilter.dedupeUserEchoes)
  // collapses the local + server pair. Image attachments surface via
  // ``imageCount`` so the renderer can suffix "(N attached)" without
  // polluting the dedupe key.
  async function deliverMessage(text, images = []) {
    stream.appendLocalEvent({
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text,
      imageCount: images.length,
    });
    stream.markTurnBusy(true);
    const result = await postChatMessage(taskId, text, images);
    if (result.ok) {
      const status = result.body?.status;
      if (status === 'spawned') {
        stream.appendLocalEvent({
          source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM,
          text: '✓ resumed — spawning Claude…',
        });
        stream.reconnect();
      } else {
        stream.appendLocalEvent({
          source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text: '✓ delivered',
        });
      }
      return true;
    }
    stream.appendLocalEvent({
      source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR,
      text: `send failed: ${result.error}`,
    });
    stream.markTurnBusy(false);
    // Return false so MessageForm preserves the operator's draft —
    // they can edit + retry instead of having to retype.
    return false;
  }

  // Composer entry point. While Claude is mid-turn, HOLD the message
  // in the queue and let it fly when the turn finishes (the flush
  // effect below) — the operator's input no longer steers/interrupts
  // the live turn. When Claude is idle, deliver immediately.
  async function onSendMessage(text, images = []) {
    if (stream.turnInFlight) {
      queuedMessagesRef.current.push({ text, images });
      stream.appendLocalEvent({
        source: ENTRY_SOURCE.LOCAL,
        kind: BUBBLE_KIND.SYSTEM,
        text: '⏳ queued — will send when Claude is free',
      });
      // Truthy → MessageForm accepts it and clears the draft.
      return true;
    }
    return deliverMessage(text, images);
  }

  // Flush the queue one message at a time as each turn ends.
  // Delivering a queued message re-enters the busy state, so the
  // next one waits for the turn after — messages stay strictly
  // ordered without ever interrupting Claude.
  useEffect(() => {
    const wasInFlight = prevTurnInFlightRef.current;
    prevTurnInFlightRef.current = stream.turnInFlight;
    if (wasInFlight && !stream.turnInFlight
        && queuedMessagesRef.current.length > 0) {
      const next = queuedMessagesRef.current.shift();
      deliverMessage(next.text, next.images);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.turnInFlight]);

  async function submitPermissionResponse({ requestId, allow, rationale }) {
    const result = await postSession(taskId, 'permission', {
      request_id: requestId,
      allow,
      rationale,
    });
    if (!result.ok) {
      stream.appendLocalEvent({
        source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR,
        text: `permission send failed: ${result.error}`,
      });
      return false;
    }
    return true;
  }

  async function onStopped(result) {
    stream.appendLocalEvent(
      result.ok
        ? { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text: '✗ session stopped' }
        : { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR, text: `stop failed: ${result.error}` },
    );
  }

  // Resume: respawn the Claude subprocess and tell it to keep going.
  // We send a real message ("Please continue…") rather than a no-op so
  // Claude has something to react to — the spawn path requires a user
  // turn to anchor the resumed conversation. Delivered directly, NOT
  // via the queue: resume must always actually send (a session being
  // resumed is idle, and a queued resume would never flush).
  async function onResume() {
    await deliverMessage('Please continue from where you left off.');
  }

  // Drop a system bubble into the chat so the operator has a visual
  // confirmation that adoption took — without it, the modal closes,
  // a toast flashes, and the chat looks unchanged. The bubble also
  // persists in the per-task event cache, so switching tabs and
  // coming back still shows "session attached" until the next
  // server-side history replay overwrites the picture.
  function onSessionAdopted(adopted) {
    const sessionId = String(adopted?.session_id || '').trim();
    const cwd = String(adopted?.cwd || '').trim();
    const idShort = sessionId ? `${sessionId.slice(0, 8)}…` : '(unknown)';
    const cwdLine = cwd ? `\ncwd: ${cwd}` : '';
    stream.appendLocalEvent({
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.SYSTEM,
      text: (
        `📎 session attached — kato will resume Claude session ${idShort} `
        + `for ${taskId} on the next message.${cwdLine}`
      ),
    });
  }

  const hasVisible = useMemo(() => hasVisibleBubbles(stream.events), [stream.events]);
  const banner = lifecycleBanner(stream.lifecycle, taskId, hasVisible);
  const composerDisabled = !canSend(stream.lifecycle, session);
  const composerHint = composerDisabledReason(stream.lifecycle, session);
  // Chat search state. Lifted here (not in EventLog) so the search
  // bar — which lives at the top of the chat area as a peer of
  // EventLog — and the highlight pass inside EventLog stay in sync
  // through a single source of truth. ``matchCount`` is reported
  // back by EventLog after its post-render DOM walk so the search
  // bar can show "X / N". ``currentMatchIndex`` is the navigation
  // cursor across that match run; EventLog scrolls and accents
  // whichever match is at this index.
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatchCount, setSearchMatchCount] = useState(0);
  const [searchCurrentIndex, setSearchCurrentIndex] = useState(0);
  // Reset the query (and the navigation cursor) when switching
  // tasks — a query that was open on task A shouldn't silently dim
  // task B's chat on tab switch.
  useEffect(() => {
    setSearchQuery('');
    setSearchCurrentIndex(0);
  }, [taskId]);
  // New query → reset cursor to first match. Clamp cursor if the
  // match count shrank from under it (e.g. a bubble was filtered
  // out by dedupe between renders).
  const handleSearchQueryChange = useCallback((next) => {
    setSearchQuery(next);
    setSearchCurrentIndex(0);
  }, []);
  const handleSearchMatchCount = useCallback((count) => {
    setSearchMatchCount(count);
    setSearchCurrentIndex((idx) => {
      if (count <= 0) { return 0; }
      if (idx >= count) { return count - 1; }
      return idx;
    });
  }, []);
  // Prev/next wrap around so the operator can step through without
  // hitting a "stuck at end" dead-state.
  const handlePrevMatch = useCallback(() => {
    setSearchCurrentIndex((idx) => {
      if (searchMatchCount <= 0) { return 0; }
      return (idx - 1 + searchMatchCount) % searchMatchCount;
    });
  }, [searchMatchCount]);
  const handleNextMatch = useCallback(() => {
    setSearchCurrentIndex((idx) => {
      if (searchMatchCount <= 0) { return 0; }
      return (idx + 1) % searchMatchCount;
    });
  }, [searchMatchCount]);
  const sessionHeader = (
    <SessionHeader
      session={session}
      needsAttention={needsAttention}
      onStopped={onStopped}
      onResume={onResume}
      onSessionAdopted={onSessionAdopted}
      streamLifecycle={stream.lifecycle}
      turnInFlight={stream.turnInFlight}
      searchSlot={
        <ChatSearch
          query={searchQuery}
          onQueryChange={handleSearchQueryChange}
          matchCount={searchMatchCount}
          currentMatchIndex={searchCurrentIndex}
          onPrevMatch={handlePrevMatch}
          onNextMatch={handleNextMatch}
        />
      }
    />
  );
  return (
    <main id="session-pane">
      {resizer}
      <section id="session-detail">
        {headerSlot
          ? createPortal(sessionHeader, headerSlot)
          : sessionHeader}
        {/* The working indicator is the LAST entry inside the
            scrollable log, not a floating overlay. It scrolls with
            the messages and sits just after the newest one — so it
            reads as part of the chat and the transcript never bleeds
            through it (the earlier "floating dock" overlapped chat
            text that scrolled behind it). */}
        <EventLog
          taskId={taskId}
          entries={stream.events}
          banner={banner}
          searchQuery={searchQuery}
          searchCurrentIndex={searchCurrentIndex}
          onSearchMatchCount={handleSearchMatchCount}
          onOpenFile={onOpenFile}
          footer={
            <WorkingIndicator
              active={stream.turnInFlight || !!stream.pendingPermission}
              waitingForApproval={!!stream.pendingPermission}
              lastEventAt={stream.lastEventAt}
              onContinue={() => deliverMessage('continue')}
            />
          }
        />
        <MessageForm
          ref={composerRef}
          taskId={taskId}
          turnInFlight={stream.turnInFlight}
          onSubmit={onSendMessage}
          disabled={composerDisabled}
          disabledReason={composerHint}
          availableModels={availableModels}
          selectedModel={selectedModel}
          onModelChange={handleModelChange}
        />
      </section>
      <PermissionDecisionContainer
        pending={stream.pendingPermission}
        onDismiss={stream.dismissPermission}
        onSubmit={submitPermissionResponse}
        onAuditBubble={stream.appendLocalEvent}
        recallToolDecision={memory.recall}
        rememberToolDecision={memory.remember}
      />
    </main>
  );
}

function canSend(lifecycle, session) {
  // Only block when the server has no record at all. CLOSED/IDLE still
  // accept sends — the backend respawns Claude on demand, and after a
  // rate-limit hit the operator needs to be able to retry once the
  // window resets without manually refreshing.
  if (lifecycle === SESSION_LIFECYCLE.MISSING) { return false; }
  return true;
}

function composerDisabledReason(lifecycle, session) {
  if (canSend(lifecycle, session)) { return ''; }
  return 'No record for this task on the server.';
}

// Banner is the always-visible status line at the top of the log.
// - CONNECTING / IDLE / MISSING / CLOSED → always show the explanatory text.
// - STREAMING → show "Connected, waiting…" *only* until at least one
//   bubble appears, then suppress so the chat reads cleanly.
// Exported for unit tests. Pure function with no React deps.
export function lifecycleBanner(lifecycle, taskId, hasVisible) {
  switch (lifecycle) {
    case SESSION_LIFECYCLE.CONNECTING:
      return `Connecting to session for ${taskId}…`;
    case SESSION_LIFECYCLE.STREAMING:
      return hasVisible
        ? null
        : `Connected — waiting for Claude's first reply…`;
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

// True when at least one entry would produce a visible bubble. Used by
// the banner so we don't show "waiting for first reply" once chat
// content actually arrives. Mirrors EventLog's filtering rules.
// Exported for unit tests. Pure function with no React deps.
export function hasVisibleBubbles(entries) {
  return entries.some((entry) => {
    if (entry?.source === ENTRY_SOURCE.LOCAL) { return true; }
    if (entry?.source === ENTRY_SOURCE.HISTORY) { return true; }
    const type = entry?.raw?.type;
    if (!type) { return false; }
    if (type === CLAUDE_EVENT.USER || type === CLAUDE_EVENT.STREAM_EVENT) { return false; }
    if (type === CLAUDE_EVENT.PERMISSION_REQUEST
        || type === CLAUDE_EVENT.CONTROL_REQUEST
        || type === CLAUDE_EVENT.PERMISSION_RESPONSE) { return false; }
    if (type === CLAUDE_EVENT.SYSTEM && entry.raw.subtype !== CLAUDE_SYSTEM_SUBTYPE.INIT) {
      return false;
    }
    if (type === CLAUDE_EVENT.ASSISTANT) {
      const content = entry.raw?.message?.content || [];
      return content.some(
        (b) => (b?.type === 'text' && b.text) || b?.type === 'tool_use',
      );
    }
    return true;
  });
}
