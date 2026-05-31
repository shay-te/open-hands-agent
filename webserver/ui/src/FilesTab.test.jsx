// Tests for FilesTab. The file is 632 lines composing the file
// tree + commit dropdown + sync action. We focus on the pure
// helper that maps sync api results to toast shape — every other
// behavior is a render-only composition of well-tested deps
// (react-arborist for the tree).

import { beforeEach, describe, test, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('./api.js', () => ({
  fetchDiff: vi.fn(),
  fetchFileTree: vi.fn(),
  fetchFileContent: vi.fn(),
  fetchRepoCommits: vi.fn().mockResolvedValue({ ok: true, body: [] }),
  fetchTaskComments: vi.fn().mockResolvedValue({ ok: true, body: { comments: [] } }),
  syncTaskRepositories: vi.fn(),
}));
vi.mock('./stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
  toastResult: vi.fn(),
}));

import FilesTab, {
  buildFilesCommentMeta,
  buildFilesDiffMeta,
  filterChangedFileTree,
  formatSyncResult,
} from './FilesTab.jsx';
import { fetchDiff, fetchFileTree, fetchTaskComments } from './api.js';

const FILE_TREE_PAYLOAD = {
  trees: [{
    repo_id: 'client',
    cwd: '/tmp/client',
    tree: [{
      name: 'src',
      path: '/tmp/client/src',
      children: [{
        name: 'Changed.js',
        path: '/tmp/client/src/Changed.js',
      }, {
        name: 'Unchanged.js',
        path: '/tmp/client/src/Unchanged.js',
      }],
    }],
    changed_files: ['src/Changed.js'],
    conflicted_files: [],
  }],
};

const DIFF_PAYLOAD = {
  diffs: [{
    repo_id: 'client',
    cwd: '/tmp/client',
    diff: [
      'diff --git a/src/Changed.js b/src/Changed.js',
      'index 1111111..2222222 100644',
      '--- a/src/Changed.js',
      '+++ b/src/Changed.js',
      '@@ -1 +1,2 @@',
      '-old',
      '+new',
      '+newer',
      '',
    ].join('\n'),
    conflicted_files: [],
  }],
};

beforeEach(() => {
  fetchDiff.mockResolvedValue({ diffs: [] });
  fetchFileTree.mockResolvedValue({ trees: [] });
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});


