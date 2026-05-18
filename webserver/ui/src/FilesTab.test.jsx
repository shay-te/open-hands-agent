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
  syncTaskComments: vi.fn(),
  syncTaskRepositories: vi.fn(),
}));
vi.mock('./stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
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
    expect(client.get('src/a.js')).toBe(2);
    expect(client.get('src/b.js')).toBe(1);
    expect(client.has('')).toBe(false);
  });

  test('missing repo_id buckets under "" (single-repo task)', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', file_path: 'app.py', parent_id: '' },
    ]);
    expect(meta.get('').get('app.py')).toBe(1);
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
    expect(meta.get('r').get('src/a.js')).toBe(1);
  });

  test('kato_status=addressed threads still show a tree badge until user-resolved', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'queued' },
      { id: 'c2', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'addressed' },
    ]);
    expect(meta.get('r').get('src/b.js')).toBe(2);
  });

  test('queued and working kato comments are counted in the tree badge', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'queued' },
      { id: 'c2', repo_id: 'r', file_path: 'src/b.js', parent_id: '', kato_status: 'working' },
    ]);
    expect(meta.get('r').get('src/b.js')).toBe(2);
  });

  test('file with only user-resolved threads shows no badge', () => {
    const meta = buildFilesCommentMeta([
      { id: 'c1', repo_id: 'r', file_path: 'src/c.js', parent_id: '', status: 'resolved' },
    ]);
    expect(meta.get('r')?.has('src/c.js')).toBeFalsy();
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
      expect(screen.getByLabelText('2 comments')).toBeInTheDocument();
    });
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
