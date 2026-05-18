import { useEffect, useMemo, useRef, useState } from 'react';
import Bubble from './Bubble.jsx';
import Icon from './Icon.jsx';
import StickyHeader from './StickyHeader.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { formatToolUse, toolUseFilePath } from '../utils/formatToolUse.js';
import { MessageFilter } from '../utils/MessageFilter.js';
import { isPinnedToBottom, scrollToBottom } from '../utils/scrollUtils.js';
import {
  TOOL_DETAILS_COLLAPSE_THRESHOLD,
  TOOL_DETAILS_HARD_CAP,
  computeEventLogWindow,
  computeToolDetailsRender,
} from './eventLogTruncation.js';

export default function EventLog({
  entries,
  banner,
  searchQuery = '',
  searchCurrentIndex = 0,
  onSearchMatchCount,
  onOpenFile,
  footer = null,
  taskId = null,
}) {
  const containerRef = useRef(null);
  // Sticky-scroll intent. Starts true so the log opens at the
  // newest message; flipped by the operator's own scrolling (see
  // the scroll listener below). New content only yanks to the
  // bottom while this is true.
  const pinnedRef = useRef(true);
  const [showAll, setShowAll] = useState(false);
  // Dedupe is O(N) over the entire event list; without memoization
  // it re-runs every time the parent re-renders (tab switches,
  // workspace bumps, attention flips), even though ``entries`` is
  // unchanged. Memoizing on ``entries`` identity collapses that to
  // once-per-stream-update.
  const visibleEntries = useMemo(
    () => MessageFilter.dedupeUserEchoes(
      MessageFilter.hideInternalTaskNotifications(
        MessageFilter.dedupeRateLimitCycles(entries),
      ),
    ),
    [entries],
  );
  const window = useMemo(
    () => computeEventLogWindow(visibleEntries, showAll),
    [visibleEntries, showAll],
  );
  // Each operator prompt renders as a sticky section header (see
  // ``StickyPrompt`` / ``bubblesFor``). Native ``position: sticky``
  // stacking means: while you read a turn's replies its prompt is
  // pinned at the top; scroll up into the previous turn and that
  // turn's prompt pushes the current one off and takes the top —
  // the Claude VS Code plugin behaviour, no JS scroll math needed.
  // Track the operator's scroll intent: any time they scroll, note
  // whether they're (still) at the bottom. This survives content
  // growth because it's only updated by real scroll events, not by
  // the append itself — so "was the user at the bottom?" stays
  // accurate when the next message arrives.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) { return undefined; }
    const onScroll = () => { pinnedRef.current = isPinnedToBottom(node); };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, []);

  // New content / banner / tab switch: follow the bottom while the
  // tracked intent says "pinned". We deliberately use the intent
  // FLAG, not a live DOM read: on mount / tab switch the container
  // is at scrollTop 0, so a DOM-derived "are we at the bottom?"
  // check would say no and never scroll down (the reported
  // tab-switch bug). ``pinnedRef`` starts true and only flips when
  // the operator actually scrolls up (listener above), so a fresh
  // log opens pinned and a tab switch lands at the newest message.
  useEffect(() => {
    if (pinnedRef.current) {
      scrollToBottom(containerRef.current);
    }
  }, [window.visible.length, banner]);

  // Switching tasks must ALWAYS land at the newest message. App
  // remounts SessionDetail (and thus EventLog) per task, so a fresh
  // ``pinnedRef`` starts true here — re-arm + jump on the taskId
  // change too, for the rare reuse-without-remount path.
  useEffect(() => {
    pinnedRef.current = true;
    scrollToBottom(containerRef.current);
  }, [taskId]);

  // Stay glued to the newest message while the operator's intent is
  // "pinned" — even when content/layout grows AFTER the count-based
  // effect already ran. On a tab switch the log is empty, then the
  // task's history streams in async and late layout shifts (sticky
  // prompt headers, tool-detail blocks, the trailing working
  // indicator, image loads) push scrollHeight down with no change to
  // the visible-event count, so the length-keyed effect never
  // re-fires and the log was left mid-scroll. A MutationObserver
  // catches every one of those DOM growths; ``pinnedRef`` (flipped
  // false only by a real user scroll-up) gates it, so this follows
  // the stream without fighting the operator.
  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof MutationObserver === 'undefined') { return undefined; }
    const observer = new MutationObserver(() => {
      if (pinnedRef.current) { scrollToBottom(node); }
    });
    observer.observe(node, {
      childList: true, subtree: true, characterData: true,
    });
    return () => observer.disconnect();
  }, []);

  // ----- chat search highlighting + navigation ------------------
  // We do this as a post-render DOM walk rather than threading the
  // query into every bubble's children — bubble bodies are arbitrary
  // React subtrees (markdown, tool widgets, diffs) and walking the
  // pre-render tree to substring-match would mean re-implementing
  // half of React's renderer. Reading ``textContent`` from the
  // already-rendered DOM is one cheap pass and stays correct no
  // matter what shape a bubble's children take.
  //
  // After tagging matches, we accent ``searchCurrentIndex`` with
  // ``.bubble--match-current`` and scroll it into view — that's
  // what the prev/next buttons in ChatSearch drive.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      if (typeof onSearchMatchCount === 'function') {
        onSearchMatchCount(0);
      }
      return;
    }
    const query = (searchQuery || '').trim().toLowerCase();
    const bubbles = node.querySelectorAll('.bubble');
    if (!query) {
      bubbles.forEach((b) => {
        b.classList.remove(
          'bubble--match', 'bubble--no-match', 'bubble--match-current',
        );
      });
      node.classList.remove('is-searching');
      if (typeof onSearchMatchCount === 'function') {
        onSearchMatchCount(0);
      }
      return;
    }
    node.classList.add('is-searching');
    const matched = [];
    bubbles.forEach((b) => {
      const haystack = (b.textContent || '').toLowerCase();
      if (haystack.includes(query)) {
        b.classList.add('bubble--match');
        b.classList.remove('bubble--no-match', 'bubble--match-current');
        matched.push(b);
      } else {
        b.classList.add('bubble--no-match');
        b.classList.remove('bubble--match', 'bubble--match-current');
      }
    });
    if (matched.length > 0) {
      const clampedIndex = Math.max(
        0, Math.min(searchCurrentIndex, matched.length - 1),
      );
      const current = matched[clampedIndex];
      current.classList.add('bubble--match-current');
      // Scroll into view smoothly so the operator can follow the
      // jump. ``center`` keeps the active bubble vertically centred
      // — the eye doesn't have to find it after each press.
      current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    if (typeof onSearchMatchCount === 'function') {
      onSearchMatchCount(matched.length);
    }
  });

  const bannerBubble = banner && <Bubble kind={BUBBLE_KIND.SYSTEM}>{banner}</Bubble>;
  const eventBubbles = useMemo(
    () => window.visible.flatMap(
      (entry, index) => bubblesFor(entry, index, onOpenFile),
    ),
    [window.visible, onOpenFile],
  );
  // Group the flat bubble stream into per-prompt turns. Each turn is a
  // ``StickyPrompt`` followed by every bubble until the next prompt.
  // Wrapping a turn in its own block is what makes the sticky prompt
  // behave like a section header: ``position: sticky`` is bounded by
  // its containing block, so a prompt only pins while ITS turn is on
  // screen and is pushed off the top by the adjacent turn's prompt as
  // you scroll past the turn boundary. Without the per-turn wrapper
  // every prompt shares one containing block (the whole log) and they
  // all stack at ``top: 0`` — the latest one always wins and previous
  // prompts never take the top when you scroll up.
  const turns = useMemo(() => groupIntoTurns(eventBubbles), [eventBubbles]);
  const hiddenCount = window.hidden;
  const showOlderButton = hiddenCount > 0 ? (
    <button
      type="button"
      className="event-log-show-older"
      onClick={() => setShowAll(true)}
    >
      {`Show ${hiddenCount} earlier event${hiddenCount === 1 ? '' : 's'}`}
    </button>
  ) : null;
  return (
    <div id="event-log" ref={containerRef}>
      {bannerBubble}
      {showOlderButton}
      {turns.preamble.length > 0 && (
        <div className="chat-turn chat-turn--preamble">{turns.preamble}</div>
      )}
      {turns.turns.map((turn) => (
        <div className="chat-turn" key={turn[0].key}>{turn}</div>
      ))}
      {/* The working indicator lives INSIDE the scroll container as
          the last entry, so it scrolls with the messages and trails
          the newest one instead of floating over the chat. */}
      {footer}
    </div>
  );
}