describe('formatSyncResult — toast shape for /api/sync-repositories', () => {

  test('failed request → error toast with the api message', () => {
    expect(formatSyncResult({ ok: false, error: 'timeout' })).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'timeout',
    });
  });

  test('failed request → error toast falls back to body.error', () => {
    expect(formatSyncResult({
      ok: false, body: { error: 'auth' },
    })).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'auth',
    });
  });

  test('failed request with no error → "unknown error" placeholder', () => {
    expect(formatSyncResult({ ok: false })).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'unknown error',
    });
  });

  test('null result is treated as failed', () => {
    // Defensive — caller might pass a falsy value.
    expect(formatSyncResult(null)).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'unknown error',
    });
  });

  test('all-failed → red error toast', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: [],
        failed_repositories: [
          { repository_id: 'r1', error: 'permission denied' },
          { repository_id: 'r2', error: 'not found' },
        ],
      },
    });
    expect(result.kind).toBe('error');
    expect(result.title).toBe('Sync failed');
    expect(result.message).toContain('r1: permission denied');
    expect(result.message).toContain('r2: not found');
  });

  test('partial success → amber warning toast', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['r1', 'r2'],
        failed_repositories: [{ repository_id: 'r3', error: 'auth' }],
      },
    });
    expect(result.kind).toBe('warning');
    expect(result.title).toBe('Sync partially succeeded');
    expect(result.message).toContain('added 2 repo(s)');
    expect(result.message).toContain('r3: auth');
  });

  test('nothing-to-add → green success toast with "already in sync" message', () => {
    // Operator clicks Sync when everything's already cloned. We
    // want a green toast saying so — never silent.
    const result = formatSyncResult({
      ok: true,
      body: { added_repositories: [], failed_repositories: [] },
    });
    expect(result.kind).toBe('success');
    expect(result.title).toMatch(/already in sync/i);
  });

  test('repos added cleanly → green success with the added list', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['client', 'backend'],
        failed_repositories: [],
      },
    });
    expect(result.kind).toBe('success');
    expect(result.title).toContain('Added 2');
    expect(result.message).toContain('client');
    expect(result.message).toContain('backend');
  });

  test('empty body produces "already in sync"', () => {
    const result = formatSyncResult({ ok: true, body: {} });
    expect(result.kind).toBe('success');
    expect(result.title).toMatch(/already in sync/i);
  });

  // ----- requires_session_restart surfacing -----
  // When the freshly-cloned repo lives outside the live chat's
  // --add-dir set, the operator MUST close + reopen the tab.
  // Without surfacing this, Claude silently refuses to write to
  // the new repo and the operator is left to guess why.

  test('added cleanly + restart needed → amber warning with restart hint', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['new'],
        failed_repositories: [],
        requires_session_restart: true,
      },
    });
    expect(result.kind).toBe('warning');
    expect(result.title).toContain('restart chat tab');
    expect(result.message).toMatch(/close and reopen/i);
    expect(result.message).toContain('new');
  });

  test('added cleanly + no restart needed → stays green success', () => {
    // No live session OR session already had the new path → no
    // restart hint, no kind downgrade.
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['new'],
        failed_repositories: [],
        requires_session_restart: false,
      },
    });
    expect(result.kind).toBe('success');
    expect(result.title).not.toMatch(/restart/i);
    expect(result.message).not.toMatch(/close and reopen/i);
  });

  test('partial success + restart needed → restart hint appended to message', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['client'],
        failed_repositories: [{ repository_id: 'backend', error: 'auth' }],
        requires_session_restart: true,
      },
    });
    expect(result.kind).toBe('warning');
    expect(result.message).toContain('client');
    expect(result.message).toContain('backend: auth');
    expect(result.message).toMatch(/close and reopen/i);
  });

  test('all-failed + restart flag → restart hint NOT shown (nothing was actually cloned)', () => {
    // Nothing landed → no point telling the operator to restart;
    // there are no new repos for the restart to surface. We DO NOT
    // render the hint even if the backend pessimistically set it.
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: [],
        failed_repositories: [{ repository_id: 'r1', error: 'permission' }],
        requires_session_restart: true,
      },
    });
    expect(result.kind).toBe('error');
    expect(result.message).not.toMatch(/close and reopen/i);
  });

  test('nothing-to-add ignores the restart flag (no work happened)', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: [],
        failed_repositories: [],
        requires_session_restart: true,
      },
    });
    expect(result.kind).toBe('success');
    expect(result.title).toMatch(/already in sync/i);
    expect(result.message).not.toMatch(/close and reopen/i);
  });

  test('restart flag missing entirely → falsy by default', () => {
    // Defensive: older backends or partial responses might omit
    // the field. The renderer must NOT spuriously show "restart".
    const result = formatSyncResult({
      ok: true,
      body: { added_repositories: ['r1'], failed_repositories: [] },
    });
    expect(result.kind).toBe('success');
    expect(result.message).not.toMatch(/close and reopen/i);
  });
});


describe('buildFilesDiffMeta', () => {

  test('indexes changed files by repo and cwd with kind + line stats', () => {
    const meta = buildFilesDiffMeta([{
      repo_id: 'client',
      cwd: '/tmp/client',
      files: [{
        type: 'add',
        oldPath: '/dev/null',
        newPath: 'src/NewFile.js',
        hunks: [{
          changes: [
            { type: 'insert' },
            { type: 'insert' },
            { type: 'delete' },
          ],
        }],
      }],
    }]);
    const byRepo = meta.get('client');
    const byCwd = meta.get('/tmp/client');
    expect(byRepo).toBe(byCwd);
    expect(byRepo.get('src/NewFile.js')).toMatchObject({
      kind: 'add',
      stats: { added: 2, deleted: 1 },
    });
    expect(byRepo.get('src/NewFile.js').file.newPath).toBe('src/NewFile.js');
  });
});


