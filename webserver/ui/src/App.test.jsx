// Tests for App.jsx — the composition root. App itself is mostly
// wiring; we mock every hook + child component so we can probe its
// own logic (activeTaskId state, handleForgetTask, modal toggle)
// without dragging in the full transitive tree.
//
// Component-level integration of children is covered by each
// child's own test file; this file pins App's own glue.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('./api.js', () => ({
  forgetTaskWorkspace: vi.fn().mockResolvedValue({ ok: true }),
  triggerScan: vi.fn().mockResolvedValue({ ok: true }),
  // App now mounts <SettingsDrawer>, whose default-tab panel
  // (RepositoriesSettingsPanel) calls fetchSettings on mount even
  // while the drawer is closed. Stub every read the drawer panels
  // fire so the mock doesn't reject (unhandled-rejection noise that
  // Vitest flags as a possible false-positive source).
  fetchSettings: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateSettings: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  fetchAllSettings: vi.fn().mockResolvedValue(
    { ok: true, body: { sections: [] } },
  ),
  updateAllSettings: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  fetchTaskProviders: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateTaskProvider: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  fetchGitProviders: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateGitProvider: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  fetchRepositoryApprovals: vi.fn().mockResolvedValue(
    { ok: true, body: { repositories: [] } },
  ),
  updateRepositoryApprovals: vi.fn().mockResolvedValue({ ok: true, body: {} }),
}));

vi.mock('./hooks/useSessions.js', () => ({
  useSessions: vi.fn(() => ({ sessions: [], refresh: vi.fn() })),
}));
vi.mock('./hooks/useTaskAttention.js', () => ({
  useTaskAttention: vi.fn(() => ({
    taskIds: new Set(),
    mark: vi.fn(),
    clear: vi.fn(),
  })),
}));
vi.mock('./hooks/useToolMemory.js', () => ({
  useToolMemory: vi.fn(() => ({
    remember: vi.fn(),
    recall: vi.fn().mockReturnValue(null),
    forget: vi.fn(),
  })),
}));
vi.mock('./hooks/useSafetyState.js', () => ({
  useSafetyState: vi.fn(() => null),
}));
vi.mock('./hooks/useStatusFeed.js', () => ({
  useStatusFeed: vi.fn(() => ({
    latest: null, history: [], stale: false, connected: false,
  })),
}));
vi.mock('./hooks/useNotifications.js', () => ({
  useNotifications: vi.fn(() => ({
    supported: false,
    enabled: false,
    permission: 'default',
    toggle: vi.fn(),
    notify: vi.fn(),
    kindPrefs: {},
    setKindEnabled: vi.fn(),
  })),
}));
vi.mock('./hooks/useNotificationRouting.js', () => ({
  useNotificationRouting: vi.fn(() => ({
    onStatusEntry: vi.fn(),
    onSessionEvent: vi.fn(),
  })),
}));
vi.mock('./hooks/useResizable.js', () => ({
  useResizable: vi.fn(() => ({
    width: 380,
    onPointerDown: vi.fn(),
  })),
}));
vi.mock('./hooks/useSessionStream.js', () => ({
  clearTaskStreamCache: vi.fn(),
}));

// Stub child components so render is fast and predictable.
vi.mock('./components/SessionDetail.jsx', () => ({
  default: ({ session }) => (
    <div data-testid="session-detail">
      session={session ? session.task_id : 'none'}
    </div>
  ),
}));
vi.mock('./components/TabList.jsx', () => ({
  default: ({ sessions, activeTaskId, onSelect, onForget }) => (
    <div data-testid="tab-list">
      <span>active={activeTaskId || 'none'}</span>
      {sessions.map((s) => (
        <button key={s.task_id} onClick={() => onSelect(s.task_id)}>
          {s.task_id}
        </button>
      ))}
      {sessions.map((s) => (
        <button
          key={`forget-${s.task_id}`}
          onClick={() => onForget(s.task_id)}
        >
          forget-{s.task_id}
        </button>
      ))}
    </div>
  ),
}));
vi.mock('./components/AdoptTaskModal.jsx', () => ({
  default: ({ isOpen, onClose }) => (
    isOpen ? (
      <div data-testid="adopt-task-modal">
        <button onClick={onClose}>close-modal</button>
      </div>
    ) : null
  ),
}));
vi.mock('./components/Header.jsx', () => ({
  default: () => <header data-testid="app-header" />,
}));
vi.mock('./components/Layout.jsx', () => ({
  default: ({ top, left, center, right }) => (
    <div>
      <div data-testid="layout-top">{top}</div>
      <div data-testid="layout-left">{left}</div>
      <div data-testid="layout-center">{center}</div>
      <div data-testid="layout-right">{right}</div>
    </div>
  ),
}));
vi.mock('./components/RightPane.jsx', () => ({
  default: () => <div data-testid="right-pane" />,
}));
vi.mock('./components/SafetyBanner.jsx', () => ({
  default: () => null,
}));
vi.mock('./components/ToastContainer.jsx', () => ({
  default: () => null,
}));

