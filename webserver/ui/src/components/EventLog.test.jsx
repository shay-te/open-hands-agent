// Tests for EventLog. Bug surfaced during writing: the file used
// ``TOOL_DETAILS_COLLAPSE_THRESHOLD`` without importing it — any
// tool_use bubble with >40 lines of details would throw
// ReferenceError at render time. Fixed in EventLog.jsx; the
// "long tool-details rendering" test below pins the regression.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Keep the real pin math (other tests don't touch scrolling) but spy
// scrollToBottom so the task-switch test can assert the log is
// yanked to the newest message on tab change.
vi.mock('../utils/scrollUtils.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, scrollToBottom: vi.fn() };
});

import EventLog from './EventLog.jsx';
import { scrollToBottom } from '../utils/scrollUtils.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';


function _local(kind, text) {
  return { source: ENTRY_SOURCE.LOCAL, kind, text };
}

function _server(raw) {
  return { source: ENTRY_SOURCE.SERVER, raw };
}


describe('EventLog — banner + empty state', () => {

  test('renders the banner as a system bubble', () => {
    render(<EventLog entries={[]} banner="Connecting…" />);
    expect(screen.getByText('Connecting…')).toBeInTheDocument();
  });

  test('renders nothing meaningful when entries+banner both empty', () => {
    const { container } = render(<EventLog entries={[]} banner={null} />);
    // The outer #event-log div is present but has no bubble children.
    const log = container.querySelector('#event-log');
    expect(log).toBeInTheDocument();
    expect(log.querySelectorAll('.bubble').length).toBe(0);
  });
});


describe('EventLog — local entries', () => {

  test('LOCAL user prompt renders as a sticky prompt', () => {
    const { container } = render(
      <EventLog entries={[_local(BUBBLE_KIND.USER, 'hello there')]} />,
    );
    // An operator prompt is its turn's sticky section header — the
    // sole representation, not a separate chat bubble.
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('hello there');
  });

  test('LOCAL bubble with image count appends "(N images attached)"', () => {
    const entry = {
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text: 'check this',
      imageCount: 2,
    };
    const { container } = render(<EventLog entries={[entry]} />);
    const prompt = container.querySelector('.chat-sticky-prompt-text');
    expect(prompt).toHaveTextContent('check this');
    expect(prompt).toHaveTextContent('2 images attached');
  });

  test('LOCAL bubble with 1 image uses singular "image"', () => {
    const entry = {
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text: '',
      imageCount: 1,
    };
    render(<EventLog entries={[entry]} />);
    expect(screen.getByText(/1 image attached/)).toBeInTheDocument();
  });
});