describe('buildFilesCommentMeta', () => {

  test('counts root threads per repo+file, ignores replies', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'client', file_path: 'src/a.js', parent_id: '' },
      { id: 'c2', repo_id: 'client', file_path: 'src/a.js', parent_id: '' },
      // reply to c1 — must NOT add to the count
      { id: 'r1', repo_id: 'client', file_path: 'src/a.js', parent_id: 'c1' },
      { id: 'c3', repo_id: 'client', file_path: 'src/b.js', parent_id: '' },
      // blank file_path (file-level on no file) — skipped
      { id: 'c4', repo_id: 'client', file_path: '', parent_id: '' },
    ]);
    const client = meta.get('client');
    expect(client.get('src/a.js').count).toBe(2);
    expect(client.get('src/b.js').count).toBe(1);
    expect(client.has('')).toBe(false);
  });

  test('missing repo_id buckets under "" (single-repo task)', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', file_path: 'app.py', parent_id: '' },
    ]);
    expect(meta.get('').get('app.py').count).toBe(1);
  });

  test('empty / nullish input → empty map', () => {
    expect(buildFilesCommentMeta([]).size).toBe(0);
    expect(buildFilesCommentMeta(null).size).toBe(0);
    expect(buildFilesCommentMeta(undefined).size).toBe(0);
  });

  test('resolved threads are not counted', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/a.js', parent_id: '', status: 'open' },
      { id: 'c2', repo_id: 'r', file_path: 'src/a.js', parent_id: '', status: 'resolved' },
    ]);
    expect(meta.get('r').get('src/a.js').count).toBe(1);
  });

  test('kato_status=addressed threads still show a tree badge until user-resolved', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'queued' },
      { id: 'c2', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'addressed' },
    ]);
    expect(meta.get('r').get('src/b.js').count).toBe(2);
  });

  test('queued and working kato comments are counted in the tree badge', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'queued' },
      { id: 'c2', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'working' },
    ]);
    expect(meta.get('r').get('src/b.js').count).toBe(2);
  });

  test('file with only user-resolved threads shows no badge', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/c.js', parent_id: '', status: 'resolved' },
    ]);
    expect(meta.get('r')?.has('src/c.js')).toBeFalsy();
  });

  test('badge status follows a single thread kato_status', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/a.js', parent_id: '', kato_status: 'in_progress' },
    ]);
    expect(meta.get('r').get('src/a.js').status).toBe('in_progress');
  });

  test('badge status is the most-urgent across threads (failed > queued > addressed)', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/a.js', parent_id: '', kato_status: 'addressed' },
      { id: 'c2', repo_id: 'r', file_path: 'src/a.js', parent_id: '', kato_status: 'failed' },
      { id: 'c3', repo_id: 'r', file_path: 'src/a.js', parent_id: '', kato_status: 'queued' },
    ]);
    const entry = meta.get('r').get('src/a.js');
    expect(entry.count).toBe(3);
    expect(entry.status).toBe('failed');
  });

  test('outdated comments are not counted (no phantom badge)', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/a.js', parent_id: '', outdated: true },
      { id: 'c2', repo_id: 'r', file_path: 'src/a.js', parent_id: '' },
    ]);
    // Only the live comment counts; the outdated one is dropped.
    expect(meta.get('r').get('src/a.js').count).toBe(1);
  });

  test('a file whose only comment is outdated shows no badge', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/b.js', parent_id: '', outdated: true },
    ]);
    expect(meta.get('r')?.has('src/b.js')).toBeFalsy();
  });

  test('unknown / idle kato_status leaves the badge status blank (neutral)', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/a.js', parent_id: '', kato_status: 'idle' },
      { id: 'c2', repo_id: 'r', file_path: 'src/a.js', parent_id: '' },
    ]);
    expect(meta.get('r').get('src/a.js').status).toBe('');
  });
});


describe('filterChangedFileTree', () => {

  test('keeps ancestors for changed files that match the search', () => {
    const tree = [{
      kind: 'folder',
      key: 'folder:src',
      name: 'src',
      children: [{
        kind: 'file',
        key: 'file:src/App.js',
        name: 'App.js',
        file: { type: 'modify', oldPath: 'src/App.js', newPath: 'src/App.js' },
        stats: { added: 1, deleted: 0 },
      }],
      stats: { added: 1, deleted: 0 },
    }];
    const filtered = filterChangedFileTree(tree, 'app');
    expect(filtered).toHaveLength(1);
    expect(filtered[0].name).toBe('src');
    expect(filtered[0].children[0].name).toBe('App.js');
  });
});


