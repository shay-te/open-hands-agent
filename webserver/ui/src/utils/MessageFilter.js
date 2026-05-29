// Single source of truth for "what events does the UI filter, hide, or
// rate-limit?" Every filtering decision in the planning UI goes through
// one of these methods. Adding a new noise pattern? It belongs here, not
// scattered across hooks and components.

import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { messageContentText } from './messageContent.js';

const HEARTBEAT_MESSAGE_PREFIX = 'Idle · next scan in';
const RATE_LIMIT_TEXT_PREFIX = "You've hit your limit";
const TASK_NOTIFICATION_PREFIX = '<task-notification>';

// Wire-protocol events kato never wants to render as chat bubbles.
// `stream_event` is incremental token streaming — pre-render noise.
// `permission_*` is handled by the modal/audit-bubble path, not the
// transcript. `rate_limit_event` is plan-throttle metadata — informative
// for ops but not a chat message.
const HIDDEN_CHAT_EVENT_TYPES = new Set([
  CLAUDE_EVENT.STREAM_EVENT,
  CLAUDE_EVENT.PERMISSION_REQUEST,
  CLAUDE_EVENT.CONTROL_REQUEST,
  CLAUDE_EVENT.PERMISSION_RESPONSE,
  'rate_limit_event',
]);

// Notification kinds that fire often during a normal turn. They get
// per-task throttling so a burst of permission requests during one turn
// produces one ping, not a dozen.
const RATE_LIMITED_NOTIFICATION_KINDS = new Set([
  NOTIFICATION_KIND.ATTENTION,
]);
const NOTIFICATION_RATE_LIMIT_MS = 8_000;

export class MessageFilter {
  // ----- status-feed (top status bar + orchestrator activity feed) -----

  // True when the entry is a kato.heartbeat ping ("Idle · next scan in
  // Xs"). Heartbeats update `latest` so the bar shows a live countdown,
  // but they are NOT pushed to the rolling history — otherwise an idle
  // window evicts real activity from the ring buffer.
  static isStatusFeedHeartbeat(entry) {
    const message = String(entry?.message || '');
    return message.startsWith(HEARTBEAT_MESSAGE_PREFIX);
  }

  // ----- chat transcript (EventLog) -----

  // True when an SSE/Claude event should NOT produce a chat bubble. Used
  // by EventLog before rendering. Returning true keeps the event in the
  // raw stream (other handlers may still react to it) but suppresses it
  // from the visible transcript.
  static isChatEventHidden(eventType) {
    return HIDDEN_CHAT_EVENT_TYPES.has(eventType);
  }

  static hideInternalTaskNotifications(entries) {
    return (entries || []).filter((entry) => {
      if (!_isServerUserEntry(entry)) { return true; }
      return !_isInternalTaskNotificationText(_userEventText(entry));
    });
  }

  // Collapse SDK rate-limit retry loops to a single visible cycle.
  //
  // When the Claude SDK hits its plan limit it retries internally and
  // emits the same triplet — assistant("You've hit your limit · resets X")
  // + result(error, same text) + system.init — once per attempt. The
  // operator only needs to see it once. We keep the first assistant +
  // first result bubble of the burst and drop subsequent duplicates plus
  // the init bubbles that come from each retry's fresh subprocess. Any
  // non-rate-limit entry resets the state so a *later* limit hit (after
  // real chat resumes) renders fresh.
  static dedupeRateLimitCycles(entries) {
    const result = [];
    let firstAssistantSeen = false;
    let firstResultSeen = false;
    let inCycle = false;
    for (const entry of entries) {
      const kind = classifyRateLimitEntry(entry);
      if (kind === 'rl-assistant') {
        if (firstAssistantSeen) { continue; }
        firstAssistantSeen = true;
        inCycle = true;
        result.push(entry);
      } else if (kind === 'rl-result') {
        if (firstResultSeen) { continue; }
        firstResultSeen = true;
        inCycle = true;
        result.push(entry);
      } else if (kind === 'sys-init' && inCycle) {
        continue;
      } else {
        firstAssistantSeen = false;
        firstResultSeen = false;
        inCycle = false;
        result.push(entry);
      }
    }
    return result;
  }

  // Drop server ``user`` echoes that duplicate an immediately-preceding
  // local user bubble.
  //
  // Why: when the operator types in the composer, ``SessionDetail``
  // appends a local ``USER`` bubble for instant feedback, then POSTs
  // to kato which forwards to Claude. Claude echoes the message back
  // as a server ``user`` event. Without dedupe the operator sees
  // "their" message twice.
  //
  // We DO want to render server user events that *don't* match a
  // recent local bubble — those are kato-injected prompts (initial
  // implementation, review-fix) the operator should see, since they
  // explain "why did Claude just start doing X?".
  //
  // Match rule: a server user event with text identical to the most
  // recent local user bubble within the last 4 entries gets dropped.
  // Bounded lookback so a server echo only suppresses ITS local twin,
  // not random matching text deeper in the transcript.
  static dedupeUserEchoes(entries) {
    const LOOKBACK = 4;
    const result = [];
    for (const entry of entries) {
      if (_isServerUserEntry(entry)) {
        const serverText = _userEventText(entry);
        if (serverText && _hasMatchingLocalUser(result, serverText, LOOKBACK)) {
          continue;
        }
      }
      result.push(entry);
    }
    return result;
  }

