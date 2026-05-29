import { useEffect, useReducer, useRef, useState } from 'react';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { safeParseJSON } from '../utils/sse.js';

export const SESSION_LIFECYCLE = {
  CONNECTING: 'connecting',
  IDLE: 'idle',           // record exists but no live subprocess
  STREAMING: 'streaming', // events flowing
  CLOSED: 'closed',
  MISSING: 'missing',     // server has no record for this task
};

const ACTION_HYDRATE = 'hydrate';
const ACTION_INCOMING_EVENT = 'incoming_event';
const ACTION_INCOMING_HISTORY = 'incoming_history';
const ACTION_LIFECYCLE = 'lifecycle';
const ACTION_LOCAL_EVENT = 'local_event';
const ACTION_DISMISS_PERMISSION = 'dismiss_permission';
const ACTION_MARK_TURN_BUSY = 'mark_turn_busy';

// Per-task chat state lives in this module-level Map so it survives the
// SessionDetail unmount/remount cycle that React triggers on tab switch
// (see App.jsx `<SessionDetail key={activeSessionKey} />`). Without this
// cache, switching tabs blows away every LOCAL bubble ("✓ delivered",
// "✗ session stopped", in-flight typed messages) plus any kato-injected
// synthetic event that lives only in the server's `recent_events`
// buffer — the operator sees the chat "shrink" by however many of
// those entries had accumulated. Hydrating from the cache restores the
// previously-seen entries; dedupe on incoming SSE replay (history +
// backlog) prevents the server from doubling them.
const TASK_STREAM_CACHE = new Map();

let _localEventCounter = 0;

function emptyTaskState() {
  return {
    events: [],
    eventKeys: new Set(),
    lifecycle: SESSION_LIFECYCLE.CONNECTING,
    turnInFlight: false,
    pendingPermission: null,
    lastEventAt: 0,
  };
}

function readCachedState(taskId) {
  if (!taskId) { return emptyTaskState(); }
  let entry = TASK_STREAM_CACHE.get(taskId);
  if (!entry) {
    entry = emptyTaskState();
    TASK_STREAM_CACHE.set(taskId, entry);
  }
  return entry;
}

function writeCachedState(taskId, state) {
  if (!taskId) { return; }
  TASK_STREAM_CACHE.set(taskId, state);
}

function entryDedupeKey(entry) {
  // LOCAL entries get a synthetic monotonic id at creation; we can
  // never confuse a local bubble with a server replay, so the id alone
  // is enough.
  if (entry.source === ENTRY_SOURCE.LOCAL) {
    return `local:${entry.localId}`;
  }
  // SERVER entries: prefer the per-event ``received_at_epoch`` the
  // server stamps on each ``SessionEvent``. It's a high-resolution
  // timestamp captured when kato received the event from Claude's
  // stdout, and it's preserved across replays — so a backlog re-emit
  // of the same event reuses the same key. JSON.stringify(raw) is a
  // BAD fallback here: two distinct live events with identical
  // payload (e.g., a respawned Claude emitting another
  // ``system { subtype: init }`` for the same session id) would
  // collide and the second would be silently dropped, freezing the UI
  // until something with different content arrives. The epoch is
  // unique-per-event by construction, so it can't collide.
  if (entry.source === ENTRY_SOURCE.SERVER) {
    const epoch = Number(entry.receivedAtEpoch || 0);
    if (epoch > 0) {
      return `server:${epoch}`;
    }
    return `server:${rawFingerprint(entry.raw)}`;
  }
  // HISTORY entries always have ``received_at_epoch === 0`` (the
  // server stamps zero on disk-replayed events to mark them as
  // archival). Use a compact fingerprint for identity — replays of
  // the same JSONL produce identical raw dicts so the fingerprint is
  // stable, and we avoid walking the full payload via JSON.stringify
  // (which is the dominant cost during long-history replay).
  return `history:${rawFingerprint(entry.raw)}`;
}

