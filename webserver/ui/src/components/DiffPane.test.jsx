// Tests for DiffPane — the centre-column diff viewer. It now renders
// EVERY changed file (all repos) stacked; the left list is pure
// navigation that scrolls this pane to a file. Heavy deps (the
// ChangesTab parser/path helpers, DiffFileWithComments, the
// chat-composer context, the API) are stubbed.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchDiff: vi.fn(),
  fetchTaskComments: vi.fn().mockResolvedValue({ ok: true, body: { comments: [] } }),
}));
vi.mock('../diffModel.js', () => ({
  parseRepoDiffs: vi.fn(),
  diffFileKey: (f) => `${f.type}:${f.oldPath || ''}->${f.newPath || ''}`,
  diffDisplayPath: (f) => {
    const real = (p) => (p && p !== '/dev/null' ? p : '');
    if (f.type === 'delete') { return real(f.oldPath) || real(f.newPath) || '(unknown)'; }
    return real(f.newPath) || real(f.oldPath) || '(unknown)';
  },
  // Pass files through in their original order — ordering is tested separately.
  buildDiffFileTree: (files) => ({
    nodes: (files || []).map((f) => ({ kind: 'file', file: f })),
    stats: { added: 0, deleted: 0 },
  }),
  isFileConflicted: (f, set) => {
    if (!set || set.size === 0) { return false; }
    return set.has(f.oldPath || '') || set.has(f.newPath || '');
  },
}));
vi.mock('./DiffFileWithComments.jsx', () => ({
  default: (props) => (
    <div
      data-testid="diff-file"
      data-path={props.file?.newPath || props.file?.oldPath}
      data-repo={props.repoId}
      data-initially-expanded={String(props.initiallyExpanded)}
      data-force-expand-token={String(props.forceExpandToken || 0)}
      data-conflicted={String(!!props.conflicted)}
      data-comments={String((props.comments || []).length)}
    >
      {(props.comments || []).length > 0 && (
        <article className="diff-file-comment-thread" data-testid="comment-thread" />
      )}
      <button
        type="button"
        onClick={() => props.onFocusInTree({
          repoId: props.repoId,
          relativePath: props.file?.newPath || props.file?.oldPath,
        })}
      >
        focus tree
      </button>
      <button type="button" onClick={() => props.onMutated()}>
        mutate comments
      </button>
    </div>
  ),
}));
vi.mock('../contexts/ChatComposerContext.jsx', () => ({
  useChatComposer: () => ({ appendToInput: vi.fn() }),
}));

import DiffPane, { diffAnchorKey } from './DiffPane.jsx';
import { fetchDiff, fetchTaskComments } from '../api.js';
import { parseRepoDiffs } from '../diffModel.js';


function _repoDiffs() {
  return [
    {
      repo_id: 'client',
      cwd: '/w/client',
      conflictedFiles: new Set(),
      files: [
        { type: 'modify', newPath: 'src/App.jsx', oldPath: 'src/App.jsx', hunks: [] },
        { type: 'add', newPath: 'src/new.js', oldPath: '/dev/null', hunks: [] },
      ],
    },
    {
      repo_id: 'backend',
      cwd: '/w/backend',
      conflictedFiles: new Set(['api/auth.py']),
      files: [{ type: 'modify', newPath: 'api/auth.py', oldPath: 'api/auth.py', hunks: [] }],
    },
  ];
}

const _open = (over = {}) => ({
  taskId: 'T1',
  absolutePath: '/w/client/src/App.jsx',
  relativePath: 'src/App.jsx',
  repoId: 'client',
  view: 'diff',
  ...over,
});

