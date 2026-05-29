// Tests for the pure helpers inside SessionDetail.jsx.
// Component-level rendering is hard to test cleanly without
// stubbing every child (EventLog, MessageForm, etc), so we focus
// on what's actually pure and load-bearing for operator UX:
//
//   - lifecycleBanner: maps lifecycle + visibility-of-bubbles into
//     the always-visible status line at the top of the log.
//   - hasVisibleBubbles: decides whether at least one event should
//     render in EventLog (used to suppress the "waiting" banner).

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Stub the heavy children so the layout test renders fast and
// deterministically. WorkingIndicator is kept REAL — it's the
// element under test. The EventLog stub renders its ``footer`` prop
// inside a ``#event-log`` so we can assert the indicator is the
// trailing entry INSIDE the scrollable log (not a floating overlay).
// MessageForm is a bare ``#message-form`` so the ordering check uses
// the same selector as the real composer.
// Expose onResume via a button so the resume path (deliverMessage,
// bypassing the queue) can be exercised. Layout tests ignore it.
vi.mock('./SessionHeader.jsx', () => ({
  default: ({ onResume }) => (
    <div data-testid="session-header">
      <button type="button" onClick={onResume}>mock-resume</button>
    </div>
  ),
  SessionHeaderPlaceholder: () => (
    <div data-testid="session-header-placeholder">Select a task</div>
  ),
}));
vi.mock('./EventLog.jsx', () => ({
  default: ({ footer }) => <div id="event-log" data-testid="event-log">{footer}</div>,
}));
// The mock exposes a button that invokes the real ``onSubmit`` prop
// (SessionDetail.onSendMessage) so the queue tests can drive a send
// without the real composer. Layout tests only look for #message-form
// / its absence, so the extra button is harmless.
vi.mock('./MessageForm.jsx', () => ({
  default: ({ onSubmit }) => (
    <form id="message-form">
      <button type="button" onClick={() => onSubmit('hello', [])}>
        mock-send
      </button>
    </form>
  ),
}));
vi.mock('./PermissionDecisionContainer.jsx', () => ({ default: () => null }));
vi.mock('./ChatSearch.jsx', () => ({ default: () => null }));
vi.mock('../hooks/useToolMemory.js', () => ({
  useToolMemory: () => ({ recall: vi.fn(), remember: vi.fn() }),
}));
vi.mock('../api.js', () => ({
  fetchModels: vi.fn().mockResolvedValue({ models: [] }),
  fetchSessionModel: vi.fn().mockResolvedValue({ model: '' }),
  postChatMessage: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  postSession: vi.fn().mockResolvedValue({ ok: true }),
  setSessionModel: vi.fn().mockResolvedValue({}),
  fetchEffortLevels: vi.fn().mockResolvedValue({ levels: [], default: '' }),
  fetchSessionEffort: vi.fn().mockResolvedValue({ effort: '' }),
  setSessionEffort: vi.fn().mockResolvedValue({}),
}));
vi.mock('../hooks/useSessionStream.js', async (importActual) => {
  const actual = await importActual();
  return { ...actual, useSessionStream: vi.fn() };
});

import SessionDetail, {
  hasVisibleBubbles,
  lifecycleBanner,
} from './SessionDetail.jsx';
import { SESSION_LIFECYCLE, useSessionStream } from '../hooks/useSessionStream.js';
import { postChatMessage } from '../api.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';