// Compact identity for a Claude raw event. Most events the SDK
// emits carry a ``uuid``; assistant/user envelopes carry an
// Anthropic ``message.id``; tool results carry a ``tool_use_id``.
// Any of those uniquely identify the event without walking the
// (potentially huge) prompt / tool-output payload. Falling back to
// a type+subtype+session triple is good enough for the rare event
// shape that lacks all three — collisions there only over-dedupe,
// they don't drop distinct content.
function rawFingerprint(raw) {
  if (!raw || typeof raw !== 'object') { return 'none'; }
  if (raw.uuid) { return `u:${raw.uuid}`; }
  const messageId = raw.message && raw.message.id;
  if (messageId) { return `m:${messageId}`; }
  if (raw.tool_use_id) { return `t:${raw.tool_use_id}`; }
  return `s:${raw.type || ''}:${raw.subtype || ''}:${raw[AGENT_SESSION_ID] || ''}`;
}

function appendEntryIfNew(state, entry) {
  const key = entryDedupeKey(entry);
  if (state.eventKeys.has(key)) {
    return { state, appended: false };
  }
  // Mutate the existing Set in place. ``eventKeys`` is internal to
  // the reducer and is never read by React's render path (only the
  // ``events`` array is); React only checks the outer ``state``
  // object's identity, which we DO replace below. Skipping the
  // ``new Set(state.eventKeys)`` clone removes an O(N) copy from
  // every appended event — significant on long-lived sessions
  // where N reaches the low thousands.
  state.eventKeys.add(key);
  return {
    state: {
      ...state,
      events: [...state.events, entry],
      eventKeys: state.eventKeys,
    },
    appended: true,
  };
}

// Exported for unit tests. Pure function — the hook is just a thin
// `useReducer` wrapper around this. Tests pass in `{type, ...}` actions
// with the constants below as type strings ("lifecycle", "mark_turn_busy",
// "incoming_event", "incoming_history", "hydrate", "dismiss_permission").
export function reducer(state, action) {
  switch (action.type) {
    case ACTION_HYDRATE:
      return action.value;
    case ACTION_INCOMING_EVENT: {
      const next = reduceIncomingEvent(state, action.event, action.receivedAtEpoch);
      // Live events also imply lifecycle=STREAMING. Folding the
      // transition into the same reducer pass means one re-render
      // per event instead of two — used to be a separate
      // ACTION_LIFECYCLE dispatch from the SSE handler.
      if (next.lifecycle !== SESSION_LIFECYCLE.STREAMING) {
        return { ...next, lifecycle: SESSION_LIFECYCLE.STREAMING };
      }
      return next;
    }
    case ACTION_INCOMING_HISTORY:
      return reduceIncomingHistory(state, action.event);
    case ACTION_LOCAL_EVENT: {
      _localEventCounter += 1;
      const enriched = { ...action.event, localId: _localEventCounter };
      return appendEntryIfNew(state, enriched).state;
    }
    case ACTION_LIFECYCLE:
      // CLOSED / IDLE / MISSING all mean "nothing live is waiting for input"
      // — drop any stale permission AND reset turnInFlight. Without
      // the turnInFlight reset, the WorkingIndicator stays "Claude is
      // thinking…" forever on a subprocess that died mid-turn (no
      // RESULT event was emitted before the subprocess exited).
      if (action.value === SESSION_LIFECYCLE.CLOSED
          || action.value === SESSION_LIFECYCLE.IDLE
          || action.value === SESSION_LIFECYCLE.MISSING) {
        return {
          ...state,
          lifecycle: action.value,
          pendingPermission: null,
          turnInFlight: false,
        };
      }
      return { ...state, lifecycle: action.value };
    case ACTION_DISMISS_PERMISSION:
      return { ...state, pendingPermission: null };
    case ACTION_MARK_TURN_BUSY:
      return { ...state, turnInFlight: action.value };
    default:
      return state;
  }
}