describe('FilesTab — render shell', () => {

  test('renders without crashing when activeTaskId is null', () => {
    const { container } = render(
      <FilesTab activeTaskId={null} onAddToChat={vi.fn()} />,
    );
    expect(container).toBeInTheDocument();
  });

  test('defaults to changed files and All toggles the full tree', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    expect(screen.getByText('client').closest('header'))
      .toHaveClass('sticky-section-header');
    expect(screen.getByText('Changed.js')).toBeInTheDocument();
    expect(screen.queryByText('Unchanged.js')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all files' }));
    fireEvent.click(await screen.findByText('src'));
    await waitFor(() => {
      expect(screen.getByText('Unchanged.js')).toBeInTheDocument();
    });
  });

  test('file-title focus signal selects and scrolls the changed file row', async () => {
    const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    render(
      <FilesTab
        taskId="T1"
        onOpenFile={vi.fn()}
        focusFileTarget={{
          repoId: 'client',
          relativePath: 'src/Changed.js',
          requestId: 1,
        }}
      />,
    );

    const label = await screen.findByText('Changed.js');
    const row = label.closest('button');
    await waitFor(() => {
      expect(row).toHaveClass('selected');
      expect(row.scrollIntoView).toHaveBeenCalledWith({
        behavior: 'smooth',
        block: 'center',
      });
    });
    window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  });

  test('does NOT re-scroll the tree on a background refresh that CHANGES data (same focus request)', async () => {
    // Regression: the focus effect listed data-refresh values in its
    // deps, so the 5s poll / 1.2s workspace bump re-fired it and the tree
    // smooth-scrolled itself every few seconds. It must act once per
    // operator click (requestId), not on every refresh — even one that
    // brings real changes and forces a full re-render.
    const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
    // App keeps the same focus object between clicks — only requestId
    // changes on a NEW click, never on a poll.
    const focus = { repoId: 'client', relativePath: 'src/Changed.js', requestId: 1 };
    const { rerender } = render(
      <FilesTab taskId="T1" onOpenFile={vi.fn()} focusFileTarget={focus} workspaceVersion={1} />,
    );
    const label = await screen.findByText('Changed.js');
    await waitFor(() => {
      expect(label.closest('button').scrollIntoView).toHaveBeenCalledTimes(1);
    });
    window.HTMLElement.prototype.scrollIntoView.mockClear();

    // Poll brings a REAL change (a new comment) → the payload signature
    // differs, so the tree fully re-renders (the requestId guard, not the
    // signature guard, is what must keep it from re-scrolling).
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [{ id: 'c1', file_path: 'src/Changed.js', line: 1, kato_status: 'queued' }] },
    });
    const fetchesBefore = fetchFileTree.mock.calls.length;
    rerender(
      <FilesTab taskId="T1" onOpenFile={vi.fn()} focusFileTarget={focus} workspaceVersion={2} />,
    );
    await waitFor(() => {
      expect(fetchFileTree.mock.calls.length).toBeGreaterThan(fetchesBefore);
    });
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(window.HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
    window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  });

  test('marks conflicted files in the changed tree and full tree', async () => {
    const fileTreePayload = {
      trees: [{
        ...FILE_TREE_PAYLOAD.trees[0],
        conflicted_files: ['src/Changed.js'],
      }],
    };
    const diffPayload = {
      diffs: [{
        ...DIFF_PAYLOAD.diffs[0],
        conflicted_files: ['src/Changed.js'],
      }],
    };
    fetchFileTree.mockResolvedValue(fileTreePayload);
    fetchDiff.mockResolvedValue(diffPayload);
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    expect(screen.getByLabelText(/merge conflict/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all files' }));
    fireEvent.click(await screen.findByText('src'));
    await waitFor(() => {
      expect(screen.getAllByLabelText(/merge conflict/i).length).toBeGreaterThan(0);
    });
  });

  test('shows a comment-count badge on a file with open threads', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: {
        comments: [
          { id: 'c1', repo_id: 'client', file_path: 'src/Changed.js',
            parent_id: '' },
          { id: 'c2', repo_id: 'client', file_path: 'src/Changed.js',
            parent_id: '' },
          { id: 'r1', repo_id: 'client', file_path: 'src/Changed.js',
            parent_id: 'c1' }, // reply — not counted
        ],
      },
    });
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    // 2 root threads on src/Changed.js (reply excluded).
    await waitFor(() => {
      expect(screen.getByLabelText('Jump to 2 comments')).toBeInTheDocument();
    });
  });

  test('clicking the comment badge opens the diff and focuses the comment', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: {
        comments: [
          { id: 'c1', repo_id: 'client', file_path: 'src/Changed.js',
            parent_id: '', kato_status: 'queued' },
        ],
      },
    });
    const onOpenFile = vi.fn();
    render(<FilesTab taskId="T1" onOpenFile={onOpenFile} />);
    const badge = await screen.findByLabelText('Jump to 1 comment');
    fireEvent.click(badge);
    // ``focusComment`` tells DiffPane to scroll to the thread, not just
    // the file; ``view: 'diff'`` is where comments are shown.
    expect(onOpenFile).toHaveBeenCalledWith(
      expect.objectContaining({
        relativePath: 'src/Changed.js',
        view: 'diff',
        focusComment: true,
      }),
    );
  });

  test('right-clicking a changed file copies repo-prefixed path', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    const label = await screen.findByText('Changed.js');

    fireEvent.contextMenu(label.closest('button'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Copy relative path' }));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('client:src/Changed.js');
    });
  });

  test('right-clicking a changed folder copies repo-prefixed path', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    const folder = await screen.findByText('src');

    fireEvent.contextMenu(folder.closest('button'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Copy relative path' }));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('client:src');
    });
  });

  test('no comment badge on files without threads', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    expect(screen.queryByLabelText(/comment/i)).not.toBeInTheDocument();
  });
});


