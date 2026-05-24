// Component-level tests for AdoptSessionModal — the "Adopt a Claude
// Code session" picker. Mounts → lists existing Claude sessions from
// ~/.claude/, search box re-queries the api, operator picks a row,
// confirm calls /adopt-agent-session.
//
// Interesting wiring:
//   - Lists each session: cwd + relative time + turn count + preview.
//   - Search input refetches with the query string.
//   - Adopt button stays disabled until a row is selected.
//   - Confirm calls adoptAgentSession(taskId, sessionId), then onAdopted + onClose.
//   - Error path: ok:false → toast error, modal stays open.
//   - fetchClaudeSessions throws → error rendered in the empty area.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  adoptAgentSession: vi.fn(),
  fetchClaudeSessions: vi.fn(),
}));

vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import AdoptSessionModal from './AdoptSessionModal.jsx';
import { adoptAgentSession, fetchClaudeSessions } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { toast } from '../stores/toastStore.js';


function _session(id, extra = {}) {
  return {
    [AGENT_SESSION_ID]: id,
    cwd: extra.cwd || `/home/dev/${id}`,
    last_modified_epoch: extra.last_modified_epoch
      ?? (Date.now() / 1000 - 600),  // 10 minutes ago
    turn_count: extra.turn_count ?? 5,
    last_user_message: extra.last_user_message || `last message in ${id}`,
    first_user_message: extra.first_user_message || `first message in ${id}`,
    adopted_by_task_id: extra.adopted_by_task_id || '',
  };
}


function renderModal({
  taskId = 'TASK-1',
  onClose = vi.fn(),
  onAdopted = vi.fn(),
} = {}) {
  return {
    onClose,
    onAdopted,
    ...render(
      <AdoptSessionModal
        taskId={taskId}
        onClose={onClose}
        onAdopted={onAdopted}
      />,
    ),
  };
}


beforeEach(() => {
  fetchClaudeSessions.mockReset();
  adoptAgentSession.mockReset();
  toast.show.mockReset();
});


describe('AdoptSessionModal — render + load', () => {

  test('renders title with task id and the help copy', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [] });

    renderModal({ taskId: 'KAT-9' });

    expect(screen.getByRole('heading', { name: /Adopt Claude session for KAT-9/i }))
      .toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search by path or message text/i))
      .toBeInTheDocument();
  });

  test('shows loading state then renders the session list', async () => {
    fetchClaudeSessions.mockResolvedValue({
      sessions: [_session('abc-1'), _session('xyz-9')],
    });

    renderModal();

    expect(screen.getByText(/Loading sessions/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/last message in abc-1/i)).toBeInTheDocument();
      expect(screen.getByText(/last message in xyz-9/i)).toBeInTheDocument();
    });
  });

  test('fetch rejected: renders the error text', async () => {
    fetchClaudeSessions.mockRejectedValue(new Error('disk read failed'));

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/disk read failed/i)).toBeInTheDocument();
    });
  });

  test('empty list: shows "no sessions found" message', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [] });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/No Claude Code sessions found/i)).toBeInTheDocument();
    });
  });

  test('confirm button is initially disabled (nothing picked)', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [_session('abc-1')] });

    renderModal();

    await waitFor(() => expect(screen.getByText(/last message in abc-1/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Adopt selected/i })).toBeDisabled();
  });
});


describe('AdoptSessionModal — search + select', () => {

  test('typing in search triggers a refetch with the query string', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [] });

    renderModal();
    await waitFor(() => expect(fetchClaudeSessions).toHaveBeenCalledWith(''));

    fireEvent.change(screen.getByPlaceholderText(/Search by path/i), {
      target: { value: 'kato' },
    });

    await waitFor(() => expect(fetchClaudeSessions).toHaveBeenCalledWith('kato'));
  });

  test('clicking a session enables the adopt button', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [_session('abc-1')] });

    renderModal();
    await waitFor(() => expect(screen.getByText(/last message in abc-1/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/last message in abc-1/));

    expect(screen.getByRole('button', { name: /Adopt selected/i })).not.toBeDisabled();
  });
});


describe('AdoptSessionModal — submit', () => {

  test('success: calls adoptAgentSession(taskId, sessionId) then onAdopted + onClose', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [_session('claude-sess-1')] });
    adoptAgentSession.mockResolvedValue({ ok: true, body: {} });

    const { onAdopted, onClose } = renderModal({ taskId: 'TASK-7' });

    await waitFor(() => expect(screen.getByText(/last message in claude-sess-1/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/last message in claude-sess-1/));
    fireEvent.click(screen.getByRole('button', { name: /Adopt selected/i }));

    await waitFor(() => {
      expect(adoptAgentSession).toHaveBeenCalledWith('TASK-7', 'claude-sess-1');
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onAdopted).toHaveBeenCalled();
  });

  test('failure: surfaces error toast, modal stays open', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [_session('claude-sess-1')] });
    adoptAgentSession.mockResolvedValue({
      ok: false,
      body: { error: 'session is locked' },
    });

    const { onClose } = renderModal();

    await waitFor(() => expect(screen.getByText(/last message in claude-sess-1/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/last message in claude-sess-1/));
    fireEvent.click(screen.getByRole('button', { name: /Adopt selected/i }));

    await waitFor(() => expect(adoptAgentSession).toHaveBeenCalled());

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error',
      message: 'session is locked',
    }));
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe('AdoptSessionModal — close affordances', () => {

  test('Cancel button calls onClose', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [] });
    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('× close button calls onClose', async () => {
    fetchClaudeSessions.mockResolvedValue({ sessions: [] });
    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
