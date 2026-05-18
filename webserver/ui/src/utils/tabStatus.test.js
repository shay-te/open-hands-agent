// Tests for the tab-status helpers — they're the single source of
// truth for the dot color on every per-task tab and on the session
// header. A bug here paints the wrong color (operator can't trust
// the UI at a glance).

import assert from 'node:assert/strict';
import test from 'node:test';

import { TAB_STATUS } from '../constants/tabStatus.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from './tabStatus.js';


// ---------------------------------------------------------------------------
// deriveTabStatus
// ---------------------------------------------------------------------------

test('deriveTabStatus: passes through known statuses', function () {
  for (const status of [
    TAB_STATUS.PROVISIONING,
    TAB_STATUS.REVIEW,
    TAB_STATUS.DONE,
    TAB_STATUS.TERMINATED,
    TAB_STATUS.ERRORED,
  ]) {
    assert.equal(deriveTabStatus({ status }), status);
  }
});

test('deriveTabStatus: defaults to ACTIVE when status missing', function () {
  // Defensive: a session record without a status field shouldn't
  // crash. ACTIVE is the most generous default; better than blank.
  assert.equal(deriveTabStatus({}), TAB_STATUS.ACTIVE);
  assert.equal(deriveTabStatus(null), TAB_STATUS.ACTIVE);
  assert.equal(deriveTabStatus(undefined), TAB_STATUS.ACTIVE);
});

test('deriveTabStatus: ACTIVE with live=false and no session id → IDLE', function () {
  // The exact gate that lets a tab show "idle" (gray) instead of
  // misleading "active" (green) when kato has the task but no
  // running subprocess.
  assert.equal(
    deriveTabStatus({
      status: TAB_STATUS.ACTIVE, live: false, claude_session_id: '',
    }),
    TAB_STATUS.IDLE,
  );
});

test('deriveTabStatus: ACTIVE with live=false BUT a session id stays ACTIVE', function () {
  // Has a session id → kato can respawn → still "active" semantics.
  assert.equal(
    deriveTabStatus({
      status: TAB_STATUS.ACTIVE, live: false, claude_session_id: 'sess-1',
    }),
    TAB_STATUS.ACTIVE,
  );
});

test('deriveTabStatus: non-ACTIVE statuses ignore the live=false gate', function () {
  // The IDLE downgrade only applies to ACTIVE. A DONE / TERMINATED
  // session with live=false stays DONE / TERMINATED — those carry
  // semantic meaning the operator needs.
  assert.equal(
    deriveTabStatus({
      status: TAB_STATUS.DONE, live: false, claude_session_id: '',
    }),
    TAB_STATUS.DONE,
  );
  assert.equal(
    deriveTabStatus({
      status: TAB_STATUS.TERMINATED, live: false, claude_session_id: '',
    }),
    TAB_STATUS.TERMINATED,
  );
});

test('deriveTabStatus: ACTIVE with live=true stays ACTIVE regardless of session id', function () {
  // Live process running. Even if some race makes claude_session_id
  // briefly empty, "active" is the correct dot.
  assert.equal(
    deriveTabStatus({
      status: TAB_STATUS.ACTIVE, live: true, claude_session_id: '',
    }),
    TAB_STATUS.ACTIVE,
  );
});

test('deriveTabStatus: working=true overrides stale persisted status', function () {
  assert.equal(
    deriveTabStatus({
      status: TAB_STATUS.REVIEW,
      live: true,
      working: true,
      claude_session_id: 'sess-1',
    }),
    TAB_STATUS.WORKING,
  );
});


// ---------------------------------------------------------------------------
// resolveTabStatus
// ---------------------------------------------------------------------------

test('resolveTabStatus: attention overrides any base status', function () {
  // Pending permission / control_request → attention dot, always.
  // Beats any other state because operator action is required.
  for (const status of [
    TAB_STATUS.ACTIVE, TAB_STATUS.IDLE, TAB_STATUS.DONE,
    TAB_STATUS.PROVISIONING, TAB_STATUS.ERRORED, TAB_STATUS.WORKING,
  ]) {
    assert.equal(
      resolveTabStatus({ status }, true),
      TAB_STATUS.ATTENTION,
      `attention should override ${status}`,
    );
  }
});

test('resolveTabStatus: falsy attention falls through to base', function () {
  // Match real React-pattern where attention boolean might be
  // false, 0, undefined, etc.
  for (const noAttention of [false, 0, undefined, null]) {
    assert.equal(
      resolveTabStatus({ status: TAB_STATUS.ACTIVE }, noAttention),
      TAB_STATUS.ACTIVE,
    );
  }
});

test('resolveTabStatus: respects the IDLE downgrade when no attention', function () {
  assert.equal(
    resolveTabStatus({
      status: TAB_STATUS.ACTIVE, live: false, claude_session_id: '',
    }, false),
    TAB_STATUS.IDLE,
  );
});


// ---------------------------------------------------------------------------
// tabStatusTitle (tooltip text)
// ---------------------------------------------------------------------------

test('tabStatusTitle: attention reads "needs your input" appended', function () {
  // Operator hover-over tooltip — must clearly convey "act now".
  const title = tabStatusTitle(TAB_STATUS.ACTIVE, true);
  assert.ok(title.includes('needs your input'));
  // Base status is still in there so they can see what the dot
  // *would* be without the attention overlay.
  assert.ok(title.includes(TAB_STATUS.ACTIVE));
});

test('tabStatusTitle: IDLE has its own explanation', function () {
  const title = tabStatusTitle(TAB_STATUS.IDLE, false);
  // Explicitly mentions that kato will start work when needed —
  // operator should know IDLE isn't an error state.
  assert.ok(title.toLowerCase().includes('kato will start'));
});

test('tabStatusTitle: PROVISIONING has its own text', function () {
  const title = tabStatusTitle(TAB_STATUS.PROVISIONING, false);
  assert.ok(title.toLowerCase().includes('provisioning'));
});

test('tabStatusTitle: WORKING has its own text', function () {
  const title = tabStatusTitle(TAB_STATUS.WORKING, false);
  assert.ok(title.toLowerCase().includes('working'));
});

test('tabStatusTitle: unknown / generic status echoes the raw value', function () {
  // For a status the helper has no special copy for, the title
  // is just the status name. Acceptable fallback.
  assert.equal(tabStatusTitle(TAB_STATUS.DONE, false), TAB_STATUS.DONE);
  assert.equal(tabStatusTitle(TAB_STATUS.ERRORED, false), TAB_STATUS.ERRORED);
});

test('tabStatusTitle: defaults the attention flag to false', function () {
  // Single-arg call must not crash and must not falsely show
  // "needs your input".
  const title = tabStatusTitle(TAB_STATUS.ACTIVE);
  assert.ok(!title.includes('needs your input'));
});