describe('EventLog — server event rendering', () => {

  test('SYSTEM init shows agent session id', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
      [AGENT_SESSION_ID]: 'sess-abc-123',
    })]} />);
    expect(screen.getByText(/Claude session started.*sess-abc/)).toBeInTheDocument();
  });

  test('SYSTEM init with missing agent session id falls back to "(none yet)"', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
    })]} />);
    expect(screen.getByText(/none yet/)).toBeInTheDocument();
  });

  test('SYSTEM preflight renders the message', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT,
      message: 'cloning client repo…',
    })]} />);
    expect(screen.getByText('cloning client repo…')).toBeInTheDocument();
  });

  test('SYSTEM with unrecognised subtype renders nothing', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: 'mystery_subtype',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('ASSISTANT with text content renders the text', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [{ type: 'text', text: "I'll fix the bug" }] },
    })]} />);
    expect(screen.getByText("I'll fix the bug")).toBeInTheDocument();
  });

  test('ASSISTANT with tool_use renders a tool bubble with the summary', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    // Bash formatter produces "$ ls"; the bubble prefixes with "→ ".
    expect(container.querySelector('.bubble-tool-summary')).toBeInTheDocument();
    expect(container.querySelector('.bubble-tool-summary').textContent)
      .toMatch(/→.*\$.*ls/);
  });

  test('file tool_use shows a reveal button that opens the file', () => {
    const onOpenFile = vi.fn();
    render(<EventLog onOpenFile={onOpenFile} entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Write',
          input: { file_path: '/repo/src/app.py', content: 'x' } },
      ] },
    })]} />);
    const btn = screen.getByRole('button', { name: 'Open /repo/src/app.py' });
    fireEvent.click(btn);
    expect(onOpenFile).toHaveBeenCalledWith({ absolutePath: '/repo/src/app.py' });
  });

  test('no reveal button when onOpenFile is not provided', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Read',
          input: { file_path: '/repo/x.py' } },
      ] },
    })]} />);
    expect(
      screen.queryByRole('button', { name: /^Open / }),
    ).not.toBeInTheDocument();
  });

  test('non-file tool (Bash) has no reveal button even with onOpenFile', () => {
    render(<EventLog onOpenFile={vi.fn()} entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    expect(
      screen.queryByRole('button', { name: /^Open / }),
    ).not.toBeInTheDocument();
  });

  test('ASSISTANT with mixed text + tool_use renders BOTH bubbles', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'text', text: 'running ls' },
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    expect(screen.getByText('running ls')).toBeInTheDocument();
    expect(container.querySelector('.bubble-tool-summary')).toBeInTheDocument();
  });

  test('USER text content renders as a sticky prompt', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [{ type: 'text', text: 'fix this' }] },
    })]} />);
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('fix this');
  });

  test('USER string content renders as a sticky prompt', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: 'restart prompt' },
    })]} />);
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('restart prompt');
  });

  test('USER task-notification content is hidden from prompts', () => {
    const { container } = render(<EventLog entries={[
      _server({
        type: CLAUDE_EVENT.USER,
        message: {
          content: [{
            type: 'text',
            text: '<task-notification><status>completed</status></task-notification>',
          }],
        },
      }),
      _server({
        type: CLAUDE_EVENT.USER,
        message: { content: [{ type: 'text', text: 'real prompt' }] },
      }),
    ]} />);
    const prompts = container.querySelectorAll('.chat-sticky-prompt-text');

    expect(prompts.length).toBe(1);
    expect(prompts[0]).toHaveTextContent('real prompt');
    expect(container).not.toHaveTextContent('task-notification');
  });

  test('long USER prompt collapses behind the snippet-style expand button', () => {
    const longPrompt = [
      'line one',
      'line two',
      'line three',
      'line four',
    ].join('\n');
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [{ type: 'text', text: longPrompt }] },
    })]} />);
    const wrap = container.querySelector('.chat-sticky-prompt-text-wrap');
    const button = screen.getByRole('button', { name: 'Click to expand' });

    expect(wrap).toHaveClass('is-collapsed');
    expect(button).toHaveClass('bubble-tool-details-expand');
    fireEvent.click(button);
    expect(wrap).not.toHaveClass('is-collapsed');
    expect(screen.getByRole('button', { name: 'Click to collapse' }))
      .toBeInTheDocument();
  });

  test('USER with images appends image count', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [
        { type: 'text', text: 'screenshot' },
        { type: 'image' },
        { type: 'image' },
      ] },
    })]} />);
    const prompt = container.querySelector('.chat-sticky-prompt-text');
    expect(prompt).toHaveTextContent('screenshot');
    expect(prompt).toHaveTextContent('2 images attached');
  });

  test('STREAM_EVENT renders nothing (suppressed)', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.STREAM_EVENT,
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('PERMISSION_REQUEST renders nothing in the log (modal handles it)', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.PERMISSION_REQUEST,
      request_id: 'r1',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('RESULT (success) renders NO bubble — the assistant message above already covers it', () => {
    // The success-case result event is the full tool output again
    // (file lists, summaries, etc.) — duplicates what the assistant
    // just said. Operator complaint: "remove the result block".
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.RESULT,
      is_error: false,
      result: 'done',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('RESULT (error) renders "(result: error)" error bubble', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.RESULT,
      is_error: true,
      result: 'rate limited',
    })]} />);
    expect(screen.getByText(/result: error/)).toBeInTheDocument();
    expect(screen.getByText(/rate limited/)).toBeInTheDocument();
  });

  test('event with no type renders nothing', () => {
    const { container } = render(<EventLog entries={[_server({})]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('hidden chat events (rate_limit_event) render nothing', () => {
    const { container } = render(<EventLog entries={[_server({
      type: 'rate_limit_event',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('unknown event type renders as a generic TOOL bubble with the label', () => {
    render(<EventLog entries={[_server({
      type: 'unknown_event',
      subtype: 'weird',
    })]} />);
    expect(screen.getByText('unknown_event / weird')).toBeInTheDocument();
  });
});


describe('EventLog — tool_use with long details (Bug fix regression guard)', () => {

  test('tool_use with >40 details lines renders without ReferenceError (Bug fix)', () => {
    // Regression: EventLog used TOOL_DETAILS_COLLAPSE_THRESHOLD
    // without importing it. Any tool with a long output crashed
    // with "ReferenceError: TOOL_DETAILS_COLLAPSE_THRESHOLD is not
    // defined". This test renders a Bash with multi-line output to
    // exercise the toggle-button branch.
    const longCommand = Array.from({ length: 60 }, (_, i) => `echo line ${i}`).join('\n');
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: longCommand } },
      ] },
    })]} />);
    // The collapse toggle button appears for long output.
    expect(screen.getByRole('button', { name: /expand|collapse|show.*more|hide|less|fewer/i }))
      .toBeInTheDocument();
  });

  test('tool_use with <40 details lines does NOT show the toggle button', () => {
    const shortCommand = 'ls -la';
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: shortCommand } },
      ] },
    })]} />);
    // No "Show N more" button for short output.
    expect(screen.queryByRole('button', { name: /expand|collapse|show.*more|hide|less|fewer/i }))
      .not.toBeInTheDocument();
  });

  test('clicking the toggle expands collapsed details', () => {
    const longCommand = Array.from({ length: 80 }, (_, i) => `echo "${i}"`).join('\n');
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: longCommand } },
      ] },
    })]} />);

    const toggle = screen.getByRole('button', { name: /expand|collapse|show.*more|hide|less|fewer/i });
    fireEvent.click(toggle);
    // After expanding, the label changes (collapse / show fewer).
    expect(toggle.textContent.toLowerCase()).toMatch(/collapse|hide|less|fewer/);
  });
});


