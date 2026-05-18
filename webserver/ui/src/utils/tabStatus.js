import { TAB_STATUS } from '../constants/tabStatus.js';

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
      && !session?.claude_session_id) {
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
