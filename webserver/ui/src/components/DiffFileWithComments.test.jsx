// Component-level tests for DiffFileWithComments. The pure helpers
// (countDiffLines, isLargeFile, decideAutoExpand) already have unit
// tests; this file proves the React wiring:
//
//   - ``initiallyExpanded`` from ChangesTab drives the collapse state.
//   - When unspecified, the per-file fallback rule applies.
//   - The chevron button toggles the diff body.
//   - Collapsed files render only the header, not a placeholder body.

import { beforeEach, describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { parseDiff } from 'react-diff-view';

import DiffFileWithComments from './DiffFileWithComments.jsx';
import { LARGE_FILE_LINE_THRESHOLD } from './diffFileSize.js';

const apiMocks = vi.hoisted(() => {
  return {
    createTaskComment: vi.fn(),
    deleteTaskComment: vi.fn(),
    fetchBaseFileContent: vi.fn(),
    markTaskCommentAddressed: vi.fn(),
    reopenTaskComment: vi.fn(),
    resolveTaskComment: vi.fn(),
  };
});

vi.mock('../api.js', () => {
  return apiMocks;
});

beforeEach(() => {
  Object.values(apiMocks).forEach((mock) => {
    mock.mockReset();
  });
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});

function _file(lineCount, { type = 'modify', path = 'src/file.py' } = {}) {
  return {
    type,
    newPath: path,
    oldPath: path,
    hunks: [{
      content: '@@ -1 +1,1 @@',
      oldStart: 1, oldLines: lineCount,
      newStart: 1, newLines: lineCount,
      changes: new Array(lineCount).fill(0).map((_, i) => ({
        type: 'insert',
        content: `+ line ${i}`,
        lineNumber: i + 1,
        isInsert: true,
      })),
    }],
  };
}


function renderDiff({ file, ...rest } = {}) {
  return render(
    <DiffFileWithComments
      file={file || _file(10)}
      taskId="T1"
      repoId="repo-1"
      repoCwd="/workspace/repo-1"
      comments={[]}
      commentsLoading={false}
      commentsError=""
      onMutated={vi.fn()}
      onAddToChat={vi.fn()}
      {...rest}
    />,
  );
}


describe('DiffFileWithComments — collapse / expand integration', () => {

  test('initiallyExpanded=true: diff body renders inline', () => {
    const { container } = renderDiff({ file: _file(10), initiallyExpanded: true });

    const toggle = container.querySelector('.diff-file-collapse-toggle');
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-label', expect.stringMatching(/collapse diff/i));
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
  });

  test('initiallyExpanded=false: renders only the header and expand chevron', () => {
    const { container } = renderDiff({ file: _file(42), initiallyExpanded: false });

    const toggle = screen.getByRole('button', { name: /expand diff/i });
    expect(toggle).toBeInTheDocument();
    expect(toggle).not.toHaveTextContent(/42 lines/i);
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(container.querySelector('.diff')).not.toBeInTheDocument();
  });

  test('clicking the toggle expands a collapsed diff', () => {
    renderDiff({ file: _file(20), initiallyExpanded: false });

    fireEvent.click(screen.getByRole('button', { name: /expand diff/i }));
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /collapse diff/i })).toBeInTheDocument();
  });

  test('forceExpandToken expands a collapsed diff from parent navigation', async () => {
    const file = _file(20);
    const { rerender } = renderDiff({
      file,
      initiallyExpanded: false,
      forceExpandToken: 0,
    });
    expect(screen.getByRole('button', { name: /expand diff/i })).toBeInTheDocument();

    rerender(
      <DiffFileWithComments
        file={file}
        taskId="T1"
        repoId="repo-1"
        comments={[]}
        commentsLoading={false}
        commentsError=""
        onMutated={vi.fn()}
        onAddToChat={vi.fn()}
        initiallyExpanded={false}
        forceExpandToken={1}
      />,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /collapse diff/i })).toBeInTheDocument();
    });
  });

  test('clicking the toggle collapses an expanded diff', () => {
    const { container } = renderDiff({ file: _file(20), initiallyExpanded: true });

    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    fireEvent.click(container.querySelector('.diff-file-collapse-toggle'));
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /expand diff/i })).toBeInTheDocument();
  });

  test('initiallyExpanded omitted: falls back to per-file isLargeFile rule', () => {
    // No prop → uses the legacy per-file rule. A small file is
    // expanded by default; a too-large file is collapsed.
    const { rerender } = renderDiff({ file: _file(10) });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();

    rerender(
      <DiffFileWithComments
        file={_file(LARGE_FILE_LINE_THRESHOLD + 50)}
        taskId="T1"
        repoId="repo-1"
        comments={[]}
        commentsLoading={false}
        commentsError=""
        onMutated={vi.fn()}
        onAddToChat={vi.fn()}
      />,
    );
    // After re-mount with a huge file, the placeholder shows.
    // Note: rerender keeps the same instance, so the lazy init's
    // initial expanded state from the FIRST file persists. The
    // cleaner check below uses a fresh render.
  });

  test('huge file (>LARGE_FILE_LINE_THRESHOLD) auto-collapses even without initiallyExpanded prop', () => {
    const { container } = renderDiff({ file: _file(LARGE_FILE_LINE_THRESHOLD + 100) });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(container.querySelector('.diff')).not.toBeInTheDocument();
  });

  test('initiallyExpanded=true overrides the per-file large-file rule', () => {
    // Belt-and-braces: ChangesTab's cumulative budget might decide
    // to expand a moderately-large file (if it's the first one in
    // a list and budget is fresh). Per-file isLargeFile says no,
    // but the explicit prop wins.
    renderDiff({
      file: _file(LARGE_FILE_LINE_THRESHOLD + 100),
      initiallyExpanded: true,
    });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
  });

  test('initiallyExpanded=false overrides the per-file small-file rule', () => {
    // The cumulative budget can decide a small file should collapse
    // because earlier files exhausted the budget. Explicit false
    // wins over the per-file "small file → expand" default.
    const { container } = renderDiff({ file: _file(20), initiallyExpanded: false });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(container.querySelector('.diff-file-comments')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — header rendering', () => {

  test('renders the file path in the header', () => {
    const { container } = renderDiff({ file: _file(10, { path: 'src/auth/login.py' }) });
    const pathNode = container.querySelector('.diff-file-path');
    expect(pathNode).toHaveTextContent('src/auth/login.py');
    expect(container.querySelectorAll('.diff-file-path-separator')).toHaveLength(2);
  });

  test('clicking the header path asks the file tree to reveal that file', () => {
    const onFocusInTree = vi.fn();
    renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onFocusInTree,
    });

    fireEvent.click(screen.getByRole('button', { name: /src.*auth.*login.py/i }));
    expect(onFocusInTree).toHaveBeenCalledWith({
      repoId: 'repo-1',
      relativePath: 'src/auth/login.py',
    });
  });

  test('right-clicking the file header opens file actions', () => {
    const onFocusInTree = vi.fn();
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onFocusInTree,
    });

    fireEvent.contextMenu(container.querySelector('.diff-file-header'));

    expect(screen.getByRole('menuitem', { name: /show in tree/i }))
      .toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /place in chat/i }))
      .toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /copy relative path/i }))
      .toBeInTheDocument();
  });

  test('header context menu can reveal the file in the tree', () => {
    const onFocusInTree = vi.fn();
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onFocusInTree,
    });

    fireEvent.contextMenu(container.querySelector('.diff-file-header'));
    fireEvent.click(screen.getByRole('menuitem', { name: /show in tree/i }));

    expect(onFocusInTree).toHaveBeenCalledWith({
      repoId: 'repo-1',
      relativePath: 'src/auth/login.py',
    });
  });

  test('header context menu places the repo-prefixed path in chat', () => {
    const onAddToChat = vi.fn();
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onAddToChat,
    });

    fireEvent.contextMenu(container.querySelector('.diff-file-header'));
    fireEvent.click(screen.getByRole('menuitem', { name: /place in chat/i }));

    expect(onAddToChat).toHaveBeenCalledWith('`repo-1:src/auth/login.py`');
  });

  test('header context menu copies the repo-prefixed relative path', async () => {
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
    });

    fireEvent.contextMenu(container.querySelector('.diff-file-header'));
    fireEvent.click(screen.getByRole('menuitem', { name: /copy relative path/i }));

    await waitFor(() => {
      expect(navigator.clipboard.writeText)
        .toHaveBeenCalledWith('repo-1:src/auth/login.py');
    });
  });

  test('shows the diff type icon (modify / add / delete)', () => {
    const { container } = renderDiff({ file: _file(10, { type: 'add' }) });
    expect(container.querySelector('.diff-file-row-kind.kind-add'))
      .toBeInTheDocument();
  });

  test('deleted file header shows its real path, never "/dev/null"', () => {
    // react-diff-view sets the missing side to "/dev/null" for a
    // pure delete. The header must resolve to the OLD path via
    // diffDisplayPath, not render the literal "/dev/null".
    const file = _file(10, { type: 'delete', path: 'src/gone.py' });
    file.newPath = '/dev/null';
    const { container } = renderDiff({ file });
    const pathNode = container.querySelector('.diff-file-path');
    expect(pathNode).toHaveTextContent('src/gone.py');
    expect(pathNode).not.toHaveTextContent('/dev/null');
  });

  test('diff body lives in a .diff-file-body sibling of the sticky header', () => {
    // The card keeps overflow:visible for its sticky header, so the
    // rounded bottom is achieved by clipping this wrapper instead.
    // It must be a sibling AFTER the header (not inside it).
    const { container } = renderDiff({ file: _file(6), initiallyExpanded: true });
    const section = container.querySelector('.diff-file');
    const header = section.querySelector('.diff-file-header');
    const body = section.querySelector('.diff-file-body');
    expect(body).toBeInTheDocument();
    expect(header).toHaveClass('sticky-section-header');
    expect(header.contains(body)).toBe(false);
    expect(section.children[section.children.length - 1]).toBe(body);
    // The actual diff table renders inside the wrapper.
    expect(body.querySelector('.diff')).toBeInTheDocument();
  });

  test('renders the expand/collapse chevron on the left side of the header', () => {
    const { container } = renderDiff({ file: _file(10), initiallyExpanded: true });
    const header = container.querySelector('.diff-file-header');
    expect(header.firstElementChild).toHaveClass('diff-file-collapse-toggle');
    expect(header.firstElementChild).toHaveAttribute(
      'aria-label',
      expect.stringMatching(/collapse diff/i),
    );
  });

  test('merge conflict mark shows when conflicted prop is true', () => {
    renderDiff({ file: _file(10), conflicted: true });
    expect(screen.getByLabelText(/merge conflict/i)).toBeInTheDocument();
  });

  test('merge conflict mark is absent by default', () => {
    renderDiff({ file: _file(10) });
    expect(screen.queryByLabelText(/merge conflict/i)).not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — comment reopen', () => {
  test('reopening a resolved root comment wakes the chat stream when kato starts', async () => {
    apiMocks.reopenTaskComment.mockResolvedValue({
      ok: true,
      body: {
        triggered_immediately: true,
        comment: { id: 'c1', status: 'open', kato_status: 'in_progress' },
      },
    });
    const onCommentSpawned = vi.fn();
    const onMutated = vi.fn();

    renderDiff({
      initiallyExpanded: true,
      onCommentSpawned,
      onMutated,
      comments: [{
        id: 'c1',
        body: 'please revisit',
        line: -1,
        status: 'resolved',
        kato_status: 'addressed',
        source: 'local',
        author: 'operator',
        created_at_epoch: 1,
      }],
    });

    fireEvent.click(screen.getByRole('button', { name: /expand comment/i }));
    fireEvent.click(screen.getByRole('button', { name: /reopen/i }));

    await waitFor(() => {
      expect(apiMocks.reopenTaskComment).toHaveBeenCalledWith('T1', 'c1');
    });
    expect(onMutated).toHaveBeenCalled();
    expect(onCommentSpawned).toHaveBeenCalled();
  });
});