describe('lifecycleBanner', () => {

  test('CONNECTING → "Connecting to session for {taskId}…"', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.CONNECTING, 'T1', false);
    expect(banner).toMatch(/connecting/i);
    expect(banner).toContain('T1');
  });

  test('STREAMING with no visible bubbles → "Connected — waiting…"', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.STREAMING, 'T1', false);
    expect(banner).toMatch(/waiting/i);
  });

  test('STREAMING WITH visible bubbles → null (banner suppressed)', () => {
    // Once chat content has arrived, the banner suppresses so the
    // operator reads the chat cleanly. This is the most important
    // banner behavior in normal use.
    const banner = lifecycleBanner(SESSION_LIFECYCLE.STREAMING, 'T1', true);
    expect(banner).toBeNull();
  });

  test('IDLE → explains kato will respawn when work arrives', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.IDLE, 'T1', false);
    expect(banner.toLowerCase()).toMatch(/kato.*re-spawns|kato.*resume/);
  });

  test('MISSING → tells operator there is no record', () => {
    // Specifically NOT the same as IDLE — operator must be able to
    // tell "no live subprocess but record exists" vs "no record at all".
    const banner = lifecycleBanner(SESSION_LIFECYCLE.MISSING, 'T1', false);
    expect(banner.toLowerCase()).toMatch(/no record/);
  });

  test('CLOSED → "(session ended)"', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.CLOSED, 'T1', false);
    expect(banner).toMatch(/ended/i);
  });

  test('unknown lifecycle → null (no rogue banner)', () => {
    expect(lifecycleBanner('weird-state', 'T1', false)).toBeNull();
    expect(lifecycleBanner(undefined, 'T1', false)).toBeNull();
  });
});


describe('hasVisibleBubbles', () => {

  test('empty entries → false', () => {
    expect(hasVisibleBubbles([])).toBe(false);
  });

  test('LOCAL-source entries always count as visible', () => {
    // User-typed bubbles + system audit bubbles.
    expect(hasVisibleBubbles([
      { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.USER, text: 'hi' },
    ])).toBe(true);
  });

  test('HISTORY-source entries always count as visible', () => {
    // Restart replay — drives the banner away on tab open.
    expect(hasVisibleBubbles([
      { source: ENTRY_SOURCE.HISTORY, raw: { type: CLAUDE_EVENT.USER } },
    ])).toBe(true);
  });

  test('SERVER ASSISTANT with text block counts as visible', () => {
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.ASSISTANT,
          message: { content: [{ type: 'text', text: 'reply' }] },
        },
      },
    ])).toBe(true);
  });

  test('SERVER ASSISTANT with tool_use block counts as visible', () => {
    // tool_use bubbles render distinctly in EventLog so they
    // count for banner-suppression purposes.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.ASSISTANT,
          message: {
            content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: {} }],
          },
        },
      },
    ])).toBe(true);
  });

  test('SERVER ASSISTANT with empty content does NOT count', () => {
    // Edge case: a malformed assistant event with no content blocks.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.ASSISTANT, message: { content: [] } },
      },
    ])).toBe(false);
  });

  test('SERVER USER events do NOT count (those are echo, banner stays)', () => {
    // The server echoes user messages back; until Claude replies,
    // banner stays "waiting…". Pinning this prevents a regression
    // where the banner vanishes the moment the operator sends.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.USER },
      },
    ])).toBe(false);
  });

  test('SERVER STREAM_EVENT does NOT count (mid-stream chunks)', () => {
    // Stream events are partial deltas; the corresponding ASSISTANT
    // event is what counts as a visible bubble.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.STREAM_EVENT },
      },
    ])).toBe(false);
  });

  test('SERVER permission_request / control_request / response do NOT count', () => {
    // Permission flow events render in the modal, not the chat
    // log proper. Banner stays "waiting…" until real content.
    for (const type of [
      CLAUDE_EVENT.PERMISSION_REQUEST,
      CLAUDE_EVENT.CONTROL_REQUEST,
      CLAUDE_EVENT.PERMISSION_RESPONSE,
    ]) {
      expect(hasVisibleBubbles([
        { source: ENTRY_SOURCE.SERVER, raw: { type } },
      ])).toBe(false);
    }
  });

  test('SERVER system non-init events do NOT count', () => {
    // The boot-time INIT system event paints a "session connected"
    // bubble that the operator should see; other system subtypes
    // are noise.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.SYSTEM, subtype: 'compact_summary' },
      },
    ])).toBe(false);
  });

  test('SERVER system INIT counts as visible', () => {
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.SYSTEM,
          subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
        },
      },
    ])).toBe(true);
  });

  test('mixed list — any visible entry flips the result', () => {
    expect(hasVisibleBubbles([
      { source: ENTRY_SOURCE.SERVER, raw: { type: CLAUDE_EVENT.USER } },
      { source: ENTRY_SOURCE.SERVER, raw: { type: CLAUDE_EVENT.STREAM_EVENT } },
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.ASSISTANT,
          message: { content: [{ type: 'text', text: 'reply' }] },
        },
      },
    ])).toBe(true);
  });
});


