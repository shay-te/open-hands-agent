import { useEffect, useMemo, useState } from 'react';
import {
  createTaskComment,
  deleteTaskComment,
  fetchTaskComments,
  reopenTaskComment,
  resolveTaskComment,
} from '../api.js';
import { toast } from '../stores/toastStore.js';
import { formatRelativeTime } from '../utils/relativeTime.js';

// Per-file comment thread on the Changes tab. Lists every
// existing local + remote-synced comment anchored to this file
// (file-level only for now — per-line gutter widgets come next),
// renders threaded replies, and exposes "+ Add comment" plus
// resolve / reopen / delete actions inline.
//
// Local comments are typed in this widget. Remote comments are
// pulled in via the per-repo "Sync remote comments" button on
// the diff repo header. Each comment carries a source badge
// (LOCAL / REMOTE) so the operator can see at a glance where it
// came from.
export default function DiffFileComments({
  taskId,
  repoId,
  filePath,
  refreshTick = 0,
  onCommentSpawned,
}) {
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [replyTo, setReplyTo] = useState('');
  const [submitting, setSubmitting] = useState(false);
  // Local tick we bump on any successful mutation so re-fetch
  // is immediate (rather than waiting for the parent's next
  // workspaceVersion poll).
  const [localTick, setLocalTick] = useState(0);

  useEffect(() => {
    if (!taskId || !repoId) { return undefined; }
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchTaskComments(taskId, repoId).then((result) => {
      if (cancelled) { return; }
      if (!result.ok) {
        setError(String(result.error || 'failed to load comments'));
        setComments([]);
        return;
      }
      const list = Array.isArray(result.body?.comments)
        ? result.body.comments
        : [];
      setComments(list.filter((c) => c.file_path === filePath));
    }).finally(() => {
      if (!cancelled) { setLoading(false); }
    });
    return () => { cancelled = true; };
  }, [taskId, repoId, filePath, refreshTick, localTick]);

  // Build the thread tree: top-of-thread first, replies under
  // each. Bitbucket-style flattened render — operator scans top
  // to bottom without having to manually expand chains.
  const threads = useMemo(() => buildThreads(comments), [comments]);

  async function onSubmit() {
    if (submitting) { return; }
    const body = draft.trim();
    if (!body) { return; }
    setSubmitting(true);
    const result = await createTaskComment(taskId, {
      repo: repoId,
      file_path: filePath,
      line: -1,
      body,
      parent_id: replyTo,
    });
    setSubmitting(false);
    if (!result.ok) {
      toast.show({
        kind: 'error',
        title: 'Could not add comment',
        message: (result.body && result.body.error) || result.error || 'add failed',
        durationMs: 8000,
      });
      return;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment added',
      message: replyTo
        ? '✓ reply posted (kato runs only on top-of-thread comments)'
        : (triggered
          ? '✓ kato is working on this comment now'
          : '✓ queued — kato will pick it up when the live agent goes idle'),
      durationMs: 5000,
    });
    setDraft('');
    setReplyTo('');
    setLocalTick((n) => n + 1);
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }

  async function onResolve(commentId) {
    const result = await resolveTaskComment(taskId, commentId);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Resolve failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    setLocalTick((n) => n + 1);
  }

  async function onReopen(commentId) {
    const result = await reopenTaskComment(taskId, commentId);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Reopen failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment reopened',
      message: triggered
        ? '✓ kato is working on this comment now'
        : '✓ queued — kato will pick it up when the live agent goes idle',
      durationMs: 5000,
    });
    setLocalTick((n) => n + 1);
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }

  async function onDelete(commentId) {
    if (!window.confirm('Delete this comment? Replies will be removed too.')) {
      return;
    }
    const result = await deleteTaskComment(taskId, commentId);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Delete failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    setLocalTick((n) => n + 1);
  }

  if (!taskId || !repoId) { return null; }

  return (
    <div className="diff-file-comments">
      {loading && (
        <p className="diff-file-comments-empty">Loading comments…</p>
      )}
      {!loading && error && (
        <p className="diff-file-comments-empty error">{error}</p>
      )}
      {!loading && !error && threads.length === 0 && (
        <p className="diff-file-comments-empty">
          No comments on this file yet. Type below to add one — kato
          will pick it up immediately if the agent is idle, or queue
          it if a turn is in flight.
        </p>
      )}
      {!loading && !error && threads.map((thread) => (
        <CommentThread
          key={thread.root.id}
          thread={thread}
          onResolve={onResolve}
          onReopen={onReopen}
          onDelete={onDelete}
          onReply={(parentId) => {
            setReplyTo(parentId);
            // Focus the textarea on next paint via a small DOM
            // hint — auto-focus prop on the textarea below
            // honours ``replyTo`` changes.
          }}
        />
      ))}
      <div className="diff-file-comments-form">
        {replyTo && (
          <div className="diff-file-comments-reply-hint">
            Replying to a comment ·{' '}
            <button
              type="button"
              className="diff-file-comments-cancel-reply"
              onClick={() => setReplyTo('')}
            >
              cancel reply
            </button>
          </div>
        )}
        <textarea
          className="diff-file-comments-textarea"
          placeholder={
            replyTo
              ? 'Add a reply… (Cmd+Enter / Ctrl+Enter to submit)'
              : 'Add a comment for kato to address… (Cmd+Enter / Ctrl+Enter to submit)'
          }
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault();
              onSubmit();
            }
          }}
          rows={3}
        />
        <div className="diff-file-comments-form-actions">
          <button
            type="button"
            className="diff-file-comments-submit"
            onClick={onSubmit}
            disabled={submitting || !draft.trim()}
          >
            {submitting ? 'Submitting…' : (replyTo ? 'Reply' : 'Add comment')}
          </button>
        </div>
      </div>
    </div>
  );
}


