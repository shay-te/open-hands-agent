// Tests for the constant enums. Each file is just frozen object
// exports — the contracts to pin are: (a) every value present,
// (b) values match the wire-protocol / CSS strings other modules
// depend on, (c) the object is immutable (Object.freeze) so
// downstream code can rely on identity.

import assert from 'node:assert/strict';
import test from 'node:test';

import { BUBBLE_KIND } from './bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from './claudeEvent.js';
import { ENTRY_SOURCE } from './entrySource.js';
import { NOTIFICATION_KIND } from './notificationKind.js';
import { TAB_STATUS } from './tabStatus.js';


// ---------------------------------------------------------------------------
// BUBBLE_KIND — visual class names for chat bubbles
// ---------------------------------------------------------------------------

test('BUBBLE_KIND has every expected entry', () => {
  assert.deepEqual(Object.keys(BUBBLE_KIND).sort(), [
    'ASSISTANT', 'ERROR', 'SYSTEM', 'TOOL', 'USER',
  ]);
});

test('BUBBLE_KIND values are lowercase strings matching CSS class suffixes', () => {
  for (const value of Object.values(BUBBLE_KIND)) {
    assert.equal(typeof value, 'string');
    assert.equal(value, value.toLowerCase(),
      `BUBBLE_KIND value should be lowercase: ${value}`);
  }
});

test('BUBBLE_KIND is frozen (read-only at runtime)', () => {
  assert.ok(Object.isFrozen(BUBBLE_KIND));
});


// ---------------------------------------------------------------------------
// CLAUDE_EVENT — wire-protocol event types
// ---------------------------------------------------------------------------

test('CLAUDE_EVENT has all wire-protocol event types', () => {
  // Mirror of what Claude CLI's stream-json emits. New types should
  // be added explicitly; this lock pins the current set.
  assert.deepEqual(Object.keys(CLAUDE_EVENT).sort(), [
    'ASSISTANT', 'CONTROL_REQUEST', 'PERMISSION_REQUEST',
    'PERMISSION_RESPONSE', 'RESULT', 'STREAM_EVENT', 'SYSTEM', 'USER',
  ]);
});

test('CLAUDE_EVENT values are the literal wire strings', () => {
  assert.equal(CLAUDE_EVENT.ASSISTANT, 'assistant');
  assert.equal(CLAUDE_EVENT.USER, 'user');
  assert.equal(CLAUDE_EVENT.SYSTEM, 'system');
  assert.equal(CLAUDE_EVENT.RESULT, 'result');
  assert.equal(CLAUDE_EVENT.STREAM_EVENT, 'stream_event');
  assert.equal(CLAUDE_EVENT.PERMISSION_REQUEST, 'permission_request');
  assert.equal(CLAUDE_EVENT.CONTROL_REQUEST, 'control_request');
  assert.equal(CLAUDE_EVENT.PERMISSION_RESPONSE, 'permission_response');
});

test('CLAUDE_EVENT is frozen', () => {
  assert.ok(Object.isFrozen(CLAUDE_EVENT));
});

test('CLAUDE_SYSTEM_SUBTYPE covers INIT and PREFLIGHT', () => {
  assert.equal(CLAUDE_SYSTEM_SUBTYPE.INIT, 'init');
  assert.equal(CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT, 'preflight');
});

test('CLAUDE_SYSTEM_SUBTYPE is frozen', () => {
  assert.ok(Object.isFrozen(CLAUDE_SYSTEM_SUBTYPE));
});


// ---------------------------------------------------------------------------
// ENTRY_SOURCE — where an event-log entry came from
// ---------------------------------------------------------------------------

test('ENTRY_SOURCE has local/server/history', () => {
  assert.deepEqual(Object.keys(ENTRY_SOURCE).sort(),
    ['HISTORY', 'LOCAL', 'SERVER']);
});

test('ENTRY_SOURCE values are stable wire strings', () => {
  assert.equal(ENTRY_SOURCE.LOCAL, 'local');
  assert.equal(ENTRY_SOURCE.SERVER, 'server');
  assert.equal(ENTRY_SOURCE.HISTORY, 'history');
});

test('ENTRY_SOURCE is frozen', () => {
  assert.ok(Object.isFrozen(ENTRY_SOURCE));
});


// ---------------------------------------------------------------------------
// NOTIFICATION_KIND — drives notification routing
// ---------------------------------------------------------------------------

test('NOTIFICATION_KIND covers every actionable kind', () => {
  // If a new kind is added, default prefs in notificationsStorage.js
  // MUST also be updated — that test is in notificationsStorage.test.js.
  assert.deepEqual(Object.keys(NOTIFICATION_KIND).sort(), [
    'ATTENTION', 'COMPLETED', 'ERROR', 'REPLY', 'STARTED', 'STATUS_CHANGE',
  ]);
});

test('NOTIFICATION_KIND values match the storage key strings', () => {
  assert.equal(NOTIFICATION_KIND.STARTED, 'started');
  assert.equal(NOTIFICATION_KIND.STATUS_CHANGE, 'status_change');
  assert.equal(NOTIFICATION_KIND.COMPLETED, 'completed');
  assert.equal(NOTIFICATION_KIND.ATTENTION, 'attention');
  assert.equal(NOTIFICATION_KIND.ERROR, 'error');
  assert.equal(NOTIFICATION_KIND.REPLY, 'reply');
});

test('NOTIFICATION_KIND is frozen', () => {
  assert.ok(Object.isFrozen(NOTIFICATION_KIND));
});


// ---------------------------------------------------------------------------
// TAB_STATUS — workspace dot colors
// ---------------------------------------------------------------------------

test('TAB_STATUS includes every state the workspace state machine produces', () => {
  // WORKING and ATTENTION are UI-only overlays from live session state.
  assert.deepEqual(Object.keys(TAB_STATUS).sort(), [
    'ACTIVE', 'ATTENTION', 'DONE', 'ERRORED', 'IDLE',
    'PROVISIONING', 'REVIEW', 'TERMINATED', 'WORKING',
  ]);
});

test('TAB_STATUS values are the wire strings', () => {
  assert.equal(TAB_STATUS.PROVISIONING, 'provisioning');
  assert.equal(TAB_STATUS.ACTIVE, 'active');
  assert.equal(TAB_STATUS.IDLE, 'idle');
  assert.equal(TAB_STATUS.REVIEW, 'review');
  assert.equal(TAB_STATUS.DONE, 'done');
  assert.equal(TAB_STATUS.TERMINATED, 'terminated');
  assert.equal(TAB_STATUS.ERRORED, 'errored');
  assert.equal(TAB_STATUS.WORKING, 'working');
  assert.equal(TAB_STATUS.ATTENTION, 'attention');
});

test('TAB_STATUS is frozen', () => {
  assert.ok(Object.isFrozen(TAB_STATUS));
});
