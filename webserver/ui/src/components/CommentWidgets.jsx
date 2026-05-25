import { useEffect, useRef, useState } from 'react';
import { formatRelativeTime } from '../utils/relativeTime.js';
import { avatarColor, avatarInitials } from '../utils/avatar.js';
import { renderCommentMarkdown } from '../utils/commentMarkdown.jsx';
import { commentSubmitLock } from '../stores/commentSubmitLock.js';
import { readDraftByKey, writeDraftByKey } from '../utils/composerDraft.js';
import { toast } from '../stores/toastStore.js';

// Bubble + thread builder + form, shared between the file-level
// comments panel and the per-line widget rendered through
// react-diff-view's ``widgets`` prop. Lives in its own module so
// both consumers reuse the exact wording (load-bearing for how
// kato pushes replies on remote-sourced threads).

export function buildThreads(comments) {
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
  roots.sort((a, b) => (a.created_at_epoch || 0) - (b.created_at_epoch || 0));
  for (const root of roots) {
    root.replies.sort((a, b) => (a.created_at_epoch || 0) - (b.created_at_epoch || 0));
  }
  return roots.map((root) => ({ root, replies: root.replies }));
}


// Bitbucket-style identity avatar (initials monogram — kato has no
// uploaded photo). Deterministic colour so one author is always the
// same colour across the diff.
function CommentAvatar({ name }) {
  return (
    <span
      className="diff-file-comment-avatar"
      style={{ backgroundColor: avatarColor(name) }}
      aria-hidden="true"
    >
      {avatarInitials(name)}
    </span>
  );
}

// Bitbucket's "PENDING"-style outlined status pill. Maps kato's
// resolved / kato_status onto a single chip.
function statusPill(comment) {
  if (comment.status === 'resolved') {
    return { label: 'RESOLVED', cls: 'is-resolved' };
  }
  switch (comment.kato_status) {
    case 'queued': return { label: 'PENDING', cls: 'is-queued' };
    case 'in_progress': return { label: 'WORKING', cls: 'is-in_progress' };
    case 'addressed': return { label: 'ADDRESSED', cls: 'is-addressed' };
    case 'failed': return { label: 'FAILED', cls: 'is-failed' };
    default: return null;
  }
}

export function CommentBubble({
  comment, isRoot,
  onResolve, onReopen, onDelete, onReply, onMarkAddressed,
}) {
  const sourceLabel = comment.source === 'remote' ? 'REMOTE' : 'LOCAL';
  const sourceTitle = comment.source === 'remote'
    ? 'Pulled from the source git platform (Bitbucket / GitHub PR review). Resolving locally syncs back to the source thread.'
    : 'Local kato comment — kato runs on this immediately if idle.';
  const ago = comment.created_at_epoch
    ? formatRelativeTime((Date.now() / 1000) - comment.created_at_epoch)
    : '';
  const author = comment.author || (comment.source === 'remote' ? 'remote' : 'operator');
  const isResolved = comment.status === 'resolved';
  const katoStatus = comment.kato_status;
  const showMarkAddressed = (
    isRoot
    && typeof onMarkAddressed === 'function'
    && katoStatus !== 'addressed'
  );
  const pill = isRoot ? statusPill(comment) : null;

  // Bitbucket: every comment has a collapse chevron. A resolved root
  // ("done") starts collapsed so it doesn't dominate the diff; the
  // operator can still expand it, and it never disappears unless they
  // Delete it. Re-sync when the status flips (resolve→collapse,
  // reopen→expand) while still allowing a manual toggle in between.
  const [collapsed, setCollapsed] = useState(isRoot && isResolved);
  useEffect(() => { setCollapsed(isRoot && isResolved); }, [isRoot, isResolved]);

  return (
    <div
      className={[
        'diff-file-comment',
        isRoot ? 'is-root' : 'is-reply',
        comment.source === 'remote' ? 'is-remote' : 'is-local',
        collapsed ? 'is-collapsed' : '',
      ].filter(Boolean).join(' ')}
    >
      <header className="diff-file-comment-head">
        <CommentAvatar name={author} />
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
        {pill && (
          <span
            className={`diff-file-comment-pill ${pill.cls}`}
            title={describeKatoStatus(comment)}
          >
            {pill.label}
          </span>
        )}
        {ago && <span className="diff-file-comment-ago">{ago}</span>}
        <button
          type="button"
          className="diff-file-comment-collapse"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand comment' : 'Collapse comment'}
          title={collapsed ? 'Expand comment' : 'Collapse comment'}
        >
          {collapsed ? '▸' : '▾'}
        </button>
      </header>
      {!collapsed && (
        <>
          {isResolved && isRoot && (
            <div className="diff-file-comment-resolved-banner inline">
              ✓ {comment.resolved_by || 'operator'} resolved this thread
              {comment.resolved_at_epoch ? (
                <> · {formatRelativeTime((Date.now() / 1000) - comment.resolved_at_epoch)}</>
              ) : null}
            </div>
          )}
          <div className="diff-file-comment-body">
            {renderCommentMarkdown(comment.body)}
          </div>
          <footer className="diff-file-comment-actions">
            {typeof onReply === 'function' && (
              <button type="button" onClick={onReply} className="diff-file-comment-action">
                Reply
              </button>
            )}
            {isRoot && !isResolved && typeof onResolve === 'function' && (
              <button type="button" onClick={onResolve} className="diff-file-comment-action">
                Resolve
              </button>
            )}
            {isRoot && isResolved && typeof onReopen === 'function' && (
              <button type="button" onClick={onReopen} className="diff-file-comment-action">
                Reopen
              </button>
            )}
            {showMarkAddressed && (
              <button
                type="button"
                onClick={onMarkAddressed}
                className="diff-file-comment-action"
                title={
                  comment.source === 'remote'
                    ? 'Mark addressed locally + post the "Kato addressed" reply on the source git platform.'
                    : 'Mark this comment as addressed by kato.'
                }
              >
                Mark addressed
              </button>
            )}
            {comment.source === 'local' && typeof onDelete === 'function' && (
              <button
                type="button"
                onClick={onDelete}
                className="diff-file-comment-action danger"
              >
                Delete
              </button>
            )}
          </footer>
        </>
      )}
    </div>
  );
}


