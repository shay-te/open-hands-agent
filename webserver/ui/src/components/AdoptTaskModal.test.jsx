// Component-level tests for AdoptTaskModal — the left-panel
// "+ Add task" picker. Lists every task assigned to kato (any state),
// the operator types to filter, picks one, confirms → /api/tasks/<id>/adopt
// provisions the workspace.
//
// Wiring under test:
//   - Heading + help copy renders.
//   - Loading state → renders task rows on success.
//   - fetchAllAssignedTasks ok:false → error rendered in list.
//   - Search input filters by id / summary / state.
//   - alreadyAdoptedIds badges the rows but does not remove them.
//   - Adopt button disabled until row picked.
//   - Submit: adoptTask(id) → onAdopted + onClose on success;
//     failure → toast error, modal stays open.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  adoptTask: vi.fn(),
  fetchAllAssignedTasks: vi.fn(),
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
  };
});

import AdoptTaskModal from './AdoptTaskModal.jsx';
import { adoptTask, fetchAllAssignedTasks } from '../api.js';
import { toast } from '../stores/toastStore.js';


function _task(id, extra = {}) {
  return {
    id,
    summary: extra.summary || `Summary of ${id}`,
    state: extra.state || 'Open',
  };
}


function renderModal({
  alreadyAdoptedIds = new Set(),
  onClose = vi.fn(),
  onAdopted = vi.fn(),
} = {}) {
  return {
    onClose,
    onAdopted,
    ...render(
      <AdoptTaskModal
        alreadyAdoptedIds={alreadyAdoptedIds}
        onClose={onClose}
        onAdopted={onAdopted}
      />,
    ),
  };
}


beforeEach(() => {
  fetchAllAssignedTasks.mockReset();
  adoptTask.mockReset();
  toast.show.mockReset();
});


describe('AdoptTaskModal — render + load', () => {

  test('renders heading + search placeholder', async () => {
    fetchAllAssignedTasks.mockResolvedValue({ ok: true, body: { tasks: [] } });

    renderModal();

    expect(screen.getByRole('heading', { name: /Adopt a task/i })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search by id, summary, or state/i))
      .toBeInTheDocument();
  });

  test('loading then renders task rows', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-1'), _task('KAT-2')] },
    });

    renderModal();

    expect(screen.getByText(/Loading tasks/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('KAT-1')).toBeInTheDocument();
      expect(screen.getByText('KAT-2')).toBeInTheDocument();
    });
  });

  test('api error renders the message in-list', async () => {
    fetchAllAssignedTasks.mockResolvedValue({ ok: false, error: 'youtrack offline' });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/youtrack offline/i)).toBeInTheDocument();
    });
  });

  test('empty task list renders the "no tasks assigned" empty state', async () => {
    fetchAllAssignedTasks.mockResolvedValue({ ok: true, body: { tasks: [] } });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/No tasks assigned to kato/i)).toBeInTheDocument();
    });
  });

  test('Adopt button is disabled until a row is picked', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-1')] },
    });

    renderModal();

    await waitFor(() => expect(screen.getByText('KAT-1')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Adopt task/i })).toBeDisabled();
  });
});


describe('AdoptTaskModal — filter + select', () => {

  test('search box narrows the visible tasks by summary', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: {
        tasks: [
          _task('KAT-1', { summary: 'fix login redirect bug' }),
          _task('KAT-2', { summary: 'add metrics dashboard' }),
        ],
      },
    });

    renderModal();

    await waitFor(() => expect(screen.getByText('KAT-1')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search by id/i), {
      target: { value: 'metrics' },
    });

    expect(screen.queryByText('KAT-1')).not.toBeInTheDocument();
    expect(screen.getByText('KAT-2')).toBeInTheDocument();
  });

  test('search with no match shows "no tasks match" empty state', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-1')] },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('KAT-1')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search by id/i), {
      target: { value: 'zzz-no-match' },
    });

    expect(screen.getByText(/No tasks match/i)).toBeInTheDocument();
  });

  test('clicking a row enables the Adopt button', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-1')] },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('KAT-1')).toBeInTheDocument());

    fireEvent.click(screen.getByText('KAT-1'));

    expect(screen.getByRole('button', { name: /Adopt task/i })).not.toBeDisabled();
  });

  test('alreadyAdoptedIds annotates rows with "already in kato"', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-1'), _task('KAT-2')] },
    });

    renderModal({ alreadyAdoptedIds: new Set(['KAT-1']) });

    await waitFor(() => expect(screen.getByText('KAT-1')).toBeInTheDocument());
    expect(screen.getByText(/already in kato/i)).toBeInTheDocument();
  });
});


describe('AdoptTaskModal — submit', () => {

  test('success: calls adoptTask(id), fires onAdopted + onClose', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-7')] },
    });
    adoptTask.mockResolvedValue({
      ok: true,
      body: { task_id: 'KAT-7', cloned_repositories: ['repo-x'] },
    });

    const { onAdopted, onClose } = renderModal();

    await waitFor(() => expect(screen.getByText('KAT-7')).toBeInTheDocument());

    fireEvent.click(screen.getByText('KAT-7'));
    fireEvent.click(screen.getByRole('button', { name: /Adopt task/i }));

    await waitFor(() => expect(adoptTask).toHaveBeenCalledWith('KAT-7'));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onAdopted).toHaveBeenCalled();
  });

  test('failure: surfaces toast error, modal stays open', async () => {
    fetchAllAssignedTasks.mockResolvedValue({
      ok: true,
      body: { tasks: [_task('KAT-7')] },
    });
    adoptTask.mockResolvedValue({
      ok: false,
      body: { error: 'workspace exists with conflicts' },
    });

    const { onClose } = renderModal();

    await waitFor(() => expect(screen.getByText('KAT-7')).toBeInTheDocument());

    fireEvent.click(screen.getByText('KAT-7'));
    fireEvent.click(screen.getByRole('button', { name: /Adopt task/i }));

    await waitFor(() => expect(adoptTask).toHaveBeenCalled());

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error',
      message: 'workspace exists with conflicts',
    }));
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe('AdoptTaskModal — close affordances', () => {

  test('Cancel button calls onClose', async () => {
    fetchAllAssignedTasks.mockResolvedValue({ ok: true, body: { tasks: [] } });

    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('× close button calls onClose', async () => {
    fetchAllAssignedTasks.mockResolvedValue({ ok: true, body: { tasks: [] } });

    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
