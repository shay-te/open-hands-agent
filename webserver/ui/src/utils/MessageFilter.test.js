import assert from 'node:assert/strict';
import test from 'node:test';

import { MessageFilter } from './MessageFilter.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';


function _local(text, kind = BUBBLE_KIND.USER) {
  return { source: ENTRY_SOURCE.LOCAL, kind, text };
}

function _serverUser(text) {
  return {
    source: ENTRY_SOURCE.SERVER,
    raw: {
      type: CLAUDE_EVENT.USER,
      message: { content: [{ type: 'text', text }] },
    },
  };
}

function _serverAssistant(text) {
  return {
    source: ENTRY_SOURCE.SERVER,
    raw: {
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [{ type: 'text', text }] },
    },
  };
}


// ----- the new dedupeUserEchoes filter -----

test('dedupeUserEchoes drops server echo when matching local bubble immediately precedes', function () {
  const entries = [
    _local('hi claude'),
    _serverUser('hi claude'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  assert.equal(result.length, 1);
  assert.equal(result[0].source, ENTRY_SOURCE.LOCAL);
});

test('dedupeUserEchoes keeps server echo with no preceding local bubble (kato-injected prompt)', function () {
  // This is the key user-facing fix: kato sends an initial implementation
  // prompt that arrives as a server `user` event without any local echo.
  // It must remain visible.
  const entries = [
    _serverUser('Implement task PROJ-1: fix the auth flow…'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  assert.equal(result.length, 1);
  assert.equal(result[0].source, ENTRY_SOURCE.SERVER);
});

test('dedupeUserEchoes keeps server echo when local user text differs', function () {
  const entries = [
    _local('please look at this'),
    _serverUser('completely different prompt that kato sent'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  assert.equal(result.length, 2);
});

test('dedupeUserEchoes ignores local bubbles older than the lookback window', function () {
  // Lookback is 4 entries — anything older shouldn't suppress.
  const entries = [
    _local('older message that happens to match'),
    _serverAssistant('a'),
    _serverAssistant('b'),
    _serverAssistant('c'),
    _serverAssistant('d'),
    _serverAssistant('e'),
    _serverUser('older message that happens to match'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  // Server user at the end stays — it's a kato-injected prompt that
  // happens to share text with an old local bubble.
  assert.equal(
    result.filter((e) => e.source === ENTRY_SOURCE.SERVER && e.raw?.type === CLAUDE_EVENT.USER).length,
    1,
  );
});

test('dedupeUserEchoes only matches USER-kind local bubbles', function () {
  // A SYSTEM-kind local bubble (e.g. "✓ delivered") shouldn't suppress
  // a server user echo even if the texts happen to match.
  const entries = [
    _local('hello', BUBBLE_KIND.SYSTEM),
    _serverUser('hello'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  assert.equal(result.length, 2);
});

test('dedupeUserEchoes leaves non-user server events alone', function () {
  const entries = [
    _local('typed'),
    _serverUser('typed'),
    _serverAssistant('reply'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  assert.equal(result.length, 2);
  assert.equal(result[0].source, ENTRY_SOURCE.LOCAL);
  assert.equal(result[1].raw.type, CLAUDE_EVENT.ASSISTANT);
});

test('dedupeUserEchoes preserves multiple distinct user events in a row', function () {
  // Initial prompt + subsequent typed message — two distinct user
  // entries that must both appear.
  const entries = [
    _serverUser('Implement task PROJ-1…'),
    _local('great, but use TypeScript'),
    _serverUser('great, but use TypeScript'),
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  // Initial prompt + local typed (server echo deduped) → 2 entries.
  assert.equal(result.length, 2);
  assert.equal(result[0].source, ENTRY_SOURCE.SERVER);
  assert.equal(result[1].source, ENTRY_SOURCE.LOCAL);
});

test('dedupeUserEchoes ignores whitespace differences between local and server', function () {
  const entries = [
    _local('hi claude'),
    _serverUser('hi claude   '),  // trailing whitespace from streaming buffer
  ];
  const result = MessageFilter.dedupeUserEchoes(entries);
  assert.equal(result.length, 1);
});

test('hideInternalTaskNotifications drops task-notification user envelopes', function () {
  const entries = [
    _serverUser('<task-notification>\n<status>completed</status>\n</task-notification>'),
    _serverUser('real prompt'),
    _serverAssistant('reply'),
  ];
  const result = MessageFilter.hideInternalTaskNotifications(entries);

  assert.equal(result.length, 2);
  assert.equal(result[0].raw.message.content[0].text, 'real prompt');
  assert.equal(result[1].raw.type, CLAUDE_EVENT.ASSISTANT);
});

test('hideInternalTaskNotifications drops string-content task notifications', function () {
  const entries = [{
    source: ENTRY_SOURCE.HISTORY,
    raw: {
      type: CLAUDE_EVENT.USER,
      message: { content: '<task-notification>done</task-notification>' },
    },
  }];

  assert.deepEqual(MessageFilter.hideInternalTaskNotifications(entries), []);
});
