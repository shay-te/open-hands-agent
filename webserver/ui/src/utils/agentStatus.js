import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { deriveTabStatus, resolveTabStatus, statusDotClass } from './tabStatus.js';

// THE single source of truth for agent (Claude/Codex) liveness.
//
// One function derives it so the header chip, the tab dot, and the tab tooltip
// badge can never disagree (UNA-2492: chip said "closed" while the tab said
// "working"). When the active task's live SSE state is available (``liveStatus``
// from agentStatusStore) it wins — only it can tell ``sleeping`` from
// ``closed``. Otherwise we fall back to the 5s-polled session fields (the old
// per-surface behaviour), best-effort.
//
// This is agent-SUBPROCESS liveness only. The comment-run status (``kato_status``
// WORKING/PENDING on review comments) and the workspace/task status
// (active/review/done) are different axes — they are NOT derived here.

// kind → { label (chip word), title (tooltip) }. Ported verbatim from the old
// SessionHeader.describeClaudeStatus so the chip text/classes are unchanged.
const STATUS_BY_KIND = {
  provisioning: { label: 'provisioning', title: 'Workspace is being set up.' },
  working: { label: 'working', title: 'Claude is processing the current turn.' },
  approval: { label: 'approval', title: 'Claude is paused waiting for your approval.' },
  idle: { label: 'idle', title: 'Claude is connected and waiting for input.' },
  connecting: { label: 'connecting', title: 'Connecting to the Claude session…' },
  sleeping: { label: 'sleeping', title: 'No live subprocess — kato will respawn Claude on the next message.' },
  closed: { label: 'closed', title: 'The Claude subprocess for this task has ended.' },
  missing: { label: 'no record', title: 'No record for this task on the server.' },
  unknown: { label: '—', title: 'Claude status unknown.' },
};

const KIND_BY_LIFECYCLE = {
  [SESSION_LIFECYCLE.STREAMING]: 'idle',
  [SESSION_LIFECYCLE.CONNECTING]: 'connecting',
  [SESSION_LIFECYCLE.IDLE]: 'sleeping',
  [SESSION_LIFECYCLE.CLOSED]: 'closed',
  [SESSION_LIFECYCLE.MISSING]: 'missing',
};

// Live (active-task) path — ported from describeClaudeStatus's precedence.
function liveKind(liveStatus, baseStatus, needsAttention) {
  if (baseStatus === TAB_STATUS.PROVISIONING) { return 'provisioning'; }
  if (liveStatus.turnInFlight) { return 'working'; }
  if (needsAttention) { return 'approval'; }
  return KIND_BY_LIFECYCLE[liveStatus.lifecycle] || 'unknown';
}

// Workspace states where the subprocess is gone for good — the task is
// finished/stopped, so kato won't lazily respawn it. A non-live tab in one of
// these reads as ``closed`` rather than ``sleeping``.
const TERMINAL_STATUSES = new Set([
  TAB_STATUS.DONE,
  TAB_STATUS.TERMINATED,
  TAB_STATUS.ERRORED,
]);

// Polled-fallback path (background tabs, no live SSE) — ported from
// Tab.claudeBadge (+ provisioning). A non-live tab is ``closed`` when the task
// is in a terminal state (done/terminated/errored — no respawn), else
// ``sleeping`` because kato lazily ``--resume``s any other tab on the next
// message. The transient post-exit "closed" flash an ACTIVE tab's live stream
// shows is not a pollable fact, so background tabs can't reproduce it — and
// don't need to (that tab will respawn on the next message → sleeping).
function polledKind(session, baseStatus) {
  if (baseStatus === TAB_STATUS.PROVISIONING) { return 'provisioning'; }
  if (session?.working === true) { return 'working'; }
  if (session?.has_pending_permission) { return 'approval'; }
  if (session?.live === true) { return 'idle'; }
  if (session?.live === false) {
    return TERMINAL_STATUSES.has(baseStatus) ? 'closed' : 'sleeping';
  }
  return 'unknown';
}

// The only tab-tooltip badge CSS classes that exist are is-work/idle/sleep/wait.
const BADGE_KIND = {
  working: 'work',
  idle: 'idle',
  connecting: 'idle',
  sleeping: 'sleep',
  closed: 'sleep',
  approval: 'wait',
};

// Map a status kind to the tooltip badge's ``is-*`` class. Returns '' for kinds
// with no badge styling (provisioning/missing/unknown) — callers treat '' as
// "no badge", matching the old claudeBadge returning null.
export function badgeKindFor(kind) {
  return BADGE_KIND[kind] || '';
}

// session: the polled /api/sessions record. liveStatus: the active task's live
// SSE facts {lifecycle, turnInFlight, pendingPermission} from agentStatusStore,
// or null for background tabs. needsAttention: the caller's attention flag.
// Returns { kind, label, title, dotClass } — the dotClass keeps the workspace
// axis (resolveTabStatus + statusDotClass) so review/done/attention colours and
// the provisioning/idle-alive modifiers are unchanged.
export function deriveAgentStatus(session, liveStatus = null, needsAttention = false) {
  const baseStatus = deriveTabStatus(session);
  const kind = liveStatus
    ? liveKind(liveStatus, baseStatus, needsAttention)
    : polledKind(session, baseStatus);
  const meta = STATUS_BY_KIND[kind] || STATUS_BY_KIND.unknown;

  const resolved = resolveTabStatus(session, needsAttention);
  const turnish = liveStatus ? !!liveStatus.turnInFlight : (session?.working === true);
  const idleAlive = resolved === TAB_STATUS.ACTIVE
    && !turnish
    && session?.working === false;
  const dotClass = statusDotClass(resolved, {
    isLoading: baseStatus === TAB_STATUS.PROVISIONING,
    idleAlive,
  });

  // ``status`` is the resolved workspace-axis status (active/review/done/…
  // with the attention override) — surfaces that key a dot/tooltip off the
  // workspace axis (e.g. the tab tooltip's status dot) reuse it.
  return { kind, label: meta.label, title: meta.title, dotClass, status: resolved };
}
