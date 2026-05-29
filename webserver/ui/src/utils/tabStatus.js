import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { cx } from './cx.js';

// Single source of truth for the per-task base status.
//
// The status dot and any other per-task indicator MUST go through this
// function (or `resolveTabStatus` below) so the same task can never show
// two different colors in the UI. Inputs:
//   - session: the session record from /api/sessions
//   - needsAttention: whether the task is currently asking for input
//     (permission_request / control_request) — sourced from
//     `useTaskAttention()`, which lives once at the App.jsx top level.
export function deriveTabStatus(session) {
  const status = session?.status || TAB_STATUS.ACTIVE;
  if (session?.working === true) {
    return TAB_STATUS.WORKING;
  }
  if (status === TAB_STATUS.ACTIVE
      && session?.live === false
      && !session?.[AGENT_SESSION_ID]) {
    return TAB_STATUS.IDLE;
  }
  return status;
}

// Final status with attention override. Always use this from rendering
// components (Tab, SessionHeader, anywhere else that paints a dot).
export function resolveTabStatus(session, needsAttention) {
  if (needsAttention) { return TAB_STATUS.ATTENTION; }
  return deriveTabStatus(session);
}

// The status-dot className. Tab and SessionHeader both paint the
// same dot — one ``status-dot`` base, a ``status-<status>`` colour
// class, plus the provisioning ``is-loading`` and the
// ``is-idle-alive`` modifiers. ``idleAlive`` is passed in by the
// caller so each surface keeps its own derivation (SessionHeader
// additionally factors in ``!turnInFlight``).
export function statusDotClass(status, { isLoading = false, idleAlive = false } = {}) {
  return cx(
    'status-dot',
    `status-${status}`,
    isLoading && 'is-loading',
    idleAlive && 'is-idle-alive',
  );
}

export function tabStatusTitle(baseStatus, needsAttention = false) {
  if (needsAttention) { return `${baseStatus} — needs your input`; }
  if (baseStatus === TAB_STATUS.IDLE) {
    return 'no saved Claude session — kato will start one when work arrives';
  }
  if (baseStatus === TAB_STATUS.PROVISIONING) {
    return 'provisioning workspace…';
  }
  if (baseStatus === TAB_STATUS.WORKING) {
    return 'Claude is working';
  }
  return baseStatus;
}