describe('SessionDetail — working indicator placement', () => {

  function _stream(overrides = {}) {
    return {
      events: [],
      lifecycle: SESSION_LIFECYCLE.STREAMING,
      turnInFlight: true,
      pendingPermission: null,
      lastEventAt: 0,
      appendLocalEvent: vi.fn(),
      markTurnBusy: vi.fn(),
      reconnect: vi.fn(),
      dismissPermission: vi.fn(),
      ...overrides,
    };
  }

  test('indicator is the trailing entry INSIDE the scrollable log', async () => {
    // The reported bug: the indicator floated as an overlay and the
    // chat scrolled through it, colliding with the transcript. It is
    // now passed as EventLog's ``footer`` so it renders as the last
    // entry inside #event-log — part of the messages, scrollable —
    // never a floating overlay (no .composer-dock anymore).
    useSessionStream.mockReturnValue(_stream({ turnInFlight: true }));

    const { container } = render(
      <SessionDetail session={{ task_id: 'T1' }} />,
    );

    const log = await waitFor(() => {
      const el = container.querySelector('#event-log');
      expect(el).toBeInTheDocument();
      return el;
    });
    const indicator = container.querySelector('.working-indicator');
    expect(indicator).toBeInTheDocument();
    // Lives inside the scroll container, NOT in a floating dock.
    expect(log.contains(indicator)).toBe(true);
    expect(container.querySelector('.composer-dock')).toBeNull();
    // Composer is a separate sibling AFTER the log, not its child.
    const composer = container.querySelector('#message-form');
    expect(composer).toBeInTheDocument();
    expect(log.contains(composer)).toBe(false);
  });

  test('no indicator in the log when no turn is in flight', async () => {
    // WorkingIndicator returns null when inactive — the log then has
    // no trailing indicator entry. Exercises the footer=null path.
    useSessionStream.mockReturnValue(_stream({ turnInFlight: false }));

    const { container } = render(
      <SessionDetail session={{ task_id: 'T2' }} />,
    );

    await waitFor(() => {
      expect(container.querySelector('#event-log')).toBeInTheDocument();
    });
    expect(container.querySelector('.working-indicator')).toBeNull();
    expect(container.querySelector('#message-form')).toBeInTheDocument();
  });

  test('stalled indicator "continue" nudge sends a continue message', async () => {
    // Covers the footer indicator's onContinue closure: it goes
    // stalled after >180s of silence and shows a "send continue"
    // button; clicking it must drive SessionDetail.onSendMessage
    // ('continue') — optimistic local echo, turn marked busy, POST.
    const stream = _stream({
      turnInFlight: true,
      lastEventAt: Date.now() - 200_000,
    });
    useSessionStream.mockReturnValue(stream);

    render(<SessionDetail session={{ task_id: 'T1' }} />);

    const nudge = await screen.findByRole('button', { name: /continue/i });
    fireEvent.click(nudge);

    expect(stream.markTurnBusy).toHaveBeenCalledWith(true);
    await waitFor(() => {
      expect(postChatMessage).toHaveBeenCalledWith('T1', 'continue', []);
    });
  });

  test('renders the placeholder (no log) when no session is bound', () => {
    useSessionStream.mockReturnValue(_stream());
    const { container } = render(<SessionDetail session={null} />);
    expect(container.querySelector('#session-placeholder')).toBeInTheDocument();
    expect(container.querySelector('#event-log')).toBeNull();
    expect(container.querySelector('.working-indicator')).toBeNull();
  });
});


