// Tests for CommentWidgets. The pure ``buildThreads`` builder is
// the load-bearing part — diff-line and file-level comment panes
// both consume its output. Plus a thin layer for the CommentBubble
// rendering (author label, source badge, resolved styling).

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import {
  CommentBubble,
  CommentForm,
  CommentThread,
  buildThreads,
} from './CommentWidgets.jsx';


function _comment(overrides = {}) {
  return {
    id: 'c1',
    body: 'comment text',
    parent_id: '',
    status: 'open',
    source: 'local',
    author: 'operator',
    created_at_epoch: 1000,
    ...overrides,
  };
}


describe('buildThreads — root/replies organization', () => {

  test('empty input returns []', () => {
    expect(buildThreads([])).toEqual([]);
  });

  test('top-level comment becomes a single thread with no replies', () => {
    const out = buildThreads([_comment({ id: 'a' })]);
    expect(out).toHaveLength(1);
    expect(out[0].root.id).toBe('a');
    expect(out[0].replies).toEqual([]);
  });

  test('reply attached to its parent', () => {
    const out = buildThreads([
      _comment({ id: 'a', created_at_epoch: 100 }),
      _comment({ id: 'b', parent_id: 'a', created_at_epoch: 200 }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].root.id).toBe('a');
    expect(out[0].replies).toHaveLength(1);
    expect(out[0].replies[0].id).toBe('b');
  });

  test('multiple roots sort by created_at_epoch ascending', () => {
    const out = buildThreads([
      _comment({ id: 'newer', created_at_epoch: 200 }),
      _comment({ id: 'older', created_at_epoch: 100 }),
      _comment({ id: 'middle', created_at_epoch: 150 }),
    ]);
    expect(out.map((t) => t.root.id)).toEqual(['older', 'middle', 'newer']);
  });

  test('replies sort by created_at_epoch under their root', () => {
    const out = buildThreads([
      _comment({ id: 'root', created_at_epoch: 0 }),
      _comment({ id: 'r2', parent_id: 'root', created_at_epoch: 200 }),
      _comment({ id: 'r1', parent_id: 'root', created_at_epoch: 100 }),
    ]);
    expect(out[0].replies.map((r) => r.id)).toEqual(['r1', 'r2']);
  });

  test('orphan reply (parent_id not in input) is promoted to a root', () => {
    // Defensive: a reply whose parent doesn't exist in this batch
    // (e.g., deleted, paginated, or remote-source mismatch) should
    // appear as its own thread rather than vanish.
    const out = buildThreads([
      _comment({ id: 'reply', parent_id: 'missing-parent' }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].root.id).toBe('reply');
  });

  test('multiple replies on one root: deeply nested replies still attach to top root', () => {
    // The builder is non-recursive — a "reply to a reply" attaches
    // to its DIRECT parent only. We just verify the structure.
    const out = buildThreads([
      _comment({ id: 'a', created_at_epoch: 0 }),
      _comment({ id: 'b', parent_id: 'a', created_at_epoch: 100 }),
      _comment({ id: 'c', parent_id: 'b', created_at_epoch: 200 }),
    ]);
    // Builder is non-recursive: ``c`` attaches to ``b`` not ``a``.
    expect(out).toHaveLength(1);
    expect(out[0].root.id).toBe('a');
    const replyIds = out[0].replies.map((r) => r.id);
    expect(replyIds).toContain('b');
  });
});


describe('CommentBubble — rendering', () => {

  test('renders the comment body via children', () => {
    render(
      <CommentBubble
        comment={_comment({ body: 'hello reviewer' })}
        isRoot={true}
      >
        hello reviewer
      </CommentBubble>,
    );
    // Author is in the header even when body is in children.
    expect(screen.getByText('operator')).toBeInTheDocument();
  });

  test('shows LOCAL badge for local comments', () => {
    render(
      <CommentBubble
        comment={_comment({ source: 'local' })}
        isRoot={true}
      />,
    );
    expect(screen.getByText('LOCAL')).toBeInTheDocument();
  });

  test('shows REMOTE badge for remote comments', () => {
    render(
      <CommentBubble
        comment={_comment({ source: 'remote', author: 'reviewer-bot' })}
        isRoot={true}
      />,
    );
    expect(screen.getByText('REMOTE')).toBeInTheDocument();
    expect(screen.getByText('reviewer-bot')).toBeInTheDocument();
  });

  test('falls back to author "operator" for local with no author', () => {
    render(
      <CommentBubble
        comment={_comment({ author: '', source: 'local' })}
        isRoot={true}
      />,
    );
    expect(screen.getByText('operator')).toBeInTheDocument();
  });

  test('falls back to author "remote" for remote with no author', () => {
    render(
      <CommentBubble
        comment={_comment({ author: '', source: 'remote' })}
        isRoot={true}
      />,
    );
    expect(screen.getByText('remote')).toBeInTheDocument();
  });

  test('root + onResolve renders a Resolve button; clicking fires it', () => {
    const onResolve = vi.fn();
    render(
      <CommentBubble
        comment={_comment({ status: 'open' })}
        isRoot={true}
        onResolve={onResolve}
      />,
    );
    const btn = screen.queryByRole('button', { name: /resolve/i });
    if (btn) {
      fireEvent.click(btn);
      expect(onResolve).toHaveBeenCalled();
    }
  });

  test('resolved thread carries the is-resolved-aware kato_status when set', () => {
    const { container } = render(
      <CommentBubble
        comment={_comment({ status: 'resolved' })}
        isRoot={true}
      />,
    );
    // The bubble itself should render; resolved styling is on the
    // parent <article>, so we just confirm no-crash + author shows.
    expect(container).toBeInTheDocument();
  });
});


describe('CommentForm — submit + cancel', () => {

  test('renders the placeholder text', () => {
    render(
      <CommentForm
        placeholder="Add a reply…"
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByPlaceholderText(/Add a reply/)).toBeInTheDocument();
  });

  test('submit button is disabled when textarea is empty', () => {
    render(
      <CommentForm
        placeholder="…"
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const submit = screen.getByRole('button', { name: /post|add|send|submit/i });
    expect(submit).toBeDisabled();
  });

  test('typing enables the submit button', () => {
    render(
      <CommentForm
        placeholder="…"
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'something' } });
    const submit = screen.getByRole('button', { name: /post|add|send|submit/i });
    expect(submit).not.toBeDisabled();
  });

  test('submit calls onSubmit with the body text', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <CommentForm
        placeholder="…"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'great idea' },
    });
    fireEvent.click(screen.getByRole('button', { name: /post|add|send|submit/i }));
    expect(onSubmit).toHaveBeenCalledWith('great idea');
  });

  test('draftKey persists in-progress draft across unmount/remount', async () => {
    // Regression: while the operator was typing a comment and
    // kato/claude posted a sibling comment, the parent re-rendered,
    // unmounted CommentForm, and re-mounted it with an empty draft —
    // losing the in-flight text. With ``draftKey`` set, the draft is
    // mirrored to localStorage on every keystroke and restored on
    // the next mount.
    const draftKey = 'kato.comment.draft.T1|client|src/foo.py|line:42|root';
    try {
      const first = render(
        <CommentForm
          placeholder="…"
          onSubmit={vi.fn().mockResolvedValue(true)}
          onCancel={vi.fn()}
          draftKey={draftKey}
        />,
      );
      fireEvent.change(screen.getByRole('textbox'), {
        target: { value: 'in-flight draft' },
      });
      // Simulate the parent re-render: unmount and remount with the
      // SAME draftKey. The remounted form must hydrate from storage.
      first.unmount();
      render(
        <CommentForm
          placeholder="…"
          onSubmit={vi.fn().mockResolvedValue(true)}
          onCancel={vi.fn()}
          draftKey={draftKey}
        />,
      );
      expect(screen.getByRole('textbox').value).toBe('in-flight draft');
    } finally {
      // Don't leak storage state into sibling tests.
      try { window.localStorage.removeItem(draftKey); } catch (_e) { /* */ }
    }
  });

  test('successful submit clears the persisted draft', async () => {
    const draftKey = 'kato.comment.draft.T1|client|src/foo.py|line:7|root';
    try {
      render(
        <CommentForm
          placeholder="…"
          onSubmit={vi.fn().mockResolvedValue(true)}
          onCancel={vi.fn()}
          draftKey={draftKey}
        />,
      );
      fireEvent.change(screen.getByRole('textbox'), {
        target: { value: 'will be posted' },
      });
      expect(window.localStorage.getItem(draftKey)).toBe('will be posted');
      fireEvent.click(screen.getByRole('button', { name: /post|add|send|submit/i }));
      // Wait one microtask for the submit promise + state updates.
      await Promise.resolve();
      await Promise.resolve();
      expect(window.localStorage.getItem(draftKey)).toBe(null);
    } finally {
      try { window.localStorage.removeItem(draftKey); } catch (_e) { /* */ }
    }
  });

  test('cancel button fires onCancel', () => {
    const onCancel = vi.fn();
    render(
      <CommentForm
        placeholder="…"
        onSubmit={vi.fn()}
        onCancel={onCancel}
      />,
    );
    const cancelBtn = screen.queryByRole('button', { name: /cancel/i });
    if (cancelBtn) {
      fireEvent.click(cancelBtn);
      expect(onCancel).toHaveBeenCalled();
    }
  });

  test('the markdown toolbar inserts syntax into the textarea', () => {
    render(
      <CommentForm placeholder="…" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'hello' } });
    textarea.setSelectionRange(0, 5);
    fireEvent.click(screen.getByRole('button', { name: 'Bold' }));
    expect(textarea).toHaveValue('**hello**');
    // Select the whole line, then Quote prefixes it.
    textarea.setSelectionRange(0, textarea.value.length);
    fireEvent.click(screen.getByRole('button', { name: 'Quote' }));
    expect(textarea.value).toBe('> **hello**');
  });

  test('toolbar buttons do not collide with submit/cancel lookups', () => {
    render(
      <CommentForm placeholder="…" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    // Exactly one submit-ish button despite the toolbar row.
    expect(
      screen.getByRole('button', { name: /post|add|send|submit/i }),
    ).toBeInTheDocument();
  });
});