// Split the flat bubble list at every ``StickyPrompt`` boundary.
// Bubbles emitted before the first prompt (session init, preflight
// clone progress, replayed history before the operator's first
// message) have no owning turn — they go in ``preamble`` and render
// without a sticky header.
function groupIntoTurns(bubbles) {
  const preamble = [];
  const turns = [];
  let current = null;
  for (const el of bubbles) {
    if (el && el.type === StickyPrompt) {
      current = [el];
      turns.push(current);
    } else if (current) {
      current.push(el);
    } else {
      preamble.push(el);
    }
  }
  return { preamble, turns };
}

function bubblesFor(entry, index, onOpenFile) {
  if (entry?.source === ENTRY_SOURCE.LOCAL) {
    const text = entry.text || '';
    const count = Number(entry.imageCount || 0);
    const display = count > 0
      ? `${text}${text ? '\n' : ''}(${count} image${count === 1 ? '' : 's'} attached)`
      : text;
    if ((entry.kind || BUBBLE_KIND.SYSTEM) === BUBBLE_KIND.USER) {
      return [<StickyPrompt key={`local-${index}`} text={display} />];
    }
    return [
      <Bubble key={`local-${index}`} kind={entry.kind || BUBBLE_KIND.SYSTEM}>
        {display}
      </Bubble>,
    ];
  }
  return serverBubblesFor(
    entry?.raw,
    index,
    entry?.source === ENTRY_SOURCE.HISTORY,
    onOpenFile,
  );
}

