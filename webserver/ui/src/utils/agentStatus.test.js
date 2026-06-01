import assert from 'node:assert/strict';
import test from 'node:test';

import { deriveAgentStatus, badgeKindFor } from './agentStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';

// A plain "active workspace, live subprocess" session. Individual tests tweak
// fields. ``status: 'active'`` keeps the workspace axis out of the way so we
// isolate the agent-liveness axis.
function session(extra = {}) {
  return { task_id: 'T1', status: 'active', live: true, working: false, ...extra };
}

function live(extra = {}) {
  return { lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: false, ...extra };
}

// ---- Active (live SSE) path: one case per kind -----------------------------

test('active path maps each lifecycle to the right kind/label', () => {
  const cases = [
    [SESSION_LIFECYCLE.STREAMING, 'idle', 'idle'],
    [SESSION_LIFECYCLE.CONNECTING, 'connecting', 'connecting'],
    [SESSION_LIFECYCLE.IDLE, 'sleeping', 'sleeping'],
    [SESSION_LIFECYCLE.CLOSED, 'closed', 'closed'],
    [SESSION_LIFECYCLE.MISSING, 'missing', 'no record'],
  ];
  for (const [lifecycle, kind, label] of cases) {
    const got = deriveAgentStatus(session(), live({ lifecycle }), false);
    assert.equal(got.kind, kind, `${lifecycle} → kind`);
    assert.equal(got.label, label, `${lifecycle} → label`);
  }
});

test('UNA-2492 regression: live lifecycle=closed wins over a stale polled working=true', () => {
  // The exact bug: the chip (live) said closed, the tab (polled) said working.
  // With the unified derivation the live state wins → everyone shows closed.
  const got = deriveAgentStatus(
    session({ working: true, live: true }), // stale poll says working
    live({ lifecycle: SESSION_LIFECYCLE.CLOSED, turnInFlight: false }),
    false,
  );
  assert.equal(got.kind, 'closed');
  assert.equal(got.label, 'closed');
});

test('active precedence: turnInFlight beats lifecycle (working), provisioning beats all', () => {
  const working = deriveAgentStatus(
    session(), live({ lifecycle: SESSION_LIFECYCLE.CLOSED, turnInFlight: true }), false,
  );
  assert.equal(working.kind, 'working');

  const provisioning = deriveAgentStatus(
    session({ status: 'provisioning' }),
    live({ lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true }),
    true,
  );
  assert.equal(provisioning.kind, 'provisioning');
});

test('active: needsAttention → approval (but turnInFlight still wins)', () => {
  const approval = deriveAgentStatus(session(), live({ lifecycle: SESSION_LIFECYCLE.STREAMING }), true);
  assert.equal(approval.kind, 'approval');

  const working = deriveAgentStatus(session(), live({ turnInFlight: true }), true);
  assert.equal(working.kind, 'working');
});

// ---- Polled fallback path (no live status) ---------------------------------

test('polled fallback maps each field combo to the right kind (matches old claudeBadge)', () => {
  assert.equal(deriveAgentStatus(session({ working: true }), null, false).kind, 'working');
  assert.equal(deriveAgentStatus(session({ has_pending_permission: true }), null, false).kind, 'approval');
  assert.equal(deriveAgentStatus(session({ live: false }), null, false).kind, 'sleeping');
  assert.equal(deriveAgentStatus(session({ live: true }), null, false).kind, 'idle');
  assert.equal(deriveAgentStatus(session({ status: 'provisioning' }), null, false).kind, 'provisioning');
});

test('polled fallback cannot tell closed from sleeping (best-effort sleeping)', () => {
  // No live stream → live:false renders as sleeping even if the subprocess
  // actually ended. Documented limitation; the active task gets it right.
  assert.equal(deriveAgentStatus(session({ live: false }), null, false).kind, 'sleeping');
});

// ---- dotClass preserves the workspace axis ---------------------------------

test('dotClass keeps the workspace status (review/done) and attention override', () => {
  const review = deriveAgentStatus(session({ status: 'review' }), null, false);
  assert.match(review.dotClass, /status-review/);
  assert.equal(review.status, 'review');

  const attention = deriveAgentStatus(session({ status: 'review' }), null, true);
  assert.match(attention.dotClass, /status-attention/);
});

test('dotClass marks provisioning as loading', () => {
  const got = deriveAgentStatus(session({ status: 'provisioning' }), null, false);
  assert.match(got.dotClass, /is-loading/);
});

// ---- badgeKindFor mapping ---------------------------------------------------

test('badgeKindFor maps kinds to the existing tooltip badge classes', () => {
  assert.equal(badgeKindFor('working'), 'work');
  assert.equal(badgeKindFor('idle'), 'idle');
  assert.equal(badgeKindFor('connecting'), 'idle');
  assert.equal(badgeKindFor('sleeping'), 'sleep');
  assert.equal(badgeKindFor('closed'), 'sleep');
  assert.equal(badgeKindFor('approval'), 'wait');
  // no badge styling → '' (caller renders no badge)
  assert.equal(badgeKindFor('provisioning'), '');
  assert.equal(badgeKindFor('missing'), '');
  assert.equal(badgeKindFor('unknown'), '');
});