// --------------------------------------------------------------------------
// Chaos / random-order driver against the REAL FilesTab component.
//
// The fixed-sequence tests above pin specific behaviours. A real user
// toggles "Show all files" mid-scroll, right-clicks a folder, clicks
// a file, scrolls, toggles back, right-clicks again — in whatever
// order their attention drifts. A bug like "context menu sticks open
// after toggle" or "selected row clears when the tree re-renders"
// only shows up under unpredictable ordering.
//
// We don't stub FilesTab here — the api.js layer is mocked (as in the
// rest of this file) but everything FilesTab actually composes runs:
// react-arborist, the context menu, the tree-filter, the changed/all
// toggle, the focus-target selection logic. After each random click
// we re-assert the load-bearing invariants.
// --------------------------------------------------------------------------

function xorshift32(seed) {
  let state = (seed | 0) || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return ((state >>> 0) / 0xffffffff);
  };
}

// Larger payload — multiple repos, multiple files, mix of changed and
// unchanged so the chaos driver has interesting state to land on.
const CHAOS_FILE_TREE = {
  trees: [
    {
      repo_id: 'client',
      cwd: '/tmp/client',
      tree: [{
        name: 'src',
        path: '/tmp/client/src',
        children: [
          { name: 'Changed.js', path: '/tmp/client/src/Changed.js' },
          { name: 'Unchanged.js', path: '/tmp/client/src/Unchanged.js' },
          { name: 'AlsoChanged.js', path: '/tmp/client/src/AlsoChanged.js' },
        ],
      }],
      changed_files: ['src/Changed.js', 'src/AlsoChanged.js'],
      conflicted_files: [],
    },
    {
      repo_id: 'backend',
      cwd: '/tmp/backend',
      tree: [{
        name: 'api',
        path: '/tmp/backend/api',
        children: [
          { name: 'handler.py', path: '/tmp/backend/api/handler.py' },
        ],
      }],
      changed_files: ['api/handler.py'],
      conflicted_files: [],
    },
  ],
};