describe('DiffFileWithComments — syntax highlighting', () => {

  test('renders syntax token spans for added JavaScript files', () => {
    const rawDiff = [
      'diff --git a/helpers.js b/helpers.js',
      'new file mode 100644',
      '--- /dev/null',
      '+++ b/helpers.js',
      '@@ -0,0 +1,2 @@',
      '+export const TAG_INFO = {',
      "+  TWILIO: { colorKey: 'COLOR_SALMON' },",
      '',
    ].join('\n');
    const file = parseDiff(rawDiff)[0];
    const { container } = renderDiff({ file, initiallyExpanded: true });

    expect(container.querySelector('.token.keyword')).toBeInTheDocument();
    expect(container.querySelector('.token.string')).toBeInTheDocument();
  });
});

describe('DiffFileWithComments — collapsed context expansion', () => {

  test('renders gap controls and expands hidden lines from the base file', async () => {
    const rawDiff = [
      'diff --git a/src/promises.scss b/src/promises.scss',
      '--- a/src/promises.scss',
      '+++ b/src/promises.scss',
      '@@ -1,3 +1,3 @@',
      ' line 1',
      '-line 2',
      '+line 2 changed',
      ' line 3',
      '@@ -30,3 +30,3 @@',
      ' line 30',
      '-line 31',
      '+line 31 changed',
      ' line 32',
      '',
    ].join('\n');
    const file = parseDiff(rawDiff)[0];
    const sourceLines = new Array(40).fill(0).map((_, index) => {
      return `line ${index + 1}`;
    });
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: sourceLines.join('\n'),
      binary: false,
    });

    renderDiff({ file, initiallyExpanded: true });
    expect(screen.getByText(/26 hidden lines/i)).toBeInTheDocument();

    const expandBelow = screen.getByRole('button', {
      name: /show hidden lines below/i,
    });
    fireEvent.click(expandBelow);

    await waitFor(() => {
      expect(screen.getByText('line 29')).toBeInTheDocument();
    });
    expect(apiMocks.fetchBaseFileContent).toHaveBeenCalledWith(
      'T1',
      {
        repoId: 'repo-1',
        repoCwd: '/workspace/repo-1',
        path: 'src/promises.scss',
      },
    );
  });
});