// A repo whose files exercise BOTH auto-collapse rules (real
// decideAutoExpand runs — only diffModel is mocked). ``n`` diff lines =
// a hunk with n changes. Budget is 2000 cumulative, large-file is >500.
function _budgetRepoDiffs() {
  const lines = (n) => ({ hunks: [{ changes: new Array(n).fill({ type: 'normal' }) }] });
  const f = (name, n) => ({ type: 'modify', newPath: name, oldPath: name, ...lines(n) });
  return [{
    repo_id: 'client',
    cwd: '/w/client',
    conflictedFiles: new Set(),
    files: [
      f('a.js', 500),   // cum 500  -> expand
      f('b.js', 500),   // cum 1000 -> expand
      f('big.js', 900), // > 500    -> collapse (large), budget untouched
      f('c.js', 500),   // cum 1500 -> expand
      f('d.js', 500),   // cum 2000 -> expand
      f('e.js', 500),   // cum 2500 -> collapse (over budget)
    ],
  }];
}

const _byPath = (nodes) => Object.fromEntries(
  nodes.map((n) => [n.getAttribute('data-path'), n]),
);


describe('diffAnchorKey', () => {
  test('joins repo + path; tolerates a missing repo', () => {
    expect(diffAnchorKey('client', 'src/App.jsx')).toBe('client::src/App.jsx');
    expect(diffAnchorKey('', 'a.js')).toBe('::a.js');
    expect(diffAnchorKey(undefined, 'a.js')).toBe('::a.js');
  });
});