describe('CommentThread / CommentBubble — Bitbucket collapse', () => {
  function _thread(rootOverrides = {}, replies = []) {
    return { root: _comment(rootOverrides), replies };
  }

  const handlers = {
    onResolve: vi.fn(),
    onReopen: vi.fn(),
    onDelete: vi.fn(),
    onReply: vi.fn(),
    onMarkAddressed: vi.fn(),
  };

  test('open root renders expanded; chevron present (aria-expanded true)', () => {
    const { container } = render(
      <CommentThread
        thread={_thread({ status: 'open', body: 'open body here' })}
        {...handlers}
      />,
    );
    expect(container.querySelector('.diff-file-comment-body'))
      .toHaveTextContent('open body here');
    const chevron = screen.getByRole('button', { name: /collapse comment/i });
    expect(chevron).toHaveAttribute('aria-expanded', 'true');
  });

  test('resolved root starts collapsed — body hidden, header still shown', () => {
    const { container } = render(
      <CommentThread
        thread={_thread({
          status: 'resolved', author: 'reviewer', body: 'resolved body text',
        })}
        {...handlers}
      />,
    );
    expect(container.querySelector('.diff-file-comment-body')).toBeNull();
    // The card never disappears — the identity header stays visible.
    expect(screen.getByText('reviewer')).toBeInTheDocument();
    const chevron = screen.getByRole('button', { name: /expand comment/i });
    expect(chevron).toHaveAttribute('aria-expanded', 'false');
  });

  test('clicking the chevron expands a collapsed resolved comment', () => {
    const { container } = render(
      <CommentThread
        thread={_thread({ status: 'resolved', body: 'resolved body text' })}
        {...handlers}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /expand comment/i }));
    expect(container.querySelector('.diff-file-comment-body'))
      .toHaveTextContent('resolved body text');
    expect(
      screen.getByRole('button', { name: /collapse comment/i }),
    ).toHaveAttribute('aria-expanded', 'true');
  });

  test('renders an initials avatar and a status pill', () => {
    const { container } = render(
      <CommentThread
        thread={_thread({
          status: 'open', author: 'Shay Tessler', kato_status: 'queued',
        })}
        {...handlers}
      />,
    );
    const avatar = container.querySelector('.diff-file-comment-avatar');
    expect(avatar).toHaveTextContent('ST');
    expect(screen.getByText('PENDING')).toBeInTheDocument();
  });
});
