// Tests for SessionHeader. Mocks the api module + the two hooks
// (usePushApproval, useTaskPublish) so we test only the header's
// own logic: status-dot rendering, button enablement, action
// dispatch, modal opening.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  finishTask: vi.fn().mockResolvedValue({ ok: true, body: { finished: true } }),
  postSession: vi.fn().mockResolvedValue({ ok: true }),
  triggerScan: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateTaskSource: vi.fn().mockResolvedValue({
    ok: true,
    body: { updated_repositories: [], failed_repositories: [], warnings: [] },
  }),
}));

vi.mock('../hooks/usePushApproval.js', () => ({
  usePushApproval: vi.fn(),
}));
vi.mock('../hooks/useTaskPublish.js', () => ({
  useTaskPublish: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => {
  const show = vi.fn();
  return {
    toast: {
      show,
      errorFromResult: (result, { title, fallback = '', durationMs = 8000 } = {}) =>
        show({
          kind: 'error',
          title,
          message: String(
            (result && result.body && result.body.error)
            || (result && result.error) || fallback,
          ),
          durationMs,
        }),
    },
    // Mirror the real toastResult dispatch so the Pull / Finish /
    // Update-source toasts still land on the mocked show().
    toastResult: (
      { kind = 'info', title, message } = {},
      { errorMs = 12000, defaultMs = 7000 } = {},
    ) => show({
      kind, title, message, durationMs: kind === 'error' ? errorMs : defaultMs,
    }),
  };
});

import { postSession, triggerScan } from '../api.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { useTaskPublish } from '../hooks/useTaskPublish.js';
import { toast } from '../stores/toastStore.js';
import SessionHeader, { SessionHeaderPlaceholder } from './SessionHeader.jsx';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';


function _session(overrides = {}) {
  return {
    task_id: 'PROJ-1',
    task_summary: 'Fix the login bug',
    status: TAB_STATUS.ACTIVE,
    live: true,
    working: false,
    [AGENT_SESSION_ID]: 'sess-1',
    ...overrides,
  };
}

function _defaultPushApproval(overrides = {}) {
  return {
    awaiting: false,
    busy: false,
    approve: vi.fn().mockResolvedValue({ ok: true }),
    ...overrides,
  };
}

function _defaultTaskPublish(overrides = {}) {
  return {
    hasWorkspace: true,
    hasChangesToPush: false,
    hasPullRequest: false,
    pullRequestUrls: [],
    pushBusy: false,
    pullBusy: false,
    prBusy: false,
    push: vi.fn(),
    pull: vi.fn().mockResolvedValue({ ok: true }),
    createPullRequest: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  };
}


beforeEach(() => {
  usePushApproval.mockReturnValue(_defaultPushApproval());
  useTaskPublish.mockReturnValue(_defaultTaskPublish());
});


describe('SessionHeader — null guard', () => {

  test('returns null when no session is passed', () => {
    const { container } = render(<SessionHeader session={null} />);
    expect(container.firstChild).toBeNull();
  });
});


describe('SessionHeader — task summary + status dot', () => {

  test('renders the task summary', () => {
    render(<SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />);
    expect(screen.getByText('Fix the login bug')).toBeInTheDocument();
  });

  test('renders a status dot with the active class', () => {
    const { container } = render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(container.querySelector('.status-dot.status-active')).toBeInTheDocument();
  });

  test('needsAttention=true paints the dot with status-attention', () => {
    const { container } = render(
      <SessionHeader
        session={_session()}
        needsAttention={true}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(container.querySelector('.status-dot.status-attention')).toBeInTheDocument();
  });

  test('PROVISIONING status paints is-loading on the dot', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ status: TAB_STATUS.PROVISIONING })}
        streamLifecycle={SESSION_LIFECYCLE.CONNECTING}
      />,
    );
    expect(container.querySelector('.status-dot.is-loading')).toBeInTheDocument();
  });
});