describe('SessionDetail — outgoing message queue', () => {

  function _stream(overrides = {}) {
    return {
      events: [],
      lifecycle: SESSION_LIFECYCLE.STREAMING,
      turnInFlight: false,
      pendingPermission: null,
      lastEventAt: 0,
      appendLocalEvent: vi.fn(),
      markTurnBusy: vi.fn(),
      reconnect: vi.fn(),
      dismissPermission: vi.fn(),
      ...overrides,
    };
  }

  test('idle: a sent message is delivered immediately', async () => {
    postChatMessage.mockClear();
    useSessionStream.mockReturnValue(_stream({ turnInFlight: false }));
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));

    await waitFor(() => {
      expect(postChatMessage).toHaveBeenCalledWith('T1', 'hello', []);
    });
  });

  test('mid-turn: a sent message is QUEUED, not delivered', async () => {
    postChatMessage.mockClear();
    const stream = _stream({ turnInFlight: true });
    useSessionStream.mockReturnValue(stream);
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));

    // Held — no POST while Claude is working.
    expect(postChatMessage).not.toHaveBeenCalled();
    // The queued item shows up in the persistent floating list
    // above the composer (replaces the earlier transient "⏳
    // queued" system bubble). Operator can see what's stacked up
    // and click Steer / Remove on each.
    expect(screen.getByText('hello')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: /queued messages/i }))
      .toBeInTheDocument();
  });

  test('clicking Remove drops a queued message without sending it', async () => {
    postChatMessage.mockClear();
    const stream = _stream({ turnInFlight: true });
    useSessionStream.mockReturnValue(stream);
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));
    expect(screen.getByText('hello')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /remove queued/i }));
    // Row gone; no POST happened.
    expect(screen.queryByText('hello')).not.toBeInTheDocument();
    expect(postChatMessage).not.toHaveBeenCalled();
  });

  test('clicking Steer delivers the queued message IMMEDIATELY mid-turn', async () => {
    // The "steer" affordance: even though Claude is in-flight, the
    // operator can promote a queued item to fire right now (instead
    // of waiting for the current turn to end). Useful for
    // course-correction without manual stop+restart.
    postChatMessage.mockClear();
    const stream = _stream({ turnInFlight: true });
    useSessionStream.mockReturnValue(stream);
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));
    expect(postChatMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /steer/i }));

    await waitFor(() => {
      expect(postChatMessage).toHaveBeenCalledWith('T1', 'hello', []);
    });
    // Steered item is removed from the queue (one-shot).
    expect(screen.queryByText('hello')).not.toBeInTheDocument();
  });

  test('the queued message flushes when the turn finishes', async () => {
    postChatMessage.mockClear();
    const busy = _stream({ turnInFlight: true });
    const idle = _stream({ turnInFlight: false });

    useSessionStream.mockReturnValue(busy);
    const { rerender } = render(<SessionDetail session={{ task_id: 'T1' }} />);
    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));
    expect(postChatMessage).not.toHaveBeenCalled();

    // Turn ends → flush effect delivers the held message exactly once.
    useSessionStream.mockReturnValue(idle);
    rerender(<SessionDetail session={{ task_id: 'T1' }} />);

    await waitFor(() => {
      expect(postChatMessage).toHaveBeenCalledWith('T1', 'hello', []);
    });
    expect(postChatMessage).toHaveBeenCalledTimes(1);
    expect(idle.markTurnBusy).toHaveBeenCalledWith(true);
  });

  test('switching tasks drops the pending queue (no cross-task send)', async () => {
    postChatMessage.mockClear();
    const busy = _stream({ turnInFlight: true });
    useSessionStream.mockReturnValue(busy);
    const { rerender } = render(<SessionDetail session={{ task_id: 'T1' }} />);
    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));

    // Switch to a different task, then that task's turn goes idle.
    const idleOther = _stream({ turnInFlight: false });
    useSessionStream.mockReturnValue(idleOther);
    rerender(<SessionDetail session={{ task_id: 'T2' }} />);

    // T1's queued message must NOT be delivered into T2.
    await Promise.resolve();
    expect(postChatMessage).not.toHaveBeenCalled();
  });

  test('spawned response: posts a "resumed" note and reconnects', async () => {
    postChatMessage.mockClear();
    postChatMessage.mockResolvedValueOnce({ ok: true, body: { status: 'spawned' } });
    const stream = _stream({ turnInFlight: false });
    useSessionStream.mockReturnValue(stream);
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));

    await waitFor(() => {
      expect(stream.reconnect).toHaveBeenCalled();
    });
    expect(stream.appendLocalEvent).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('resumed') }),
    );
  });

  test('failed send: error bubble + turn un-marked busy', async () => {
    postChatMessage.mockClear();
    postChatMessage.mockResolvedValueOnce({ ok: false, error: 'boom' });
    const stream = _stream({ turnInFlight: false });
    useSessionStream.mockReturnValue(stream);
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-send' }));

    await waitFor(() => {
      expect(stream.appendLocalEvent).toHaveBeenCalledWith(
        expect.objectContaining({ text: expect.stringContaining('send failed: boom') }),
      );
    });
    // Failure releases the busy flag so the operator can retry.
    expect(stream.markTurnBusy).toHaveBeenCalledWith(false);
  });

  test('resume delivers directly (bypasses the queue, even mid-turn)', async () => {
    postChatMessage.mockClear();
    // Mid-turn: a normal composer send would QUEUE — resume must not.
    const stream = _stream({ turnInFlight: true });
    useSessionStream.mockReturnValue(stream);
    render(<SessionDetail session={{ task_id: 'T1' }} />);

    fireEvent.click(screen.getByRole('button', { name: 'mock-resume' }));

    await waitFor(() => {
      expect(postChatMessage).toHaveBeenCalledWith(
        'T1', 'Please continue from where you left off.', [],
      );
    });
  });
});