import { useSessions } from './hooks/useSessions.js';
import { forgetTaskWorkspace } from './api.js';
import App from './App.jsx';


beforeEach(() => {
  forgetTaskWorkspace.mockClear();
  useSessions.mockReturnValue({
    sessions: [],
    refresh: vi.fn(),
  });
});


describe('App — render shell', () => {

  test('mounts without crashing', () => {
    render(<App />);
    expect(screen.getByTestId('app-header')).toBeInTheDocument();
    expect(screen.getByTestId('tab-list')).toBeInTheDocument();
    expect(screen.getByTestId('session-detail')).toBeInTheDocument();
  });

  test('no active task initially', () => {
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();
  });

  test('SessionDetail receives null session when no active task', () => {
    render(<App />);
    expect(screen.getByTestId('session-detail').textContent)
      .toContain('session=none');
  });
});


describe('App — tab selection', () => {

  test('clicking a tab updates activeTaskId state', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
  });

  test('selecting a task feeds its session record into SessionDetail', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T2' }));
    expect(screen.getByTestId('session-detail').textContent)
      .toContain('session=T2');
  });
});


describe('App — forget task (hard-confirm modal gate)', () => {

  // The tab "X" no longer deletes immediately — it opens
  // ForgetTaskModal and the operator must approve.
  const confirmBtn = () => document.getElementById('forget-task-confirm');
  const cancelBtn = () => document.getElementById('forget-task-cancel');

  test('clicking "forget" opens the modal but does NOT delete yet', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(confirmBtn()).toBeInTheDocument();
    expect(forgetTaskWorkspace).not.toHaveBeenCalled();
  });

  test('Cancel aborts — nothing is deleted and the modal closes', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(cancelBtn());

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(forgetTaskWorkspace).not.toHaveBeenCalled();
  });

  test('approving the modal calls forgetTaskWorkspace + refreshes', async () => {
    const refresh = vi.fn();
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh,
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(confirmBtn());

    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T1');
    });
    await waitFor(() => { expect(refresh).toHaveBeenCalled(); });
  });

  test('approving forget of the ACTIVE task clears activeTaskId', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(confirmBtn());
    await waitFor(() => {
      expect(screen.getByText('active=none')).toBeInTheDocument();
    });
  });

  test('approving forget of a NON-active task leaves activeTaskId intact', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'forget-T2' }));
    fireEvent.click(confirmBtn());
    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T2');
    });
    expect(screen.getByText('active=T1')).toBeInTheDocument();
  });
});


// --------------------------------------------------------------------------
// Chaos / random-order driver — mash buttons in unpredictable sequences
// and assert App's state-machine invariants hold for ALL of them.
//
// The fixed-sequence tests above pin specific behaviours, but a real
// user rarely follows a script. They open the modal, cancel, open it
// again, switch tabs, forget the active tab, open the modal again, ...
// A test that always clicks in the SAME order can pass forever even
// when the state machine has a "modal stays open if cancelled while
// the tab is being forgotten" bug.
//
// This driver picks a button at random each step (deterministic by
// seed so failures reproduce) and runs N iterations. After each step
// it asserts the invariants below — every property that should hold
// regardless of what the user just clicked.
// --------------------------------------------------------------------------

function makeRng(seed) {
  // xorshift32 — enough randomness, deterministic, no extra deps.
  let state = seed | 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return ((state >>> 0) / 0xffffffff);
  };
}

const IMPATIENT_HUMAN_INPUTS = [
  'fix it',
  'whats wrong with you please fix it',
  'do it',
  'this is broken AGAIN',
  'just make it work',
  'ugh another null pointer',
  'help me!!!',
];