describe('SessionHeader — always prints the Claude session id', () => {

  test('is NOT shown next to the task code (left side)', () => {
    // The session id chip used to sit beside the task id, crowding the
    // task code/title; it was removed there and now lives only by the
    // ``Claude: <status>`` chip on the right.
    const { container } = render(
      <SessionHeader
        session={_session({ [AGENT_SESSION_ID]: 'abcdef12-3456-7890-abcd-ef1234567890' })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(container.querySelector('#session-claude-id')).not.toBeInTheDocument();
    const info = container.querySelector('.session-header-info');
    expect(info.querySelector('.claude-session-id')).toBeNull();
  });

  test('rendered adjacent to the Claude: <status> chip on the right', () => {
    // The id badge lives next to the right-side ``Claude: <status>``
    // chip so operators can see which conversation is working.
    const { container } = render(
      <SessionHeader
        session={_session({ [AGENT_SESSION_ID]: 'abcdef12-3456-7890-abcd-ef1234567890' })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    const aside = container.querySelector(
      '.session-header-actions .claude-session-id.is-aside-status',
    );
    expect(aside).toBeInTheDocument();
    expect(aside).toHaveTextContent('sid:abcdef12…');
  });

  test('right-side badge omitted when there is no session id yet', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ [AGENT_SESSION_ID]: '' })}
        streamLifecycle={SESSION_LIFECYCLE.CONNECTING}
      />,
    );
    expect(
      container.querySelector('.is-aside-status'),
    ).not.toBeInTheDocument();
  });
});


describe('SessionHeader — Stop vs Resume button', () => {

  test('STREAMING lifecycle shows the Stop button', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^stop/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^resume/i }))
      .not.toBeInTheDocument();
  });

  test('CLOSED lifecycle shows the Resume button', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.CLOSED}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^stop/i }))
      .not.toBeInTheDocument();
  });

  test('IDLE lifecycle also shows Resume', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.IDLE}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeInTheDocument();
  });

  test('MISSING lifecycle shows Resume', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.MISSING}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeInTheDocument();
  });

  test('clicking Stop calls postSession(task_id, "stop")', async () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        onStopped={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^stop/i }));
    await waitFor(() => {
      expect(postSession).toHaveBeenCalledWith('PROJ-1', 'stop');
    });
  });

  test('Stop is enabled WHILE Claude is working (the bug fix)', async () => {
    // Regression: ``deriveTabStatus`` flips to ``WORKING`` when
    // ``session.working === true``. The previous Stop-disabled guard
    // (``baseStatus !== ACTIVE``) silently disabled the button mid-
    // turn — the exact moment operators want to bail. Stop must
    // remain clickable while the subprocess is alive, regardless of
    // turn state.
    render(
      <SessionHeader
        session={_session({ working: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        onStopped={vi.fn()}
      />,
    );
    const stopBtn = screen.getByRole('button', { name: /^stop/i });
    expect(stopBtn).not.toBeDisabled();
    fireEvent.click(stopBtn);
    await waitFor(() => {
      expect(postSession).toHaveBeenCalledWith('PROJ-1', 'stop');
    });
  });

  test('Stop is enabled when Claude is paused on a permission request', async () => {
    // ATTENTION state also blocked the previous Stop guard. Operator
    // must be able to terminate a session that's parked waiting for
    // a permission decision they don't want to grant.
    render(
      <SessionHeader
        session={_session({ has_pending_permission: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        needsAttention={true}
        onStopped={vi.fn()}
      />,
    );
    const stopBtn = screen.getByRole('button', { name: /^stop/i });
    expect(stopBtn).not.toBeDisabled();
  });

  test('clicking Resume calls the onResume callback', async () => {
    const onResume = vi.fn().mockResolvedValue();
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.CLOSED}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^resume/i }));
    await waitFor(() => { expect(onResume).toHaveBeenCalled(); });
  });

  test('Resume is disabled when onResume is not a function', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.CLOSED}
        onResume={null}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeDisabled();
  });
});


describe('SessionHeader — Approve push banner', () => {

  test('approve-push button is hidden when not awaiting', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.queryByRole('button', { name: /approve push/i }))
      .not.toBeInTheDocument();
  });

  test('approve-push button visible + clickable when awaiting=true', async () => {
    const approve = vi.fn().mockResolvedValue({ ok: true });
    usePushApproval.mockReturnValue(_defaultPushApproval({
      awaiting: true, approve,
    }));

    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    const btn = screen.getByRole('button', { name: /approve push/i });
    fireEvent.click(btn);
    await waitFor(() => { expect(approve).toHaveBeenCalled(); });
  });

  test('approve-push button disabled while busy', () => {
    usePushApproval.mockReturnValue(_defaultPushApproval({
      awaiting: true, busy: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /pushing…/i })).toBeDisabled();
  });
});