describe('EventLog — dedupe + show-older', () => {

  test('dedupes a LOCAL user echo followed by a SERVER user envelope', () => {
    // ``MessageFilter.dedupeUserEchoes`` collapses the local
    // optimistic prompt + the server's echo into ONE rendered
    // prompt. Both have the same text — without dedupe there would
    // be two sticky prompts (two turns).
    const { container } = render(<EventLog entries={[
      _local(BUBBLE_KIND.USER, 'identical text'),
      _server({
        type: CLAUDE_EVENT.USER,
        message: { content: [{ type: 'text', text: 'identical text' }] },
      }),
    ]} />);

    const prompts = container.querySelectorAll('.chat-sticky-prompt-text');
    expect(prompts.length).toBe(1);
    expect(prompts[0]).toHaveTextContent('identical text');
  });

  test('"Show N earlier events" button appears when window truncates', () => {
    // EVENT_LOG_WINDOW_SIZE is 200; push 250 to force truncation.
    const many = Array.from({ length: 250 }, (_, i) => _server({
      type: CLAUDE_EVENT.ASSISTANT,
      uuid: `u${i}`,
      message: { content: [{ type: 'text', text: `msg ${i}` }] },
    }));
    render(<EventLog entries={many} />);
    const showOlder = screen.queryByRole('button', { name: /show.*earlier event/i });
    expect(showOlder).toBeInTheDocument();
  });
});


describe('EventLog — per-turn sticky grouping', () => {

  test('each operator prompt opens its own .chat-turn section', () => {
    const { container } = render(<EventLog entries={[
      _local(BUBBLE_KIND.USER, 'first ask'),
      _server({
        type: CLAUDE_EVENT.ASSISTANT,
        uuid: 'a1',
        message: { content: [{ type: 'text', text: 'reply one' }] },
      }),
      _local(BUBBLE_KIND.USER, 'second ask'),
      _server({
        type: CLAUDE_EVENT.ASSISTANT,
        uuid: 'a2',
        message: { content: [{ type: 'text', text: 'reply two' }] },
      }),
    ]} />);
    const turns = container.querySelectorAll(
      '.chat-turn:not(.chat-turn--preamble)',
    );
    expect(turns.length).toBe(2);
    // The prompt must be the FIRST child of its turn — that's what
    // bounds ``position: sticky`` to the turn so it pins while the
    // turn is on screen and is pushed off as the next turn scrolls in.
    expect(turns[0].firstElementChild)
      .toHaveClass('chat-sticky-prompt');
    expect(turns[0].firstElementChild)
      .toHaveClass('sticky-section-header');
    expect(turns[0]).toHaveTextContent('first ask');
    expect(turns[0]).toHaveTextContent('reply one');
    // A turn owns every bubble until the NEXT prompt, no further.
    expect(turns[0]).not.toHaveTextContent('second ask');
    expect(turns[1]).toHaveTextContent('second ask');
    expect(turns[1]).toHaveTextContent('reply two');
  });

  test('bubbles before the first prompt go in a preamble (no sticky header)', () => {
    const { container } = render(<EventLog entries={[
      _server({
        type: CLAUDE_EVENT.SYSTEM,
        subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
        [AGENT_SESSION_ID]: 'sess-1',
      }),
      _local(BUBBLE_KIND.USER, 'the ask'),
    ]} />);
    const preamble = container.querySelector('.chat-turn--preamble');
    expect(preamble).toBeInTheDocument();
    expect(preamble).toHaveTextContent('Claude session started · sess-1…');
    expect(preamble.querySelector('.chat-sticky-prompt')).toBeNull();
    // The operator prompt still gets its own sticky turn.
    expect(
      container.querySelector(
        '.chat-turn:not(.chat-turn--preamble) .chat-sticky-prompt-text',
      ),
    ).toHaveTextContent('the ask');
  });
});