// One review thread (root + replies). Bitbucket-style: each comment
// is its own card and carries its OWN collapse chevron (see
// CommentBubble) — a resolved root starts collapsed. The thread is
// just the container; it never hides a comment, so nothing
// disappears unless the operator Deletes it. Shared by the per-line
// gutter widgets AND the file-level panel.
export function CommentThread({
  thread,
  onResolve,
  onReopen,
  onDelete,
  onReply,
  onMarkAddressed,
}) {
  const isResolved = thread.root.status === 'resolved';
  return (
    <article
      className={[
        'diff-file-comment-thread',
        isResolved ? 'is-resolved' : '',
      ].filter(Boolean).join(' ')}
    >
      <CommentBubble
        comment={thread.root}
        isRoot
        onResolve={() => onResolve(thread.root.id)}
        onReopen={() => onReopen(thread.root.id)}
        onDelete={() => onDelete(thread.root.id)}
        onReply={() => onReply(thread.root.id)}
        onMarkAddressed={() => onMarkAddressed(thread.root.id)}
      />
      {thread.replies.map((reply) => (
        <CommentBubble
          key={reply.id}
          comment={reply}
          isRoot={false}
          onDelete={() => onDelete(reply.id)}
          onReply={() => onReply(thread.root.id)}
        />
      ))}
    </article>
  );
}


// Bitbucket-style formatting toolbar. We keep the underlying field a
// plain <textarea> of markdown TEXT on purpose: a comment body is
// also the prompt kato feeds Claude, so it must stay clean text —
// these buttons just insert/wrap markdown syntax around the
// selection. No rich-text/WYSIWYG dependency, no body-format change.
const TOOLBAR = [
  { kind: 'bold', label: 'B', aria: 'Bold', style: { fontWeight: 700 } },
  { kind: 'italic', label: 'I', aria: 'Italic', style: { fontStyle: 'italic' } },
  { kind: 'code', label: '</>', aria: 'Inline code' },
  { kind: 'codeblock', label: '{ }', aria: 'Code block' },
  { kind: 'quote', label: '❝', aria: 'Quote' },
  { kind: 'ul', label: '•', aria: 'Bulleted list' },
  { kind: 'ol', label: '1.', aria: 'Numbered list' },
  { kind: 'link', label: '🔗', aria: 'Insert link' },
];

function applyMarkdown(textarea, draft, kind) {
  const start = textarea ? textarea.selectionStart : draft.length;
  const end = textarea ? textarea.selectionEnd : draft.length;
  const sel = draft.slice(start, end);
  const wrap = (before, after, ph) => {
    const inner = sel || ph;
    return {
      text: draft.slice(0, start) + before + inner + after + draft.slice(end),
      caret: start + before.length + inner.length + after.length,
    };
  };
  const linePrefix = (prefix, ph) => {
    const block = (sel || ph).split('\n').map((l) => prefix + l).join('\n');
    return {
      text: draft.slice(0, start) + block + draft.slice(end),
      caret: start + block.length,
    };
  };
  switch (kind) {
    case 'bold': return wrap('**', '**', 'bold text');
    case 'italic': return wrap('_', '_', 'italic text');
    case 'code': return wrap('`', '`', 'code');
    case 'codeblock': return wrap('```\n', '\n```', 'code');
    case 'quote': return linePrefix('> ', 'quote');
    case 'ul': return linePrefix('- ', 'item');
    case 'ol': return linePrefix('1. ', 'item');
    case 'link': return wrap('[', '](https://)', 'text');
    default: return { text: draft, caret: end };
  }
}