function reduceIncomingEvent(state, raw, receivedAtEpoch) {
  const entry = {
    source: ENTRY_SOURCE.SERVER,
    raw,
    receivedAtEpoch: Number(receivedAtEpoch || 0),
  };
  const { state: appended } = appendEntryIfNew(state, entry);
  // Always advance the activity clock + lifecycle hooks, even when
  // dedupe drops the entry (e.g., backlog replay re-emits an event
  // we already cached). The bubble doesn't get rendered twice but
  // activity tracking still sees the heartbeat — without this, the
  // WorkingIndicator trips its "stalled" threshold during a healthy
  // live stream and only un-trips on tab switch (when remount
  // forces a hydrate that includes a freshly-stamped lastEventAt).
  const next = appended === state ? { ...state } : appended;
  next.lastEventAt = Date.now();
  switch (raw?.type) {
    case CLAUDE_EVENT.SYSTEM:
      // A fresh ``system/init`` is the EARLIEST wire signal that a turn
      // has begun: autonomous task prompts are written to Claude's
      // stdin (never echoed back as a ``user`` event) and partial
      // ``stream_event`` deltas are disabled, so the only thing that
      // precedes the first ``assistant`` event is ``init``. Flipping
      // "working" here means the status pill stops lagging behind the
      // "Claude session started" bubble — previously it sat on "idle"
      // for the multi-second window while Claude read context before
      // its first reply. A crash before ``result`` is still cleared by
      // the CLOSED/IDLE/MISSING lifecycle reset above; an idle session
      // reconnect replays its trailing ``result`` (backlog flows
      // through this same live path) and settles back to idle. Only
      // INIT counts — PREFLIGHT (workspace cloning) is masked by the
      // PROVISIONING status anyway.
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.INIT) {
        next.turnInFlight = true;
      }
      break;
    case CLAUDE_EVENT.ASSISTANT:
      next.turnInFlight = true;
      break;
    case CLAUDE_EVENT.RESULT:
      next.turnInFlight = false;
      next.pendingPermission = null;
      break;
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
      next.pendingPermission = raw;
      break;
    case CLAUDE_EVENT.PERMISSION_RESPONSE: {
      // Only clear pending when we can MATCH the response to it.
      // Previously we also cleared on empty respondedId — but an
      // unrelated response (e.g., synthetic event with no id) would
      // then wipe a legitimate pending modal. Require a positive
      // match: either both ids present and equal, OR the pending
      // side has no id at all (legacy shape with no way to verify).
      const respondedId = String(raw.request_id || '');
      const pendingId = pendingRequestId(state.pendingPermission);
      if (pendingId && respondedId && respondedId === pendingId) {
        next.pendingPermission = null;
      } else if (!pendingId && state.pendingPermission) {
        // Pending exists but has no id — best-effort clear so we
        // don't deadlock on a malformed legacy event.
        next.pendingPermission = null;
      }
      break;
    }
    default:
      break;
  }
  return next;
}

function reduceIncomingHistory(state, raw) {
  const entry = { source: ENTRY_SOURCE.HISTORY, raw };
  const { state: appended, appended: didAppend } = appendEntryIfNew(state, entry);
  if (!didAppend) { return state; }
  const next = appended;
  switch (raw?.type) {
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
      next.pendingPermission = raw;
      break;
    case CLAUDE_EVENT.RESULT:
      next.pendingPermission = null;
      break;
    case CLAUDE_EVENT.PERMISSION_RESPONSE: {
      const respondedId = String(raw.request_id || raw.request?.request_id || '');
      const pendingId = pendingRequestId(state.pendingPermission);
      if (!respondedId || !pendingId || respondedId === pendingId) {
        next.pendingPermission = null;
      }
      break;
    }
    default:
      break;
  }
  return next;
}

function pendingRequestId(pending) {
  if (!pending) { return ''; }
  return String(
    pending.request_id
    || pending.request?.request_id
    || pending.id
    || '',
  );
}