function serverBubblesFor(raw, index, isHistory = false, onOpenFile) {
  if (!raw || !raw.type) { return []; }
  switch (raw.type) {
    case CLAUDE_EVENT.SYSTEM:
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.INIT) {
        const sid = raw.session_id || '';
        const sidShort = sid ? sid.slice(0, 8) : '(none yet)';
        const sidFull = sid || '(unknown)';
        return [
          <Bubble key={keyOf(raw, index, 'sys')} kind={BUBBLE_KIND.SYSTEM}>
            <span title={`Full session id: ${sidFull}`}>
              {`Claude session started · ${sidShort}${sid ? '…' : ''}`}
            </span>
          </Bubble>,
        ];
      }
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT) {
        const message = String(raw.message || '').trim();
        if (!message) { return []; }
        // Kato-synthetic provisioning step. Renders as a system
        // bubble so the operator sees clone progress in the chat
        // tab without having to look at the orchestrator activity
        // feed in the right pane.
        return [
          <Bubble key={keyOf(raw, index, 'preflight')} kind={BUBBLE_KIND.SYSTEM}>
            {message}
          </Bubble>,
        ];
      }
      return [];
    case CLAUDE_EVENT.ASSISTANT:
      return assistantBubbles(raw, index, onOpenFile);
    case CLAUDE_EVENT.USER:
      // Render every ``user`` envelope kato sent to Claude — typed
      // messages, kato-injected initial prompts (implementation /
      // review-fix), and history replay all flow through here. The
      // operator wants visibility into "what caused Claude to do
      // X", so kato's prompts must show up in the chat just like
      // typed messages do. Duplicate echoes of typed messages are
      // suppressed upstream by ``MessageFilter.dedupeUserEchoes``.
      return userBubbles(raw, index);
    case CLAUDE_EVENT.STREAM_EVENT:
      return [];
    case CLAUDE_EVENT.RESULT:
      return resultBubbles(raw, index);
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
    case CLAUDE_EVENT.PERMISSION_RESPONSE:
      return [];
    default: {
      // Hidden chat-event types (``rate_limit_event``, etc.) live in
      // MessageFilter — the canonical "what's noise vs signal" list.
      // Without this guard the default case below would render every
      // unknown type as a TOOL bubble, including pure plan-throttle
      // metadata the operator doesn't need to see.
      if (MessageFilter.isChatEventHidden(raw.type)) {
        return [];
      }
      const eventLabel = raw.subtype
        ? `${raw.type} / ${raw.subtype}`
        : String(raw.type || '');
      return [
        <Bubble key={keyOf(raw, index, 'tool')} kind={BUBBLE_KIND.TOOL}>
          {eventLabel}
        </Bubble>,
      ];
    }
  }
}