// Every button the operator can mash, plus the invariants they
// must preserve. Buttons that aren't present in the current DOM
// are skipped (the driver re-queries before every click).
function chaosActions() {
  return [
    {
      name: 'select-T1',
      run: () => {
        const b = screen.queryByRole('button', { name: 'T1' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'select-T2',
      run: () => {
        const b = screen.queryByRole('button', { name: 'T2' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'open-forget-T1',
      run: () => {
        const b = screen.queryByRole('button', { name: 'forget-T1' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'open-forget-T2',
      run: () => {
        const b = screen.queryByRole('button', { name: 'forget-T2' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'cancel-modal',
      run: () => {
        const b = document.getElementById('forget-task-cancel');
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'confirm-modal',
      run: () => {
        const b = document.getElementById('forget-task-confirm');
        if (b) fireEvent.click(b);
      },
    },
  ];
}

function activeTaskFromDom() {
  // Read the active=X chip the TabList stub renders.
  const node = Array.from(document.querySelectorAll('span'))
    .find((n) => /^active=/.test(n.textContent || ''));
  return node ? node.textContent.replace(/^active=/, '') : 'none';
}

function modalOpen() {
  return screen.queryByRole('dialog') !== null;
}

describe('App — chaos / random button mashing', () => {

  // Seeds chosen so the suite covers a few different orderings; failure
  // on any one of them surfaces the seed directly so the human can rerun.
  const SEEDS = [11, 137, 4242, 0xdeadbeef];

  SEEDS.forEach((seed) => {
    test(`survives 60 random clicks with seed=${seed}`, async () => {
      const refresh = vi.fn();
      useSessions.mockReturnValue({
        sessions: [
          { task_id: 'T1', summary: IMPATIENT_HUMAN_INPUTS[seed % 7] },
          { task_id: 'T2', summary: 'do it' },
        ],
        refresh,
      });
      render(<App />);
      const actions = chaosActions();
      const rng = makeRng(seed);
      const log = [];

      for (let i = 0; i < 60; i += 1) {
        const action = actions[Math.floor(rng() * actions.length)];
        log.push(action.name);
        action.run();
        // Settle any pending micro-tasks (forgetTaskWorkspace is async).
        // eslint-disable-next-line no-await-in-loop
        await Promise.resolve();

        // Invariants that must hold after EVERY click:
        //   1. App didn't unmount — the header is still there.
        expect(screen.getByTestId('app-header')).toBeInTheDocument();
        //   2. Tab list is still rendered.
        expect(screen.getByTestId('tab-list')).toBeInTheDocument();
        //   3. activeTaskId is one of {none, T1, T2} — never a stale
        //      id that no longer exists in the session list.
        const active = activeTaskFromDom();
        expect(['none', 'T1', 'T2']).toContain(active);
        //   4. The modal is either open or closed; if open, BOTH
        //      buttons exist (you can always cancel or confirm).
        if (modalOpen()) {
          expect(document.getElementById('forget-task-confirm'))
            .not.toBeNull();
          expect(document.getElementById('forget-task-cancel'))
            .not.toBeNull();
        }
      }
      // forgetTaskWorkspace was called ONLY for ids that exist in the
      // session list — never for a phantom id. (The fixed-sequence
      // tests above pin the per-call behaviour; this asserts the
      // invariant holds across every shuffled sequence.)
      forgetTaskWorkspace.mock.calls.forEach(([taskId]) => {
        expect(['T1', 'T2']).toContain(taskId);
      });
      // Diagnostic for failures: surface the click trace.
      if (log.length !== 60) {
        // eslint-disable-next-line no-console
        console.warn('chaos seed=' + seed + ' trace:', log.join(','));
      }
    });
  });

  test('mashing buttons with NO sessions never crashes', async () => {
    // Empty session list — every action should be a no-op safely.
    useSessions.mockReturnValue({ sessions: [], refresh: vi.fn() });
    render(<App />);
    const actions = chaosActions();
    const rng = makeRng(99);
    for (let i = 0; i < 40; i += 1) {
      const action = actions[Math.floor(rng() * actions.length)];
      action.run();
      // eslint-disable-next-line no-await-in-loop
      await Promise.resolve();
      expect(screen.getByTestId('app-header')).toBeInTheDocument();
      expect(activeTaskFromDom()).toBe('none');
    }
    expect(forgetTaskWorkspace).not.toHaveBeenCalled();
  });
});