const CHAOS_DIFFS = {
  diffs: [
    {
      // One diff entry per repo with ALL changed files concatenated —
      // matches how the production /api/diff endpoint returns data.
      repo_id: 'client',
      cwd: '/tmp/client',
      diff: [
        'diff --git a/src/Changed.js b/src/Changed.js',
        'index 1111111..2222222 100644',
        '--- a/src/Changed.js',
        '+++ b/src/Changed.js',
        '@@ -1 +1,2 @@',
        '-old',
        '+new',
        '+newer',
        'diff --git a/src/AlsoChanged.js b/src/AlsoChanged.js',
        'index 3333333..4444444 100644',
        '--- a/src/AlsoChanged.js',
        '+++ b/src/AlsoChanged.js',
        '@@ -1 +1 @@',
        '-foo',
        '+bar',
        '',
      ].join('\n'),
      conflicted_files: [],
    },
    {
      repo_id: 'backend',
      cwd: '/tmp/backend',
      diff: [
        'diff --git a/api/handler.py b/api/handler.py',
        'index 5555555..6666666 100644',
        '--- a/api/handler.py',
        '+++ b/api/handler.py',
        '@@ -1 +1 @@',
        '-pass',
        '+return 1',
        '',
      ].join('\n'),
      conflicted_files: [],
    },
  ],
};

// IMPATIENT_INPUTS — the chaos driver only uses these as labels;
// they aren't typed into anything in this view, but the strings
// flow into headers via the focus-target API in other tests. The
// constant lives here to keep "what an impatient human says" in
// one place per file.
const IMPATIENT_INPUTS = [
  'fix it',
  'whats wrong with you please fix it',
  'do it',
  'ugh another null pointer',
  'help me!!!',
];

// One callable per UI action the driver might fire. Each one
// queries the DOM at call time (rows / folders re-render after
// every interaction), so a stale handle from a previous tick
// never gets clicked. Missing target is a no-op — the driver
// shouldn't blow up just because nothing is selectable yet.
function chaosActions(container) {
  return [
    {
      name: 'toggle-show-all',
      run: () => {
        // The toggle's aria-label flips between "Show all files" (when
        // the changed view is active) and "Showing all files" (when
        // the all-files view is already active).
        const btn = screen.queryByRole('button', { name: 'Show all files' })
          || screen.queryByRole('button', { name: 'Showing all files' });
        if (btn) fireEvent.click(btn);
      },
    },
    {
      name: 'expand-src',
      run: () => {
        const folder = screen.queryByText('src');
        if (folder) fireEvent.click(folder);
      },
    },
    {
      name: 'expand-api',
      run: () => {
        const folder = screen.queryByText('api');
        if (folder) fireEvent.click(folder);
      },
    },
    {
      name: 'click-changed-file',
      run: () => {
        const label = screen.queryByText('Changed.js');
        if (label) fireEvent.click(label);
      },
    },
    {
      name: 'click-also-changed-file',
      run: () => {
        const label = screen.queryByText('AlsoChanged.js');
        if (label) fireEvent.click(label);
      },
    },
    {
      name: 'click-handler-file',
      run: () => {
        const label = screen.queryByText('handler.py');
        if (label) fireEvent.click(label);
      },
    },
    {
      name: 'right-click-changed-file',
      run: () => {
        const label = screen.queryByText('Changed.js');
        const target = label && label.closest('button');
        if (target) fireEvent.contextMenu(target);
      },
    },
    {
      name: 'right-click-src-folder',
      run: () => {
        const folder = screen.queryByText('src');
        const target = folder && folder.closest('button');
        if (target) fireEvent.contextMenu(target);
      },
    },
    {
      name: 'copy-from-menu',
      run: () => {
        const item = screen.queryByRole('menuitem', { name: 'Copy relative path' });
        if (item) fireEvent.click(item);
      },
    },
    {
      name: 'close-menu-via-escape',
      run: () => {
        fireEvent.keyDown(document.body, { key: 'Escape', code: 'Escape' });
      },
    },
  ];
}