  // ----- browser notifications -----

  // Decides whether a `notify()` call for `(taskId, kind)` should fire.
  // Returns `{ allow: bool, reason?: string }`. The caller passes:
  //   - kindPrefs: per-kind on/off map from useNotifications
  //   - activeTaskId: the currently-focused task (suppress same-task
  //                   notifications when the operator is already looking
  //                   at the chat that produced them)
  //   - lastFireMap:  per-key (kind+taskId) timestamp of last fire,
  //                   maintained by the caller as a Map
  //   - now:          monotonic millis (defaults to Date.now())
  //
  // The lastFireMap is mutated in place when allow=true so the caller
  // doesn't need to track the bookkeeping separately.
  static shouldFireNotification({
    kind,
    taskId,
    kindPrefs,
    activeTaskId,
    lastFireMap,
    documentHidden = false,
    now = Date.now(),
  }) {
    if (!kindPrefs || kindPrefs[kind] === false) {
      return { allow: false, reason: 'kind disabled' };
    }
    // Operator is already looking at this task's chat — no need to
    // notify them about something they're watching live.
    if (!documentHidden && taskId && taskId === activeTaskId) {
      return { allow: false, reason: 'active task in foreground' };
    }
    if (RATE_LIMITED_NOTIFICATION_KINDS.has(kind) && lastFireMap) {
      const key = `${kind}:${taskId || ''}`;
      const last = lastFireMap.get(key);
      if (last && now - last < NOTIFICATION_RATE_LIMIT_MS) {
        return { allow: false, reason: 'rate-limited' };
      }
      lastFireMap.set(key, now);
    }
    return { allow: true };
  }
}

// Classify an entry against the rate-limit retry pattern. Returns
//   'rl-assistant' — assistant bubble whose only text is the limit notice
//   'rl-result'    — error result whose payload is the limit notice
//   'sys-init'     — the system.init bubble that fires per fresh subprocess
//   'other'        — anything else (real chat content, history, locals)
function classifyRateLimitEntry(entry) {
  if (!entry || entry.source !== ENTRY_SOURCE.SERVER) { return 'other'; }
  const raw = entry.raw;
  if (!raw) { return 'other'; }
  if (raw.type === CLAUDE_EVENT.SYSTEM
      && raw.subtype === CLAUDE_SYSTEM_SUBTYPE.INIT) {
    return 'sys-init';
  }
  if (raw.type === CLAUDE_EVENT.ASSISTANT) {
    const blocks = Array.isArray(raw.message?.content) ? raw.message.content : [];
    let hasRateLimitText = false;
    let hasOtherContent = false;
    for (const block of blocks) {
      if (!block || typeof block !== 'object') { continue; }
      if (block.type === 'text') {
        if (isRateLimitText(block.text)) {
          hasRateLimitText = true;
        } else if (block.text) {
          hasOtherContent = true;
        }
      } else if (block.type === 'tool_use') {
        hasOtherContent = true;
      }
    }
    if (hasRateLimitText && !hasOtherContent) { return 'rl-assistant'; }
    return 'other';
  }
  if (raw.type === CLAUDE_EVENT.RESULT && raw.is_error
      && isRateLimitText(raw.result)) {
    return 'rl-result';
  }
  return 'other';
}

function isRateLimitText(text) {
  return String(text || '').trim().startsWith(RATE_LIMIT_TEXT_PREFIX);
}

function _isServerUserEntry(entry) {
  if (!entry || entry.source === ENTRY_SOURCE.LOCAL) { return false; }
  const raw = entry.raw;
  return !!raw && raw.type === CLAUDE_EVENT.USER;
}

function _userEventText(entry) {
  const message = entry?.raw?.message || {};
  // String content short-circuits; array content goes through the
  // shared text-block filter+join.
  if (typeof message.content === 'string') {
    return message.content.trim();
  }
  return messageContentText(message);
}

function _isInternalTaskNotificationText(text) {
  return String(text || '').trim().startsWith(TASK_NOTIFICATION_PREFIX);
}

function _hasMatchingLocalUser(recentEntries, serverText, lookback) {
  const start = Math.max(0, recentEntries.length - lookback);
  for (let i = recentEntries.length - 1; i >= start; i -= 1) {
    const entry = recentEntries[i];
    if (entry?.source !== ENTRY_SOURCE.LOCAL) { continue; }
    if (entry.kind !== BUBBLE_KIND.USER) { continue; }
    if (String(entry.text || '').trim() === serverText) {
      return true;
    }
  }
  return false;
}

// Re-exports for tests and outside consumers that just want the constants.
export const _internals = {
  HEARTBEAT_MESSAGE_PREFIX,
  HIDDEN_CHAT_EVENT_TYPES,
  RATE_LIMITED_NOTIFICATION_KINDS,
  NOTIFICATION_RATE_LIMIT_MS,
  RATE_LIMIT_TEXT_PREFIX,
  TASK_NOTIFICATION_PREFIX,
};