function assistantBubbles(raw, index, onOpenFile) {
  const message = raw.message || {};
  const content = Array.isArray(message.content) ? message.content : [];
  const textPieces = [];
  const toolBubbles = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') { continue; }
    if (block.type === 'text' && block.text) {
      textPieces.push(block.text);
    } else if (block.type === 'tool_use') {
      const toolName = block.name || 'tool';
      const formatted = formatToolUse(toolName, block.input);
      // ``formatted`` is either a string (header-only) or
      // ``{ summary, details }``. The details block renders as
      // monospace code under the header — for Edit/Write/MultiEdit
      // this is the full before/after snippet, for Bash it's the
      // remaining lines of a multi-line command, etc.
      const summary = typeof formatted === 'string'
        ? formatted
        : (formatted?.summary || '');
      const details = typeof formatted === 'object' && formatted
        ? formatted.details
        : '';
      // File-touching tools (Read/Write/Edit/MultiEdit/Notebook)
      // get a one-click "open this file" affordance next to the
      // path — opens it in the editor pane, same as a left-tree
      // click, so the operator can jump straight to what the agent
      // just touched without hunting for it in the tree.
      const filePath = toolUseFilePath(toolName, block.input);
      const revealBtn = filePath && typeof onOpenFile === 'function' ? (
        <button
          type="button"
          className="bubble-tool-reveal tooltip-end"
          data-tooltip="Open this file in the editor pane."
          aria-label={`Open ${filePath}`}
          onClick={() => onOpenFile({ absolutePath: filePath })}
        >
          <Icon name="file" />
        </button>
      ) : null;
      toolBubbles.push(
        <Bubble
          key={keyOf(raw, index, `tool-${block.id || toolBubbles.length}`)}
          kind={BUBBLE_KIND.TOOL}
        >
          <span className="bubble-tool-summary">
            {`→ ${summary}`}
            {revealBtn}
          </span>
          {details && <ToolDetails details={details} />}
        </Bubble>,
      );
    }
  }
  if (textPieces.length === 0) { return toolBubbles; }
  return [
    ...toolBubbles,
    <Bubble key={keyOf(raw, index, 'assistant')} kind={BUBBLE_KIND.ASSISTANT}>
      {textPieces.join('\n')}
    </Bubble>,
  ];
}

function userBubbles(raw, index) {
  const message = raw.message || {};
  const rawContent = message.content;
  const content = Array.isArray(rawContent) ? rawContent : [];
  const textPieces = content
    .filter((b) => b && b.type === 'text' && b.text)
    .map((b) => b.text);
  if (typeof rawContent === 'string' && rawContent.trim()) {
    textPieces.push(rawContent);
  }
  // Show image-bearing user envelopes too — surface the image count
  // inline so the operator can confirm their attachment landed.
  const imageCount = content.filter((b) => b && b.type === 'image').length;
  if (textPieces.length === 0 && imageCount === 0) { return []; }
  const text = textPieces.join('\n');
  const display = imageCount > 0
    ? `${text}${text ? '\n' : ''}(${imageCount} image${imageCount === 1 ? '' : 's'} attached)`
    : text;
  return [
    <StickyPrompt key={keyOf(raw, index, 'user')} text={display} />,
  ];
}


