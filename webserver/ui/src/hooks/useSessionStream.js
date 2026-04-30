import { useEffect, useReducer } from 'react';
import { safeParseJSON } from '../utils/sse.js';

// Lifecycle states for the SSE connection itself (not for the task).
const LIFECYCLE = {
  CONNECTING: 'connecting',
  IDLE: 'idle',           // record exists but no live subprocess
  STREAMING: 'streaming', // events flowing
  CLOSED: 'closed',
  MISSING: 'missing',     // server has no record for this task
};

function initialState() {
  return {
    events: [],
    lifecycle: LIFECYCLE.CONNECTING,
    turnInFlight: false,
    pendingPermission: null,
  };
}

function reducer(state, action) {
  switch (action.type) {
    case 'reset':
      return initialState();
    case 'event':
      return applyEvent(state, action.payload);
    case 'lifecycle':
      return { ...state, lifecycle: action.value };
    case 'set_pending_permission':
      return { ...state, pendingPermission: action.value };
    case 'set_turn_in_flight':
      return { ...state, turnInFlight: action.value };
    default:
      return state;
  }
}

// Each SSE event mutates a small piece of state. Centralising this
// keeps the reducer pure and the event mapping explicit.
function applyEvent(state, raw) {
  const events = [...state.events, raw];
  const type = raw?.type;
  let next = { ...state, events };
  if (type === 'assistant') {
    next.turnInFlight = true;
  } else if (type === 'result') {
    next.turnInFlight = false;
  } else if (type === 'permission_request' || type === 'control_request') {
    next.pendingPermission = raw;
  }
  return next;
}

// Subscribes to /api/sessions/<task_id>/events. Returns the event log
// + derived flags (turnInFlight, pendingPermission, lifecycle).
//
// Callers can pass `onIncomingEvent` to react to events imperatively
// (e.g. fire OS notifications) without re-deriving state in another
// effect.
export function useSessionStream(taskId, onIncomingEvent) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);

  useEffect(() => {
    if (!taskId) { return undefined; }
    dispatch({ type: 'reset' });

    const stream = new EventSource(
      `/api/sessions/${encodeURIComponent(taskId)}/events`,
    );

    const handleSessionEvent = (event) => {
      const payload = safeParseJSON(event.data);
      const raw = payload?.event || payload;
      if (!raw) { return; }
      dispatch({ type: 'event', payload: raw });
      if (typeof onIncomingEvent === 'function') {
        onIncomingEvent(raw);
      }
      // First event we see flips us into streaming mode.
      dispatch({ type: 'lifecycle', value: LIFECYCLE.STREAMING });
    };

    stream.addEventListener('session_event', handleSessionEvent);
    stream.addEventListener('session_idle', () => {
      dispatch({ type: 'lifecycle', value: LIFECYCLE.IDLE });
      stream.close();
    });
    stream.addEventListener('session_missing', () => {
      dispatch({ type: 'lifecycle', value: LIFECYCLE.MISSING });
      stream.close();
    });
    stream.addEventListener('session_closed', () => {
      dispatch({ type: 'lifecycle', value: LIFECYCLE.CLOSED });
      stream.close();
    });
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        dispatch({ type: 'lifecycle', value: LIFECYCLE.CLOSED });
      }
    };
    return () => stream.close();
  // ``onIncomingEvent`` is intentionally not in deps — we read it through
  // the closure on every event delivery; subscribing/unsubscribing on
  // every parent re-render would tear down the SSE stream constantly.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  return {
    ...state,
    setTurnInFlight: (value) => dispatch({ type: 'set_turn_in_flight', value }),
    clearPendingPermission: () => dispatch({ type: 'set_pending_permission', value: null }),
  };
}

export const SESSION_LIFECYCLE = LIFECYCLE;