describe('EventLog — footer (trailing working indicator)', () => {

  test('renders footer as the LAST child inside #event-log', () => {
    // The working indicator is passed as ``footer`` so it scrolls
    // with the messages and trails the newest one — it must be the
    // final child of the scroll container, after every turn.
    const { container } = render(
      <EventLog
        entries={[
          _local(BUBBLE_KIND.USER, 'do the thing'),
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            uuid: 'a1',
            message: { content: [{ type: 'text', text: 'on it' }] },
          }),
        ]}
        footer={<div data-testid="work-indicator">thinking…</div>}
      />,
    );
    const log = container.querySelector('#event-log');
    const indicator = container.querySelector('[data-testid="work-indicator"]');
    expect(log.contains(indicator)).toBe(true);
    // Last child of the scroll container, i.e. after the last turn.
    expect(log.lastElementChild).toBe(indicator);
  });

  test('omitting footer renders nothing extra (default null)', () => {
    const { container } = render(
      <EventLog entries={[_local(BUBBLE_KIND.USER, 'hi')]} />,
    );
    expect(
      container.querySelector('[data-testid="work-indicator"]'),
    ).toBeNull();
  });
});


describe('EventLog — task-switch scroll', () => {

  test('changing taskId scrolls the log to the bottom', () => {
    const entries = [
      _local(BUBBLE_KIND.USER, 'q'),
      _server({
        type: CLAUDE_EVENT.ASSISTANT,
        uuid: 'a1',
        message: { content: [{ type: 'text', text: 'a' }] },
      }),
    ];
    const { container, rerender } = render(
      <EventLog taskId="T1" entries={entries} />,
    );

    // Operator scrolls up on task T1 → pin intent goes false, so the
    // content effect alone would NOT re-pin on switch.
    const log = container.querySelector('#event-log');
    Object.defineProperty(log, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(log, 'clientHeight', { value: 200, configurable: true });
    log.scrollTop = 0;
    fireEvent.scroll(log);

    scrollToBottom.mockClear();
    rerender(<EventLog taskId="T2" entries={entries} />);

    // Switching tasks must always land at the newest message.
    expect(scrollToBottom).toHaveBeenCalled();
  });

  test('same taskId on re-render does NOT force a scroll', () => {
    const entries = [_local(BUBBLE_KIND.USER, 'q')];
    const { rerender } = render(<EventLog taskId="T1" entries={entries} />);
    scrollToBottom.mockClear();
    // Re-render with the SAME task + SAME entries: the task-switch
    // effect must not fire (its dep, taskId, is unchanged).
    rerender(<EventLog taskId="T1" entries={entries} />);
    expect(scrollToBottom).not.toHaveBeenCalled();
  });
});


describe('EventLog — stay pinned to bottom on late content', () => {

  test('async/late content (DOM growth) re-snaps to bottom while pinned', async () => {
    const { rerender } = render(
      <EventLog taskId="T1" entries={[_local(BUBBLE_KIND.USER, 'first')]} />,
    );
    scrollToBottom.mockClear();
    // Simulate the task's history streaming in AFTER mount — the
    // visible-event count grows, mutating the log's DOM. The
    // MutationObserver must yank back to the bottom (pinned is still
    // true — the operator never scrolled).
    rerender(
      <EventLog
        taskId="T1"
        entries={[
          _local(BUBBLE_KIND.USER, 'first'),
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            uuid: 'a1',
            message: { content: [{ type: 'text', text: 'late reply' }] },
          }),
        ]}
      />,
    );
    await waitFor(() => {
      expect(scrollToBottom).toHaveBeenCalled();
    });
  });

  test('does NOT re-snap once the operator has scrolled up', async () => {
    const { container, rerender } = render(
      <EventLog taskId="T1" entries={[_local(BUBBLE_KIND.USER, 'first')]} />,
    );
    // Operator scrolls up → pin intent goes false.
    const log = container.querySelector('#event-log');
    Object.defineProperty(log, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(log, 'clientHeight', { value: 200, configurable: true });
    log.scrollTop = 0;
    fireEvent.scroll(log);

    scrollToBottom.mockClear();
    rerender(
      <EventLog
        taskId="T1"
        entries={[
          _local(BUBBLE_KIND.USER, 'first'),
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            uuid: 'a1',
            message: { content: [{ type: 'text', text: 'more' }] },
          }),
        ]}
      />,
    );
    // Give the MutationObserver a chance to (not) fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(scrollToBottom).not.toHaveBeenCalled();
  });
});