// One operator prompt, rendered as a sticky section header. Long
// prompts collapse to three lines with the same expand button style
// used by tool-output snippets.
function StickyPrompt({ text }) {
  const [expanded, setExpanded] = useState(false);
  const promptText = String(text || '');
  const lineCount = promptText.split('\n').length;
  const isCollapsible = lineCount > 3 || promptText.length > 180;
  const promptClass = [
    'chat-sticky-prompt',
    expanded ? 'is-expanded' : '',
    isCollapsible ? 'is-collapsible' : '',
  ].filter(Boolean).join(' ');
  const textWrapClass = [
    'chat-sticky-prompt-text-wrap',
    isCollapsible && !expanded ? 'is-collapsed' : '',
  ].filter(Boolean).join(' ');
  const expandLabel = expanded ? 'Click to collapse' : 'Click to expand';
  const expandButton = isCollapsible ? (
    <button
      type="button"
      className="bubble-tool-details-expand chat-sticky-prompt-expand"
      onClick={() => setExpanded((current) => !current)}
      aria-expanded={expanded}
    >
      {expandLabel}
    </button>
  ) : null;

  return (
    <StickyHeader className={promptClass}>
      <div className="chat-sticky-prompt-toggle">
        <span className="chat-sticky-prompt-label">You asked</span>
        <span className={textWrapClass}>
          <span className="chat-sticky-prompt-text">{promptText}</span>
          {expandButton}
        </span>
      </div>
    </StickyHeader>
  );
}

function resultBubbles(raw, index) {
  const ok = !raw.is_error;
  const summary = raw.result || (ok ? 'completed' : 'failed');
  const bubbleKind = ok ? BUBBLE_KIND.SYSTEM : BUBBLE_KIND.ERROR;
  const resultLabel = ok ? 'success' : 'error';
  const resultText = `(result: ${resultLabel}) ${summary}`;
  return [
    <Bubble
      key={keyOf(raw, index, 'result')}
      kind={bubbleKind}
    >
      {resultText}
    </Bubble>,
  ];
}

function keyOf(raw, index, slot) {
  return `${index}:${raw.uuid || raw.session_id || ''}:${slot}`;
}


// Render the monospace tool-details block, collapsed when the
// payload is huge. The truncation rules + thresholds live in the
// sibling ``eventLogTruncation.js`` so the rendering and the rules
// can evolve independently and stay testable without a JSX
// transformer.

function ToolDetails({ details }) {
  const [expanded, setExpanded] = useState(false);
  const lines = useMemo(() => details.split('\n'), [details]);
  const renderInfo = useMemo(
    () => computeToolDetailsRender(lines, expanded),
    [lines, expanded],
  );
  // ``computeToolDetailsToggleLabel`` is unused now — the wrapper
  // handles clip-and-fade visuals and the button label is just
  // "Click to expand" / "Click to collapse" below.
  const overflowNotice = renderInfo.overflowed ? (
    <p className="bubble-tool-details-overflow">
      {`Output truncated at ${TOOL_DETAILS_HARD_CAP.toLocaleString()} lines `
       + `(${(lines.length - TOOL_DETAILS_HARD_CAP).toLocaleString()} more `
       + `not shown). Inspect the agent transcript on disk for the full body.`}
    </p>
  ) : null;
  const overflows = lines.length > TOOL_DETAILS_COLLAPSE_THRESHOLD;
  const isCollapsed = overflows && !expanded;
  const wrapClass = [
    'bubble-tool-details-wrap',
    isCollapsed ? 'is-collapsed' : '',
  ].filter(Boolean).join(' ');
  const expandButton = overflows ? (
    <button
      type="button"
      className="bubble-tool-details-expand"
      onClick={() => setExpanded((current) => !current)}
    >
      {expanded ? 'Click to collapse' : 'Click to expand'}
    </button>
  ) : null;
  return (
    <>
      <div className={wrapClass}>
        <pre className="bubble-tool-details">
          {renderInfo.visible.map((line, lineIdx) => (
            <span
              key={lineIdx}
              className={`bubble-tool-details-line ${_diffLineKind(line)}`}
            >
              {line || ' '}
              {'\n'}
            </span>
          ))}
        </pre>
        {expandButton}
      </div>
      {overflowNotice}
    </>
  );
}


// Classify a tool-details line by its prefix so the renderer can
// tint added vs removed lines red/green. Prefixes match what
// ``formatToolUse`` produces:
//   ``+ `` — added line (Edit new_string, Write content)
//   ``- `` — removed line (Edit old_string)
//   ``---`` — separator between MultiEdit edits
function _diffLineKind(line) {
  if (line.startsWith('+ ')) { return 'added'; }
  if (line.startsWith('- ')) { return 'removed'; }
  if (line === '---') { return 'separator'; }
  return 'context';
}