describe('FilesTab — chaos / random button mashing', () => {

  beforeEach(() => {
    fetchFileTree.mockResolvedValue(CHAOS_FILE_TREE);
    fetchDiff.mockResolvedValue(CHAOS_DIFFS);
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
  });

  const SEEDS = [7, 99, 4096, 0xc0ffee];

  SEEDS.forEach((seed) => {
    test(`survives 50 random interactions with seed=${seed}`, async () => {
      const { container } = render(
        <FilesTab taskId={`CHAOS-${seed}`} onOpenFile={vi.fn()} />,
      );
      // Wait for the initial diff load so the tree is real before we
      // start mashing. Two repos in the chaos payload → two "Lines
      // updated" headers (one per repo). ``findAllByText`` returns
      // once at least one is present.
      const headers = await screen.findAllByText('Lines updated');
      expect(headers.length).toBeGreaterThanOrEqual(1);

      // Seed the clipboard so the post-loop invariant has teeth even
      // if the random sequence never lands on a successful menu copy.
      // Without this, a chaos seed that never opens the menu in the
      // right order leaves ``writeText.mock.calls`` empty and the
      // forEach below passes vacuously.
      const seedFile = screen.getByText('Changed.js');
      fireEvent.contextMenu(seedFile.closest('button'));
      fireEvent.click(screen.getByRole('menuitem', {
        name: 'Copy relative path',
      }));
      await Promise.resolve();
      const seedCallCount = navigator.clipboard.writeText.mock.calls.length;
      expect(seedCallCount).toBeGreaterThan(0);

      const rng = xorshift32(seed);
      const actions = chaosActions(container);
      const log = [];
      for (let i = 0; i < 50; i += 1) {
        const action = actions[Math.floor(rng() * actions.length)];
        log.push(action.name);
        action.run();
        // eslint-disable-next-line no-await-in-loop
        await Promise.resolve();

        // Invariants that must hold after every interaction:
        //   1. The root container is still mounted (no unmount/crash).
        expect(container.firstChild).not.toBeNull();
        //   2. The view-mode toggle is ALWAYS present — its
        //      aria-label flips between "Show all files" and
        //      "Showing all files" depending on state. The button
        //      itself must never disappear.
        const toggle = screen.queryByRole('button', { name: 'Show all files' })
          || screen.queryByRole('button', { name: 'Showing all files' });
        expect(toggle).not.toBeNull();
      }

      // After the mashing:
      //   3. Force a final right-click + copy. This is the load-bearing
      //      assertion: the seed copy only proves "copy works once on
      //      a fresh render". This proves "copy still works AFTER the
      //      chaos sequence" — catches a state-machine bug where some
      //      sequence of toggles/menus leaves the context menu wired
      //      to nothing, or makes the copy item silently a no-op.
      const callCountAfterChaos = navigator.clipboard.writeText.mock.calls.length;
      function pickTreeRowButton(text) {
        // Match the text node that's inside an actual tree-row button.
        // Section headers / breadcrumbs share the same text but are
        // not buttons.
        const candidates = screen.queryAllByText(text);
        for (const node of candidates) {
          const btn = node.closest('button');
          if (btn) return btn;
        }
        return null;
      }
      // Drive the toggle into "changed only" mode (button label
      // "Show all files" means changed-only is currently active).
      // The changed-only view always shows changed files at the top
      // level without needing a folder to be expanded.
      const showAllBtn = screen.queryByRole(
        'button', { name: 'Show all files' },
      );
      if (!showAllBtn) {
        // Currently in "all" mode — toggle back.
        const showingAllBtn = screen.queryByRole(
          'button', { name: 'Showing all files' },
        );
        if (showingAllBtn) fireEvent.click(showingAllBtn);
        await Promise.resolve();
      }
      let finalTarget = pickTreeRowButton('Changed.js')
        || pickTreeRowButton('AlsoChanged.js')
        || pickTreeRowButton('handler.py');
      if (finalTarget === null) {
        // Chaos may have collapsed src/api — expand them.
        const srcRow = pickTreeRowButton('src');
        if (srcRow) fireEvent.click(srcRow);
        const apiRow = pickTreeRowButton('api');
        if (apiRow) fireEvent.click(apiRow);
        await Promise.resolve();
        finalTarget = pickTreeRowButton('Changed.js')
          || pickTreeRowButton('AlsoChanged.js')
          || pickTreeRowButton('handler.py');
      }
      expect(finalTarget).not.toBeNull();
      fireEvent.contextMenu(finalTarget);
      const finalMenuItem = screen.queryByRole('menuitem', {
        name: 'Copy relative path',
      });
      expect(finalMenuItem).not.toBeNull();
      fireEvent.click(finalMenuItem);
      await Promise.resolve();
      const callCountAfterFinalCopy = navigator.clipboard.writeText.mock.calls.length;
      expect(callCountAfterFinalCopy).toBeGreaterThan(callCountAfterChaos);

      //   4. Every clipboard write (seed + chaos + final) was a
      //      well-formed "<repo>:<path>". A bug where the context-menu
      //      copies the absolute /tmp path or an empty string surfaces
      //      here.
      const allCalls = navigator.clipboard.writeText.mock.calls;
      expect(allCalls.length).toBeGreaterThanOrEqual(seedCallCount + 1);
      allCalls.forEach(([payload]) => {
        expect(typeof payload).toBe('string');
        expect(payload).toMatch(/^(client|backend):/);
        // Never leaks the OS-level path.
        expect(payload.startsWith('/tmp/')).toBe(false);
      });
      // Diagnostic — if the test ever fails, the trace tells us
      // which sequence triggered it.
      if (log.length !== 50) {
        // eslint-disable-next-line no-console
        console.warn('chaos seed=' + seed + ' trace:', log.join(','));
      }
    });
  });

  test('survives chaos on a single-repo task', async () => {
    fetchFileTree.mockResolvedValue({
      trees: [CHAOS_FILE_TREE.trees[0]],
    });
    fetchDiff.mockResolvedValue({
      diffs: CHAOS_DIFFS.diffs.filter((d) => d.repo_id === 'client'),
    });
    const { container } = render(
      <FilesTab taskId="solo-task" onOpenFile={vi.fn()} />,
    );
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();

    // Seed the clipboard so the "no backend: leak" check below has
    // teeth even if the random sequence never lands on a copy.
    const seedFile = screen.getByText('Changed.js');
    fireEvent.contextMenu(seedFile.closest('button'));
    fireEvent.click(screen.getByRole('menuitem', {
      name: 'Copy relative path',
    }));
    await Promise.resolve();
    expect(navigator.clipboard.writeText.mock.calls.length).toBeGreaterThan(0);

    const actions = chaosActions(container);
    const rng = xorshift32(31337);
    for (let i = 0; i < 40; i += 1) {
      const action = actions[Math.floor(rng() * actions.length)];
      action.run();
      // eslint-disable-next-line no-await-in-loop
      await Promise.resolve();
      expect(container.firstChild).not.toBeNull();
    }
    // Final right-click + copy AFTER the chaos so we prove the copy
    // path still works post-mashing — not just on a fresh render.
    const callCountAfterChaos = navigator.clipboard.writeText.mock.calls.length;
    const showAllBtn = screen.queryByRole(
      'button', { name: 'Show all files' },
    );
    if (!showAllBtn) {
      const showingAllBtn = screen.queryByRole(
        'button', { name: 'Showing all files' },
      );
      if (showingAllBtn) fireEvent.click(showingAllBtn);
      await Promise.resolve();
    }
    function findTreeRowButton(label) {
      const match = screen.queryAllByText(label).find(
        (n) => n.closest('button') !== null,
      );
      return match ? match.closest('button') : null;
    }
    // The chaos may have collapsed the src folder. If no file row is
    // visible, click src to expand and try again.
    let finalTarget = findTreeRowButton('Changed.js')
      || findTreeRowButton('AlsoChanged.js');
    if (finalTarget === null) {
      const srcRow = findTreeRowButton('src');
      if (srcRow) fireEvent.click(srcRow);
      await Promise.resolve();
      finalTarget = findTreeRowButton('Changed.js')
        || findTreeRowButton('AlsoChanged.js');
    }
    expect(finalTarget).not.toBeNull();
    fireEvent.contextMenu(finalTarget);
    const finalMenuItem = screen.queryByRole(
      'menuitem', { name: 'Copy relative path' },
    );
    expect(finalMenuItem).not.toBeNull();
    fireEvent.click(finalMenuItem);
    await Promise.resolve();
    expect(navigator.clipboard.writeText.mock.calls.length)
      .toBeGreaterThan(callCountAfterChaos);

    // The single-repo task should never produce a "backend:" copy
    // because that repo isn't in the tree. We've already seeded
    // at least one client: copy AND forced one post-chaos copy —
    // so this isn't a vacuous check.
    const allCalls = navigator.clipboard.writeText.mock.calls;
    expect(allCalls.length).toBeGreaterThan(0);
    allCalls.forEach(([payload]) => {
      if (typeof payload === 'string') {
        expect(payload.startsWith('backend:')).toBe(false);
      }
    });
  });
});