export function CommentForm({
  placeholder = 'Add a comment…',
  onSubmit,
  onCancel,
  replyMode = false,
  // Stable identity for draft persistence. When supplied, the
  // textarea contents are mirrored to localStorage on every keystroke
  // so the draft survives parent re-renders / unmounts (kato/claude
  // posting a sibling comment caused the diff view to re-key inline
  // forms and wipe the in-flight draft). Leave blank to opt out — the
  // form then behaves as a plain ephemeral textarea.
  draftKey = '',
}) {
  const [draft, setDraft] = useState(() => readDraftByKey(draftKey));
  const [busy, setBusy] = useState(false);
  // Mirror the global lock into local render state so the button
  // shows ``Submitting…`` and disables when ANY comment form is
  // mid-submit, not just this one. Without this an operator with
  // multiple comment forms open could fire two submits in parallel
  // and kato would race two review-fix runs against the same
  // workspace.
  const [globallyLocked, setGloballyLocked] = useState(commentSubmitLock.isBusy());
  const textareaRef = useRef(null);

  // Focus the textarea on mount so the operator can type
  // immediately after clicking the gutter / Reply.
  useEffect(() => {
    if (textareaRef.current) { textareaRef.current.focus(); }
  }, []);

  // Track the global lock so any other form's in-flight submit
  // disables this form's submit button too.
  useEffect(() => commentSubmitLock.subscribe(setGloballyLocked), []);

  // Mirror every keystroke into localStorage when a draftKey is
  // supplied, so an unmount + remount (e.g. parent re-keying the
  // form when kato posts a sibling comment) restores the draft on
  // the next mount instead of dropping it.
  useEffect(() => {
    if (draftKey) { writeDraftByKey(draftKey, draft); }
  }, [draftKey, draft]);

  async function submit() {
    const trimmed = draft.trim();
    if (!trimmed || busy) { return; }
    if (!commentSubmitLock.acquire()) {
      toast.show({
        kind: 'warning',
        title: 'Another comment is already being submitted',
        message: 'Wait for it to finish, then try again.',
        durationMs: 5000,
      });
      return;
    }
    setBusy(true);
    try {
      const ok = await onSubmit(trimmed);
      if (ok) {
        setDraft('');
        if (draftKey) { writeDraftByKey(draftKey, ''); }
      }
    } finally {
      setBusy(false);
      commentSubmitLock.release();
    }
  }

  function onToolbar(kind) {
    const ta = textareaRef.current;
    const { text, caret } = applyMarkdown(ta, draft, kind);
    setDraft(text);
    const restore = () => {
      if (ta) { ta.focus(); ta.setSelectionRange(caret, caret); }
    };
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(restore);
    } else {
      restore();
    }
  }

  return (
    <div className="diff-file-comments-form">
      <div className="diff-file-comments-toolbar" role="toolbar" aria-label="Formatting">
        {TOOLBAR.map((tool) => (
          <button
            key={tool.kind}
            type="button"
            className="diff-file-comments-toolbar-btn"
            onClick={() => onToolbar(tool.kind)}
            aria-label={tool.aria}
            title={tool.aria}
            style={tool.style}
            disabled={busy || globallyLocked}
          >
            {tool.label}
          </button>
        ))}
      </div>
      <textarea
        ref={textareaRef}
        className="diff-file-comments-textarea"
        placeholder={`${placeholder} (Cmd+Enter / Ctrl+Enter to submit)`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            submit();
          } else if (e.key === 'Escape' && typeof onCancel === 'function') {
            e.preventDefault();
            onCancel();
          }
        }}
        rows={3}
      />
      <div className="diff-file-comments-form-actions">
        {typeof onCancel === 'function' && (
          <button
            type="button"
            className="diff-file-comments-cancel"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          className="diff-file-comments-submit"
          onClick={submit}
          disabled={busy || globallyLocked || !draft.trim()}
        >
          {(busy || globallyLocked) ? 'Submitting…' : (replyMode ? 'Reply' : 'Add comment')}
        </button>
      </div>
    </div>
  );
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