describe('DiffPane — renders ALL files, scrolls to the target', () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    fetchDiff.mockReset();
    parseRepoDiffs.mockReset();
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
  });

  test('loading state while the diff fetch is in flight', () => {
    fetchDiff.mockReturnValue(new Promise(() => {}));
    render(<DiffPane openFile={_open()} />);
    expect(screen.getByText(/computing diff/i)).toBeInTheDocument();
  });

  test('renders every changed file across every repo', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(<DiffPane openFile={_open()} />);
    const files = await screen.findAllByTestId('diff-file');
    // 2 (client) + 1 (backend) = 3 — the WHOLE changeset, not one file.
    expect(files).toHaveLength(3);
    expect(fetchDiff).toHaveBeenCalledWith('T1');  // no repoId filter
  });

  test('refetches the diff when the workspace version changes', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { rerender } = render(
      <DiffPane openFile={_open()} workspaceVersion={1} />,
    );
    await screen.findAllByTestId('diff-file');
    expect(fetchDiff).toHaveBeenCalledTimes(1);

    rerender(<DiffPane openFile={_open()} workspaceVersion={2} />);
    await waitFor(() => {
      expect(fetchDiff).toHaveBeenCalledTimes(2);
    });
  });

  test('opens every (small) diff file by default', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(<DiffPane openFile={_open()} />);
    const files = await screen.findAllByTestId('diff-file');
    expect(files.map((node) => node.getAttribute('data-initially-expanded')))
      .toEqual(['true', 'true', 'true']);
  });

  test('auto-collapses large files and files past the cumulative line budget', async () => {
    // The fix: stop force-expanding every file. Large (>500-line) files
    // and everything past the 2000-line running budget start collapsed,
    // so a big PR no longer mounts + tokenizes the whole changeset on open.
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_budgetRepoDiffs());
    // Target a file that is NOT in the changeset so nothing is force-expanded.
    render(<DiffPane openFile={_open({ relativePath: '(none)', repoId: 'client' })} />);
    const byPath = _byPath(await screen.findAllByTestId('diff-file'));
    const expanded = (name) => byPath[name].getAttribute('data-initially-expanded');
    expect(expanded('a.js')).toBe('true');    // cum 500
    expect(expanded('b.js')).toBe('true');    // cum 1000
    expect(expanded('big.js')).toBe('false'); // > 500 lines -> collapsed
    expect(expanded('c.js')).toBe('true');    // cum 1500
    expect(expanded('d.js')).toBe('true');    // cum 2000
    expect(expanded('e.js')).toBe('false');   // over budget -> collapsed
  });

  test('the clicked target file stays expanded even when the budget would collapse it', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_budgetRepoDiffs());
    // e.js is over budget (would collapse) but it is the file the operator opened.
    render(<DiffPane openFile={_open({ relativePath: 'e.js', repoId: 'client', openRequestId: 7 })} />);
    const byPath = _byPath(await screen.findAllByTestId('diff-file'));
    expect(byPath['e.js'].getAttribute('data-initially-expanded')).toBe('true');
    expect(byPath['e.js'].getAttribute('data-force-expand-token')).toBe('7');
    // A different over-limit file the operator did NOT open stays collapsed.
    expect(byPath['big.js'].getAttribute('data-initially-expanded')).toBe('false');
    expect(byPath['big.js'].getAttribute('data-force-expand-token')).toBe('0');
  });

  test('scrolls the targeted file section into view', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { container } = render(
      <DiffPane openFile={_open({ relativePath: 'api/auth.py', repoId: 'backend' })} />,
    );
    await screen.findAllByTestId('diff-file');
    await waitFor(() => {
      const target = container.querySelector('[data-diff-key="backend::api/auth.py"]');
      expect(target).toBeInTheDocument();
      expect(target.scrollIntoView).toHaveBeenCalled();
    });
  });

  test('does NOT re-scroll on a background diff refresh', async () => {
    // Regression (operator bug): a background diff refresh re-fired the
    // scroll-to-file effect and yanked the operator away mid-read. The
    // scroll must fire once per OPEN request, never on a refresh — even
    // one that changes the file count (which re-runs the effect).
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const open = _open({ relativePath: 'api/auth.py', repoId: 'backend' });
    const { container, rerender } = render(
      <DiffPane openFile={open} workspaceVersion={1} />,
    );
    const target = await waitFor(() => {
      const t = container.querySelector('[data-diff-key="backend::api/auth.py"]');
      expect(t.scrollIntoView).toHaveBeenCalledTimes(1);
      return t;
    });

    // Refresh with a CHANGED file count so the scroll effect re-runs…
    parseRepoDiffs.mockReturnValue([
      ..._repoDiffs(),
      {
        repo_id: 'extra',
        cwd: '/w/extra',
        conflictedFiles: new Set(),
        files: [{ type: 'add', newPath: 'x.js', oldPath: '/dev/null', hunks: [] }],
      },
    ]);
    rerender(<DiffPane openFile={open} workspaceVersion={2} />);
    await waitFor(() => expect(fetchDiff).toHaveBeenCalledTimes(2));
    // …the SAME open request must NOT scroll again.
    expect(target.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  test('focusComment scrolls to the file\'s first comment thread', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    fetchTaskComments.mockImplementation((_taskId, rid) => Promise.resolve(
      rid === 'backend'
        ? { ok: true, body: { comments: [{ id: 'c1', file_path: 'api/auth.py' }] } }
        : { ok: true, body: { comments: [] } },
    ));
    const { container } = render(
      <DiffPane
        openFile={_open({
          relativePath: 'api/auth.py', repoId: 'backend', focusComment: true,
        })}
      />,
    );
    await screen.findAllByTestId('diff-file');
    await waitFor(() => {
      const thread = container.querySelector(
        '[data-diff-key="backend::api/auth.py"] .diff-file-comment-thread',
      );
      expect(thread).toBeInTheDocument();
      expect(thread.scrollIntoView).toHaveBeenCalled();
    });
  });

  test('does NOT re-scroll to the thread when a later comments poll changes data (same open request)', async () => {
    // Regression: the focusComment effect depends on commentsByRepo (the
    // thread only exists once comments load), and a poll that picked up a
    // new comment / status flip re-fired it and yanked the pane back to
    // the thread mid-read. It must centre the thread once per open
    // request, not on every comments refresh.
    const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    fetchDiff.mockResolvedValue({ diffs: [] });
    // Fresh array per call so a refetch changes state.repoDiffs identity
    // and the comments effect actually re-runs (a real poll).
    parseRepoDiffs.mockImplementation(() => _repoDiffs());
    fetchTaskComments.mockImplementation((_taskId, rid) => Promise.resolve(
      rid === 'backend'
        ? { ok: true, body: { comments: [{ id: 'c1', file_path: 'api/auth.py' }] } }
        : { ok: true, body: { comments: [] } },
    ));
    const open = _open({ relativePath: 'api/auth.py', repoId: 'backend', focusComment: true });
    const { container, rerender } = render(<DiffPane openFile={open} workspaceVersion={1} />);
    const fileNode = () => container.querySelector('[data-diff-key="backend::api/auth.py"]');
    // data-comments lives on the inner (mocked) diff-file element.
    const commentCount = () => fileNode()
      ?.querySelector('[data-testid="diff-file"]')
      ?.getAttribute('data-comments');
    await waitFor(() => {
      const thread = fileNode().querySelector('.diff-file-comment-thread');
      expect(thread).toBeInTheDocument();
      expect(thread.scrollIntoView).toHaveBeenCalled();
    });
    window.HTMLElement.prototype.scrollIntoView.mockClear();

    // Poll brings a SECOND comment (count 1→2, observable) → commentsByRepo
    // re-builds with a new identity and the focusComment effect re-fires;
    // the requestId guard must keep the pane where the operator left it.
    fetchTaskComments.mockImplementation((_taskId, rid) => Promise.resolve(
      rid === 'backend'
        ? { ok: true, body: { comments: [
            { id: 'c1', file_path: 'api/auth.py' },
            { id: 'c2', file_path: 'api/auth.py' },
          ] } }
        : { ok: true, body: { comments: [] } },
    ));
    rerender(<DiffPane openFile={open} workspaceVersion={2} />);
    await waitFor(() => expect(commentCount()).toBe('2'));
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(window.HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
    window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  });

  test('passes the open request token only to the targeted diff file', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { container } = render(
      <DiffPane
        openFile={_open({
          relativePath: 'api/auth.py',
          repoId: 'backend',
          openRequestId: 7,
        })}
      />,
    );
    await screen.findAllByTestId('diff-file');
    const target = container.querySelector('[data-diff-key="backend::api/auth.py"] [data-testid="diff-file"]');
    const other = container.querySelector('[data-diff-key="client::src/App.jsx"] [data-testid="diff-file"]');
    expect(target.getAttribute('data-force-expand-token')).toBe('7');
    expect(other.getAttribute('data-force-expand-token')).toBe('0');
  });

  test('conflicted file gets the conflicted flag', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { container } = render(<DiffPane openFile={_open()} />);
    await screen.findAllByTestId('diff-file');
    const conflicted = container.querySelector(
      '[data-diff-key="backend::api/auth.py"] [data-testid="diff-file"]',
    );
    expect(conflicted.getAttribute('data-conflicted')).toBe('true');
  });

  test('passes file-tree focus requests from file headers to the parent', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const onFocusFileInTree = vi.fn();
    render(
      <DiffPane openFile={_open()} onFocusFileInTree={onFocusFileInTree} />,
    );
    const buttons = await screen.findAllByRole('button', { name: /focus tree/i });
    fireEvent.click(buttons[2]);
    expect(onFocusFileInTree).toHaveBeenCalledWith({
      repoId: 'backend',
      relativePath: 'api/auth.py',
    });
  });

  test('comment mutations ask the parent to refresh tree comment badges', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const onCommentsChanged = vi.fn();
    render(
      <DiffPane openFile={_open()} onCommentsChanged={onCommentsChanged} />,
    );
    const buttons = await screen.findAllByRole('button', { name: /mutate comments/i });
    fireEvent.click(buttons[0]);
    expect(onCommentsChanged).toHaveBeenCalledTimes(1);
  });

  test('empty changeset → "No changes on this task branch."', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue([]);
    render(<DiffPane openFile={_open()} />);
    await waitFor(() => {
      expect(screen.getByText(/no changes on this task branch/i))
        .toBeInTheDocument();
    });
  });

  test('fetch failure surfaces the error', async () => {
    fetchDiff.mockRejectedValue(new Error('boom'));
    render(<DiffPane openFile={_open()} />);
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });

  test('no bound task → error, no fetch', () => {
    render(<DiffPane openFile={_open({ taskId: '' })} />);
    expect(screen.getByText(/no task bound/i)).toBeInTheDocument();
    expect(fetchDiff).not.toHaveBeenCalled();
  });
});