describe('SessionDetail — permission dialog auto-reconnect', () => {

  function _stream(overrides = {}) {
    return {
      events: [],
      lifecycle: SESSION_LIFECYCLE.IDLE,
      turnInFlight: false,
      pendingPermission: null,
      lastEventAt: 0,
      appendLocalEvent: vi.fn(),
      markTurnBusy: vi.fn(),
      reconnect: vi.fn(),
      dismissPermission: vi.fn(),
      ...overrides,
    };
  }

  function _render(stream, props = {}) {
    useSessionStream.mockReturnValue(stream);
    return render(
      <SessionDetail
        session={{ task_id: 'T1' }}
        needsAttention={false}
        onPendingPermissionChange={vi.fn()}
        {...props}
      />,
    );
  }

  test('reconnects when attention rises while the SSE is closed (idle)', () => {
    // The bug: a permission request lands while the operator is
    // already on this tab; the per-task SSE was closed on idle so
    // pendingPermission never updates and the dialog never shows
    // until a manual tab re-click. Attention rising must auto-reopen.
    const stream = _stream({ lifecycle: SESSION_LIFECYCLE.IDLE });
    const { rerender } = _render(stream, { needsAttention: false });
    expect(stream.reconnect).not.toHaveBeenCalled();

    rerender(
      <SessionDetail
        session={{ task_id: 'T1' }}
        needsAttention
        onPendingPermissionChange={vi.fn()}
      />,
    );
    expect(stream.reconnect).toHaveBeenCalledTimes(1);
  });

  test('waits for idle when attention arrives before the stream sleeps', () => {
    const stream = _stream({ lifecycle: SESSION_LIFECYCLE.STREAMING });
    const { rerender } = _render(stream, { needsAttention: false });
    rerender(
      <SessionDetail session={{ task_id: 'T1' }} needsAttention />,
    );
    expect(stream.reconnect).not.toHaveBeenCalled();

    const idleStream = { ...stream, lifecycle: SESSION_LIFECYCLE.IDLE };
    useSessionStream.mockReturnValue(idleStream);
    rerender(
      <SessionDetail session={{ task_id: 'T1' }} needsAttention />,
    );
    expect(stream.reconnect).toHaveBeenCalledTimes(1);
  });

  test('does NOT reconnect when the stream stays live (STREAMING)', () => {
    // A live SSE already delivers the request; reconnecting would be
    // a needless reset.
    const stream = _stream({ lifecycle: SESSION_LIFECYCLE.STREAMING });
    const { rerender } = _render(stream, { needsAttention: false });
    rerender(
      <SessionDetail session={{ task_id: 'T1' }} needsAttention />,
    );
    rerender(
      <SessionDetail session={{ task_id: 'T1' }} needsAttention />,
    );
    expect(stream.reconnect).not.toHaveBeenCalled();
  });

  test('does NOT reconnect when a permission is already pending', () => {
    const stream = _stream({
      lifecycle: SESSION_LIFECYCLE.IDLE,
      pendingPermission: { type: 'permission_request', request_id: 'r1' },
    });
    const { rerender } = _render(stream, { needsAttention: false });
    rerender(
      <SessionDetail session={{ task_id: 'T1' }} needsAttention />,
    );
    expect(stream.reconnect).not.toHaveBeenCalled();
  });

  test('reconnects once per rising edge, not on every re-render', () => {
    const stream = _stream({ lifecycle: SESSION_LIFECYCLE.IDLE });
    const { rerender } = _render(stream, { needsAttention: false });
    const withAttention = (
      <SessionDetail session={{ task_id: 'T1' }} needsAttention />
    );
    rerender(withAttention);          // false → true: rising edge
    rerender(withAttention);          // still true: no new edge
    rerender(withAttention);
    expect(stream.reconnect).toHaveBeenCalledTimes(1);
  });
});