describe('SessionHeader — Push / Pull / PR buttons', () => {

  test('Push button disabled when hasChangesToPush=false', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasChangesToPush: false,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^push$/i })).toBeDisabled();
  });

  test('Push button enabled when hasChangesToPush=true', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasChangesToPush: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^push$/i })).not.toBeDisabled();
  });

  test('Pull button disabled when no workspace', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^pull$/i })).toBeDisabled();
  });

  test('Pull request button disabled when PR already exists', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true,
      hasPullRequest: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    // Exact match: the open-PR button's label also contains
    // "pull request", so anchor to the create-PR button only.
    expect(screen.getByRole('button', { name: /^pull request$/i }))
      .toBeDisabled();
  });

  test('Update source button disabled when no workspace', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^update source$/i })).toBeDisabled();
  });

  test('Update source button enabled when workspace exists', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^update source$/i })).not.toBeDisabled();
  });

  test('Push action calls taskPublish.push and refreshes', async () => {
    const push = vi.fn().mockResolvedValue({ ok: true });
    const refresh = vi.fn();
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasChangesToPush: true, push, refresh,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^push$/i }));
    await waitFor(() => { expect(push).toHaveBeenCalled(); });
  });
});


describe('SessionHeader — Open pull request button', () => {

  const openBtn = () =>
    screen.getByRole('button', { name: /open pull request in a new tab/i });

  test('disabled when there is no pull request yet', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      pullRequestUrls: [],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(openBtn()).toBeDisabled();
  });

  test('enabled once a PR url exists', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasPullRequest: true,
      pullRequestUrls: ['https://bitbucket.org/o/r/pull-requests/1'],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(openBtn()).not.toBeDisabled();
  });

  test('clicking opens the PR in a new tab with noopener,noreferrer', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasPullRequest: true,
      pullRequestUrls: ['https://bitbucket.org/o/r/pull-requests/1'],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(openBtn());
    expect(openSpy).toHaveBeenCalledWith(
      'https://bitbucket.org/o/r/pull-requests/1',
      '_blank',
      'noopener,noreferrer',
    );
    openSpy.mockRestore();
  });

  test('multi-repo: opens every PR url', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasPullRequest: true,
      pullRequestUrls: [
        'https://bitbucket.org/o/api/pull-requests/3',
        'https://bitbucket.org/o/web/pull-requests/4',
      ],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(openBtn());
    expect(openSpy).toHaveBeenCalledTimes(2);
    openSpy.mockRestore();
  });
});


describe('SessionHeaderPlaceholder — persistent no-task bar', () => {

  test('shows a "Select a task" title and the same header shell', () => {
    const { container } = render(<SessionHeaderPlaceholder />);
    expect(container.querySelector('#session-header.is-empty'))
      .toBeInTheDocument();
    expect(screen.getByText(/select a task/i)).toBeInTheDocument();
  });

  test('renders the action buttons but every one is disabled', () => {
    const { container } = render(<SessionHeaderPlaceholder />);
    const buttons = container.querySelectorAll('.session-action');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((b) => {
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute('tabindex', '-1');
    });
  });
});


describe('SessionHeader — manual Sync button', () => {
  // The autonomous scan loop now ticks every 3 min (was 30s) so
  // provider APIs don't get hammered. The Sync button lets the
  // operator pull review-comment / status updates immediately
  // without waiting for the next auto-tick.

  test('renders the Sync button alongside the other actions', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument();
  });

  test('clicking Sync calls triggerScan and shows a success toast', async () => {
    triggerScan.mockClear();
    triggerScan.mockResolvedValueOnce({ ok: true, body: {} });
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /sync now/i }));
    await waitFor(() => {
      expect(triggerScan).toHaveBeenCalled();
    });
    expect(toast.show).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'success' }),
    );
  });

  test('Sync failure surfaces an error toast', async () => {
    triggerScan.mockClear();
    triggerScan.mockResolvedValueOnce({ ok: false, error: 'auth' });
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /sync now/i }));
    await waitFor(() => {
      expect(triggerScan).toHaveBeenCalled();
    });
    expect(toast.show).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'error', message: 'auth' }),
    );
  });
});