function buildThreads(comments) {
  const byId = new Map();
  for (const comment of comments) {
    byId.set(comment.id, { ...comment, replies: [] });
  }
  const roots = [];
  for (const comment of byId.values()) {
    if (comment.parent_id && byId.has(comment.parent_id)) {
      byId.get(comment.parent_id).replies.push(comment);
    } else {
      roots.push(comment);
    }
  }
  // Stable order: oldest top-of-thread first, replies oldest
  // first within each thread.
  roots.sort((a, b) => (a.created_at_epoch || 0) - (b.created_at_epoch || 0));
  for (const root of roots) {
    root.replies.sort((a, b) => (a.created_at_epoch || 0) - (b.created_at_epoch || 0));
  }
  return roots.map((root) => ({ root, replies: root.replies }));
}


function CommentThread({ thread, onResolve, onReopen, onDelete, onReply }) {
  const { root, replies } = thread;
  const isResolved = root.status === 'resolved';
  return (
    <article className={[
      'diff-file-comment-thread',
      isResolved ? 'is-resolved' : '',
    ].filter(Boolean).join(' ')}>
      {isResolved && (
        <header className="diff-file-comment-resolved-banner">
          <span>
            ✓ {root.resolved_by || 'operator'} resolved this thread
            {root.resolved_at_epoch ? (
              <> · {formatRelativeTime((Date.now() / 1000) - root.resolved_at_epoch)}</>
            ) : null}
          </span>
        </header>
      )}
      <CommentBubble
        comment={root}
        isRoot
        onResolve={() => onResolve(root.id)}
        onReopen={() => onReopen(root.id)}
        onDelete={() => onDelete(root.id)}
        onReply={() => onReply(root.id)}
      />
      {replies.map((reply) => (
        <CommentBubble
          key={reply.id}
          comment={reply}
          isRoot={false}
          onDelete={() => onDelete(reply.id)}
          onReply={() => onReply(root.id)}
        />
      ))}
    </article>
  );
}


function CommentBubble({
  comment, isRoot,
  onResolve, onReopen, onDelete, onReply,
}) {
  const sourceLabel = comment.source === 'remote' ? 'REMOTE' : 'LOCAL';
  const sourceTitle = comment.source === 'remote'
    ? 'Pulled from the source git platform (Bitbucket / GitHub PR review).'
    : 'Local kato comment — kato runs on this immediately if idle.';
  const ago = comment.created_at_epoch
    ? formatRelativeTime((Date.now() / 1000) - comment.created_at_epoch)
    : '';
  const author = comment.author || (comment.source === 'remote' ? 'remote' : 'operator');
  const isResolved = comment.status === 'resolved';

  return (
    <div
      className={[
        'diff-file-comment',
        isRoot ? 'is-root' : 'is-reply',
        comment.source === 'remote' ? 'is-remote' : 'is-local',
      ].filter(Boolean).join(' ')}
    >
      <header className="diff-file-comment-head">
        <span className="diff-file-comment-author">{author}</span>
        <span
          className={[
            'diff-file-comment-source',
            comment.source === 'remote' ? 'is-remote' : 'is-local',
          ].join(' ')}
          title={sourceTitle}
        >
          {sourceLabel}
        </span>
        {ago && <span className="diff-file-comment-ago">{ago}</span>}
        {isRoot && comment.kato_status && comment.kato_status !== 'idle' && (
          <span
            className={`diff-file-comment-kato-status is-${comment.kato_status}`}
            title={describeKatoStatus(comment)}
          >
            {katoStatusLabel(comment.kato_status)}
          </span>
        )}
      </header>
      <div className="diff-file-comment-body">
        {comment.body || '(empty comment)'}
      </div>
      <footer className="diff-file-comment-actions">
        <button type="button" onClick={onReply} className="diff-file-comment-action">
          Reply
        </button>
        {isRoot && !isResolved && (
          <button
            type="button"
            onClick={onResolve}
            className="diff-file-comment-action"
          >
            Resolve
          </button>
        )}
        {isRoot && isResolved && (
          <button
            type="button"
            onClick={onReopen}
            className="diff-file-comment-action"
          >
            Reopen
          </button>
        )}
        {comment.source === 'local' && onDelete && (
          <button
            type="button"
            onClick={onDelete}
            className="diff-file-comment-action danger"
          >
            Delete
          </button>
        )}
      </footer>
    </div>
  );
}


function katoStatusLabel(status) {
  switch (status) {
    case 'queued': return '⏳ queued';
    case 'in_progress': return '⟳ kato working';
    case 'addressed': return '✓ kato addressed';
    case 'failed': return '✗ kato failed';
    default: return status;
  }
}


function describeKatoStatus(comment) {
  switch (comment.kato_status) {
    case 'queued':
      return 'Kato will run an agent on this comment when the live turn ends.';
    case 'in_progress':
      return 'Kato is running an agent against this comment right now.';
    case 'addressed':
      return comment.kato_addressed_sha
        ? `Kato pushed a fix in commit ${comment.kato_addressed_sha.slice(0, 8)}.`
        : 'Kato addressed this comment.';
    case 'failed':
      return comment.kato_failure_reason
        ? `Kato could not address this comment: ${comment.kato_failure_reason}`
        : 'Kato could not address this comment.';
    default:
      return '';
  }
}