describe('SessionDetail — task header is hoisted to the global slot', () => {

  function _stream(overrides = {}) {
    return {
      events: [], lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: false,
      pendingPermission: null, lastEventAt: 0,
      appendLocalEvent: vi.fn(), markTurnBusy: vi.fn(),
      reconnect: vi.fn(), dismissPermission: vi.fn(), ...overrides,
    };
  }

  test('portals SessionHeader into #task-header-slot when present', async () => {
    useSessionStream.mockReturnValue(_stream());
    const slot = document.createElement('div');
    slot.id = 'task-header-slot';
    document.body.appendChild(slot);
    try {
      const { container } = render(
        <SessionDetail session={{ task_id: 'T1' }} />,
      );
      const header = await screen.findByTestId('session-header');
      // Rendered into the global slot, NOT inside the chat pane.
      expect(slot.contains(header)).toBe(true);
      expect(container.querySelector('#session-detail').contains(header))
        .toBe(false);
    } finally {
      document.body.removeChild(slot);
    }
  });

  test('falls back to inline header when the slot is absent', async () => {
    useSessionStream.mockReturnValue(_stream());
    const { container } = render(
      <SessionDetail session={{ task_id: 'T1' }} />,
    );
    const header = await screen.findByTestId('session-header');
    // No slot → header stays inside the chat pane (legacy position).
    expect(container.querySelector('#session-detail').contains(header))
      .toBe(true);
  });

  test('no task → placeholder header is portaled into the slot, not hidden', async () => {
    useSessionStream.mockReturnValue(_stream());
    const slot = document.createElement('div');
    slot.id = 'task-header-slot';
    document.body.appendChild(slot);
    try {
      render(<SessionDetail session={null} />);
      // The bar must NOT disappear: a "Select a task" placeholder
      // header lives in the global slot even with no task bound.
      const ph = await screen.findByTestId('session-header-placeholder');
      expect(slot.contains(ph)).toBe(true);
      // The real session header is NOT rendered with no session.
      expect(screen.queryByTestId('session-header')).not.toBeInTheDocument();
    } finally {
      document.body.removeChild(slot);
    }
  });

  test('no task + no slot → placeholder still renders (inline fallback)', () => {
    useSessionStream.mockReturnValue(_stream());
    render(<SessionDetail session={null} />);
    expect(screen.getByTestId('session-header-placeholder'))
      .toBeInTheDocument();
  });
});