export function useSessionStream(taskId, onIncomingEvent) {
  const [state, dispatch] = useReducer(
    reducer,
    taskId,
    (id) => readCachedState(id),
  );
  const [streamGeneration, setStreamGeneration] = useState(0);
  const taskIdRef = useRef(taskId);

  // Persist every state transition into the module-level cache so a
  // remount (tab switch) sees the latest events when it hydrates.
  useEffect(() => {
    if (state && taskIdRef.current) {
      writeCachedState(taskIdRef.current, state);
    }
  }, [state]);

  useEffect(() => {
    if (!taskId) { return undefined; }
    // Hydrate the reducer from the cache when taskId changes (or on
    // first mount). This is what restores pre-existing entries before
    // the new SSE connection starts replaying — without it, a remount
    // would render an empty list until the server's history catches
    // up.
    taskIdRef.current = taskId;
    // When the cache says we were STREAMING (or IDLE), preserve
    // that lifecycle through the re-open. The SSE side will refresh
    // it as soon as a new event lands. Forcing CONNECTING here
    // showed a misleading "Connecting…" banner on every send (the
    // sendMessage handler calls ``reconnect()`` after a respawn) —
    // operator saw a flicker even though the session itself was
    // still live.
    const cached = readCachedState(taskId);
    const carriedLifecycle = (
      cached.lifecycle === SESSION_LIFECYCLE.STREAMING
      || cached.lifecycle === SESSION_LIFECYCLE.IDLE
    )
      ? cached.lifecycle
      : SESSION_LIFECYCLE.CONNECTING;
    dispatch({
      type: ACTION_HYDRATE,
      value: { ...cached, lifecycle: carriedLifecycle },
    });

    const stream = new EventSource(
      `/api/sessions/${encodeURIComponent(taskId)}/events`,
    );

    stream.addEventListener('session_event', (event) => {
      const payload = safeParseJSON(event.data);
      const envelope = payload?.event || payload;
      const raw = envelope?.raw || envelope;
      if (!raw) { return; }
      dispatch({
        type: ACTION_INCOMING_EVENT,
        event: raw,
        receivedAtEpoch: envelope?.received_at_epoch,
      });
      if (typeof onIncomingEvent === 'function') {
        onIncomingEvent(raw, taskId);
      }
    });
    stream.addEventListener('session_history_event', (event) => {
      const payload = safeParseJSON(event.data);
      const envelope = payload?.event || payload;
      const raw = envelope?.raw || envelope;
      if (!raw) { return; }
      dispatch({ type: ACTION_INCOMING_HISTORY, event: raw });
    });
    stream.addEventListener('session_idle', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.IDLE });
      stream.close();
    });
    stream.addEventListener('session_missing', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.MISSING });
      stream.close();
    });
    stream.addEventListener('session_closed', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.CLOSED });
      stream.close();
    });
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.CLOSED });
      }
    };
    return () => stream.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, streamGeneration]);

  return {
    events: state.events,
    lifecycle: state.lifecycle,
    turnInFlight: state.turnInFlight,
    pendingPermission: state.pendingPermission,
    lastEventAt: state.lastEventAt,
    appendLocalEvent: (event) => dispatch({ type: ACTION_LOCAL_EVENT, event }),
    markTurnBusy: (value) => dispatch({ type: ACTION_MARK_TURN_BUSY, value }),
    dismissPermission: () => dispatch({ type: ACTION_DISMISS_PERMISSION }),
    reconnect: () => setStreamGeneration((n) => n + 1),
  };
}

// Drop the cached chat state for a task — used when the operator
// "forgets" the workspace. Future mounts for that task start fresh.
export function clearTaskStreamCache(taskId) {
  if (!taskId) {
    TASK_STREAM_CACHE.clear();
    return;
  }
  TASK_STREAM_CACHE.delete(taskId);
}