describe('DiffFileWithComments — buried comment auto-reveal', () => {

  const gappedDiff = [
    'diff --git a/src/promises.scss b/src/promises.scss',
    '--- a/src/promises.scss',
    '+++ b/src/promises.scss',
    '@@ -1,3 +1,3 @@',
    ' line 1',
    '-line 2',
    '+line 2 changed',
    ' line 3',
    '@@ -30,3 +30,3 @@',
    ' line 30',
    '-line 31',
    '+line 31 changed',
    ' line 32',
    '',
  ].join('\n');

  function gapSource() {
    return new Array(40).fill(0).map((_, i) => `line ${i + 1}`).join('\n');
  }

  test('an open comment hidden in a gap is revealed with no manual click', async () => {
    const file = parseDiff(gappedDiff)[0];
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: gapSource(), binary: false,
    });

    renderDiff({
      file,
      initiallyExpanded: true,
      comments: [{
        id: 'c1', body: 'open comment in a gap', line: 15,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });

    // No expander was clicked — the thread shows up on its own,
    // and the line it is anchored to is now in the diff.
    await waitFor(() => {
      expect(screen.getByText('open comment in a gap')).toBeInTheDocument();
    });
    expect(screen.getByText('line 15')).toBeInTheDocument();
    expect(apiMocks.fetchBaseFileContent).toHaveBeenCalledWith('T1', {
      repoId: 'repo-1',
      repoCwd: '/workspace/repo-1',
      path: 'src/promises.scss',
    });
  });

  test('a resolved-only thread does NOT force the gap open', async () => {
    const file = parseDiff(gappedDiff)[0];
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: gapSource(), binary: false,
    });

    renderDiff({
      file,
      initiallyExpanded: true,
      comments: [{
        id: 'c1', body: 'resolved long ago', line: 15,
        parent_id: '', status: 'resolved',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });

    // Resolved threads must not auto-expand: the gap stays collapsed
    // and we never even fetch the base file for it.
    expect(screen.getByText(/26 hidden lines/i)).toBeInTheDocument();
    await Promise.resolve();
    expect(apiMocks.fetchBaseFileContent).not.toHaveBeenCalled();
    expect(screen.queryByText('resolved long ago')).not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — file-level comment shortcut', () => {

  test('clean file shows no entry button and no hint paragraph', () => {
    // The standalone "+ Add file-level comment" entry button and its
    // empty-state hint were removed on request — a clean file's diff
    // footer is now empty (no boilerplate under every file).
    renderDiff({ file: _file(10), comments: [] });
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/add a file-level comment/i))
      .not.toBeInTheDocument();
    expect(screen.queryByText(/click a diff line's gutter/i))
      .not.toBeInTheDocument();
  });

  test('the file-level entry button is gone even with comments present', () => {
    // No file-level-comment entry point from the diff view in any
    // state — the removal is unconditional, not just empty-state.
    renderDiff({
      file: _file(10),
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByText(/click a diff line's gutter/i))
      .not.toBeInTheDocument();
  });

  test('existing file-level threads still render (review comments preserved)', () => {
    // Removing the ENTRY point must not hide existing review
    // threads — they remain visible so the operator can still read
    // and reply to them.
    renderDiff({
      file: _file(10),
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.getByText('pre-existing thread')).toBeInTheDocument();
  });

  test('collapsed file still shows existing file-level threads', () => {
    renderDiff({
      file: _file(10),
      initiallyExpanded: false,
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.getByText('pre-existing thread')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
  });
});
