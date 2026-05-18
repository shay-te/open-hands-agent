// Tests for DiffPane — the centre-column diff viewer. It now renders
// EVERY changed file (all repos) stacked; the left list is pure
// navigation that scrolls this pane to a file. Heavy deps (the
// ChangesTab parser/path helpers, DiffFileWithComments, the
// chat-composer context, the API) are stubbed.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

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
    />
  ),
}));
vi.mock('../contexts/ChatComposerContext.jsx', () => ({
  useChatComposer: () => ({ appendToInput: vi.fn() }),
}));

import DiffPane, { findDiffFile, diffAnchorKey } from './DiffPane.jsx';
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


describe('diffAnchorKey', () => {
  test('joins repo + path; tolerates a missing repo', () => {
    expect(diffAnchorKey('client', 'src/App.jsx')).toBe('client::src/App.jsx');
    expect(diffAnchorKey('', 'a.js')).toBe('::a.js');
    expect(diffAnchorKey(undefined, 'a.js')).toBe('::a.js');
  });
});


describe('findDiffFile — still resolves a single file', () => {
  test('matches on display path within the selected repo', () => {
    const m = findDiffFile(_repoDiffs(), 'client', 'src/App.jsx');
    expect(m.repo.repo_id).toBe('client');
    expect(m.file.newPath).toBe('src/App.jsx');
  });

  test('an added file resolves via its /dev/null old side too', () => {
    const m = findDiffFile(_repoDiffs(), 'client', '/dev/null');
    expect(m.file.newPath).toBe('src/new.js');
  });

  test('selects the repo by id', () => {
    const m = findDiffFile(_repoDiffs(), 'backend', 'api/auth.py');
    expect(m.repo.repo_id).toBe('backend');
  });

  test('repo found, file absent → file null; empty input → null', () => {
    expect(findDiffFile(_repoDiffs(), 'client', 'nope').file).toBeNull();
    expect(findDiffFile([], 'client', 'x')).toBeNull();
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

  test('opens every diff file by default', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(<DiffPane openFile={_open()} />);
    const files = await screen.findAllByTestId('diff-file');
    expect(files.map((node) => node.getAttribute('data-initially-expanded')))
      .toEqual(['true', 'true', 'true']);
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
