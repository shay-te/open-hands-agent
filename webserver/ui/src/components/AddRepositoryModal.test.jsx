// Component-level tests for AddRepositoryModal — the "+ Add repository"
// picker on the Files tab. Pulls the kato inventory, filters out repos
// already on the task, lets the operator pick one, then POSTs the
// add-repository endpoint. The interesting wiring lives in:
//
//   - Initial load → loading state → rendered list.
//   - Search box narrows the list (id / owner / slug).
//   - alreadyAttachedIds filter strips repos UI-side.
//   - Confirm button stays disabled until a row is picked.
//   - Confirm calls addTaskRepository(taskId, selectedId), then onAdded + onClose.
//   - Error path: api error → toast fires, modal stays open, onClose NOT called.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  addTaskRepository: vi.fn(),
  fetchInventoryRepositories: vi.fn(),
}));

vi.mock('../stores/toastStore.js', () => {
  const show = vi.fn();
  return {
    toast: {
      show,
      // Mirror the real errorFromResult: build the canonical
      // { kind:'error', title, message } envelope and forward to show.
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

import AddRepositoryModal from './AddRepositoryModal.jsx';
import { addTaskRepository, fetchInventoryRepositories } from '../api.js';
import { toast } from '../stores/toastStore.js';


function _repo(id, extra = {}) {
  return {
    id,
    owner: extra.owner || `owner-${id}`,
    repo_slug: extra.repo_slug || `slug-${id}`,
    local_path: extra.local_path || `/tmp/${id}`,
  };
}


function renderModal({
  taskId = 'TASK-1',
  alreadyAttachedIds = new Set(),
  onClose = vi.fn(),
  onAdded = vi.fn(),
} = {}) {
  return {
    onClose,
    onAdded,
    ...render(
      <AddRepositoryModal
        taskId={taskId}
        alreadyAttachedIds={alreadyAttachedIds}
        onClose={onClose}
        onAdded={onAdded}
      />,
    ),
  };
}


beforeEach(() => {
  fetchInventoryRepositories.mockReset();
  addTaskRepository.mockReset();
  toast.show.mockReset();
});


describe('AddRepositoryModal — render + load', () => {

  test('renders heading with task id and help copy', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    renderModal({ taskId: 'KAT-42' });

    expect(screen.getByRole('heading', { name: /Add repository to KAT-42/i }))
      .toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search by id, owner, or slug/i))
      .toBeInTheDocument();
  });

  test('shows loading state then list of repos from inventory', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha'), _repo('beta')] },
    });

    renderModal();

    expect(screen.getByText(/Loading repositories/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('alpha')).toBeInTheDocument();
      expect(screen.getByText('beta')).toBeInTheDocument();
    });
  });

  test('inventory load error renders the error message inside the list', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: false, error: 'kaboom' });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText('kaboom')).toBeInTheDocument();
    });
  });

  test('confirm button is initially disabled (no row selected)', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: /Add to task/i })).toBeDisabled();
  });
});


describe('AddRepositoryModal — filtering', () => {

  test('alreadyAttachedIds removes those repos from the list', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha'), _repo('beta'), _repo('gamma')] },
    });

    renderModal({ alreadyAttachedIds: new Set(['beta']) });

    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());
    expect(screen.queryByText('beta')).not.toBeInTheDocument();
    expect(screen.getByText('gamma')).toBeInTheDocument();
  });

  test('typing in the search box narrows the visible repos', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: {
        repositories: [
          _repo('alpha', { owner: 'acme' }),
          _repo('beta', { owner: 'wayne' }),
        ],
      },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search by id/i), {
      target: { value: 'wayne' },
    });

    expect(screen.queryByText('alpha')).not.toBeInTheDocument();
    expect(screen.getByText('beta')).toBeInTheDocument();
  });

  test('empty inventory shows the "no repositories configured" message', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/No repositories configured in kato/i)).toBeInTheDocument();
    });
  });

  test('all repos attached shows the "everything is already attached" message', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });

    renderModal({ alreadyAttachedIds: new Set(['alpha']) });

    await waitFor(() => {
      expect(screen.getByText(/already attached/i)).toBeInTheDocument();
    });
  });
});


describe('AddRepositoryModal — selection + submit', () => {

  test('clicking a row selects it; confirm button enables', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.click(screen.getByText('alpha'));

    expect(screen.getByRole('button', { name: /Add to task/i })).not.toBeDisabled();
  });

  test('submit calls addTaskRepository(taskId, repoId) and fires onAdded + onClose on success', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });
    addTaskRepository.mockResolvedValue({
      ok: true,
      body: {
        tag_added: true,
        tag_name: 'kato:repo:alpha',
        sync: { added_repositories: ['alpha'] },
      },
    });

    const { onAdded, onClose } = renderModal({ taskId: 'TASK-1' });
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.click(screen.getByText('alpha'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));

    await waitFor(() => {
      expect(addTaskRepository).toHaveBeenCalledWith('TASK-1', 'alpha');
    });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
    expect(onAdded).toHaveBeenCalled();
  });

  test('error path: api returns ok:false → toast error + modal stays open (onClose not called)', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });
    addTaskRepository.mockResolvedValue({
      ok: false,
      body: { error: 'permission denied' },
    });

    const { onClose } = renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.click(screen.getByText('alpha'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));

    await waitFor(() => {
      expect(addTaskRepository).toHaveBeenCalled();
    });

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error',
      message: 'permission denied',
    }));
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe('AddRepositoryModal — close affordances', () => {

  test('Cancel button calls onClose', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('× close button calls onClose', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
