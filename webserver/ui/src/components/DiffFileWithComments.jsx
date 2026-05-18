import { useEffect, useMemo, useState } from 'react';
import Icon from './Icon.jsx';
import {
  Decoration,
  Diff,
  Hunk,
  computeNewLineNumber,
  computeOldLineNumber,
  expandFromRawCode,
  getChangeKey,
} from 'react-diff-view';

// Encode/decode old-side (deleted) line numbers so they are stored
// alongside new-side line numbers without collision. Any value
// <= -(OLD_LINE_OFFSET + 1) is an old-side encoded number;
// -1 through -(OLD_LINE_OFFSET) are file-level comment sentinels.
const OLD_LINE_OFFSET = 2000;
function encodeOldLine(n) { return -(n + OLD_LINE_OFFSET); }
function decodeOldLine(encoded) { return (-encoded) - OLD_LINE_OFFSET; }
function isOldSideEncoded(n) { return n < -(OLD_LINE_OFFSET); }
import {
  createTaskComment,
  deleteTaskComment,
  fetchBaseFileContent,
  markTaskCommentAddressed,
  reopenTaskComment,
  resolveTaskComment,
} from '../api.js';
import { toast } from '../stores/toastStore.js';
import { diffDisplayPath } from '../diffModel.js';
import { tokenizeHunks } from '../utils/diffSyntax.js';
import {
  CommentForm,
  CommentThread,
  buildThreads,
} from './CommentWidgets.jsx';
import {
  basePathForDiffFile,
  buildDiffRenderItems,
  expansionRangeForGap,
  pendingCommentExpansions,
  splitSourceLines,
} from './DiffExpansionHelpers.js';
import { isLargeFile } from './diffFileSize.js';

const DIFF_KIND_ICON = {
  add: 'plus',
  delete: 'minus',
  modify: 'edit',
  rename: 'edit',
  copy: 'edit',
};

// Default ``initiallyExpanded`` resolver: per-file rule only (no
// awareness of sibling files). The parent ``ChangesTab`` overrides
// this by passing ``initiallyExpanded`` derived from
// ``decideAutoExpand`` over the FULL file list so the cumulative
// budget can kick in.
function _defaultInitiallyExpanded(file) {
  return !isLargeFile(file);
}

function renderPathSegments(path) {
  const rawPath = String(path || '');
  const parts = rawPath.includes('/') && !rawPath.startsWith('/')
    ? rawPath.split('/').filter(Boolean)
    : [rawPath];
  return parts.map((part, index) => {
    const separator = index > 0 ? (
      <span className="diff-file-path-separator">/</span>
    ) : null;
    return (
      <span className="diff-file-path-part" key={`${part}-${index}`}>
        {separator}
        <span className="diff-file-path-segment">{part}</span>
      </span>
    );
  });
}

function DiffHeaderKindIcon({ kind }) {
  const iconName = DIFF_KIND_ICON[kind] || 'edit';
  return (
    <span className={`diff-file-row-kind kind-${kind || 'modify'}`}>
      <Icon name={iconName} />
    </span>
  );
}

// One <Diff> + per-line comment threads + file-level thread, all
// in one component so the comments state is shared across the
// gutter widgets and the bottom panel. Wraps react-diff-view's
// ``widgets`` API: each comment with ``line >= 0`` becomes a
// widget keyed by ``getChangeKey`` of the matching change. Clicks
// on the line gutter open an inline new-comment form widget at
// that line. File-level comments (``line < 0``) live in the
// bottom panel below the diff.
export default function DiffFileWithComments({
  file, conflicted = false, repoId = '', repoCwd = '', taskId = '',
  initiallyExpanded,
  forceExpandToken = 0,
  onAddToChat,
  onFocusInTree,
  comments = [],
  commentsLoading = false,
  commentsError = '',
  onMutated,
  onCommentSpawned,
}) {
  // Use the shared resolver, NOT ``file.newPath || file.oldPath``:
  // react-diff-view sets the missing side to ``/dev/null`` for pure
  // add/delete, so the naive form renders a deleted file's header as
  // "/dev/null" instead of its real (old) path.
  const path = diffDisplayPath(file);

  // ``activeLine`` is the line number where the inline new-comment
  // form is currently open. ``-1`` is the file-level panel below
  // the diff. ``null`` means no inline form is open.
  const [activeLine, setActiveLine] = useState(null);
  const [replyTo, setReplyTo] = useState('');
  // Auto-collapse big files. Rendering a 5K-line diff into the
  // DOM freezes the browser's paint loop and makes EVERY input on
  // the page lag (typing in the chat composer, opening the adopt
  // modal, etc.). Below the threshold the file expands by default
  // — the operator's normal flow is unchanged.
  // ``initiallyExpanded`` (when passed by ChangesTab) reflects the
  // cumulative-budget decision over the full file list. Fall back
  // to the per-file rule when called from a context that doesn't
  // know about siblings (e.g. CommitDiffModal showing one file).
  const [expanded, setExpanded] = useState(() => (
    typeof initiallyExpanded === 'boolean'
      ? initiallyExpanded
      : _defaultInitiallyExpanded(file)
  ));
  const [renderedHunks, setRenderedHunks] = useState(() => file.hunks || []);
  const [baseSource, setBaseSource] = useState({
    status: 'idle',
    lines: null,
    error: '',
  });

  useEffect(() => {
    setRenderedHunks(file.hunks || []);
    setBaseSource({ status: 'idle', lines: null, error: '' });
  }, [file.hunks, path, repoId, repoCwd, taskId]);

  useEffect(() => {
    if (forceExpandToken) { setExpanded(true); }
  }, [forceExpandToken]);

  // Tokenisation walks every hunk synchronously and is by far the
  // hottest first-paint cost on big diffs. Skip it entirely when
  // the file is collapsed; recompute lazily on expand.
  const tokens = useMemo(
    () => (expanded ? tokenizeHunks(renderedHunks, path) : null),
    [renderedHunks, path, expanded],
  );

  function notifyMutated() {
    if (typeof onMutated === 'function') { onMutated(); }
  }

  // Group comments by line so we can build the widgets dict and
  // the file-level panel separately. Line < 0 means "file-level."
  const { commentsByLine, fileLevelComments } = useMemo(() => {
    const byLine = new Map();
    const fileLevel = [];
    for (const comment of comments) {
      const ln = Number(comment.line);
      if (Number.isFinite(ln) && (ln >= 0 || isOldSideEncoded(ln))) {
        if (!byLine.has(ln)) { byLine.set(ln, []); }
        byLine.get(ln).push(comment);
      } else {
        fileLevel.push(comment);
      }
    }
    return { commentsByLine: byLine, fileLevelComments: fileLevel };
  }, [comments]);

  // New-side line numbers that carry at least one OPEN (un-resolved)
  // comment. These are the threads that must never be buried inside a
  // collapsed gap. Resolved-only lines are intentionally excluded —
  // they don't need to force the diff open. Stable across renders
  // when the comment set is unchanged so the reveal effect below
  // doesn't thrash.
  const openCommentLines = useMemo(() => {
    const out = [];
    for (const [line, lineComments] of commentsByLine) {
      // Auto-reveal only works for new-side lines (react-diff-view expand logic).
      if (line < 0) { continue; }
      if (lineComments.some((c) => c.status !== 'resolved')) {
        out.push(line);
      }
    }
    return out.sort((a, b) => a - b);
  }, [commentsByLine]);

  async function onSubmit(line, body, parentId = '') {
    const trimmed = String(body || '').trim();
    if (!trimmed) { return false; }
    const result = await createTaskComment(taskId, {
      repo: repoId,
      file_path: path,
      line,
      body: trimmed,
      parent_id: parentId,
    });
    if (!result.ok) {
      toast.show({
        kind: 'error',
        title: 'Could not add comment',
        message: (result.body && result.body.error) || result.error || 'add failed',
        durationMs: 8000,
      });
      return false;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment added',
      message: parentId
        ? '✓ reply posted (kato runs only on top-of-thread comments)'
        : (triggered
          ? '✓ kato is working on this comment now'
          : '✓ queued — kato will pick it up when the live agent goes idle'),
      durationMs: 5000,
    });
    setActiveLine(null);
    setReplyTo('');
    notifyMutated();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
    return true;
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
    const remoteSync = result.body?.remote_sync;
    if (remoteSync && remoteSync.attempted) {
      const lines = [];
      if (remoteSync.reply_posted) {
        lines.push('✓ posted reply on the source git platform');
      }
      if (remoteSync.resolved) {
        lines.push('✓ resolved the source thread too');
      }
      const errs = [
        remoteSync.error, remoteSync.reply_error, remoteSync.resolve_error,
      ].filter(Boolean);
      if (errs.length) {
        lines.push(`⚠ source-platform sync had issues: ${errs.join('; ')}`);
      }
      if (lines.length) {
        toast.show({
          kind: errs.length ? 'warning' : 'success',
          title: 'Resolved',
          message: lines.join('\n'),
          durationMs: 6000,
        });
      }
    }
    notifyMutated();
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
    notifyMutated();
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
    notifyMutated();
  }

  async function onMarkAddressed(commentId, addressedSha = '') {
    const result = await markTaskCommentAddressed(taskId, commentId, addressedSha);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Mark addressed failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    const remote = result.body?.remote_reply;
    if (remote && remote.attempted) {
      if (remote.reply_posted) {
        toast.show({
          kind: 'success', title: 'Posted on source platform',
          message: '✓ "Kato addressed this review comment" reply posted',
          durationMs: 5000,
        });
      } else if (remote.error || remote.reply_error) {
        toast.show({
          kind: 'warning',
          title: 'Marked addressed locally',
          message: `Source-platform reply failed: ${remote.error || remote.reply_error}`,
          durationMs: 8000,
        });
      }
    }
    notifyMutated();
  }

  // Build the react-diff-view widgets dict. Each widget is keyed
  // by the change's stable id (``getChangeKey``) so the line
  // doesn't lose its widget when the diff re-tokenizes between
  // polls. Widget content is the threads at that line plus an
  // inline new-comment form when ``activeLine`` matches. Skipped
  // entirely when collapsed — the dict feeds into a <Diff> we are
  // not going to render anyway.
  const widgets = useMemo(() => {
    if (!expanded) { return {}; }
    const out = {};
    function buildWidget(changeKey, lineKey, isOldSide) {
      const lineComments = commentsByLine.get(lineKey);
      const isActive = activeLine === lineKey;
      if (!lineComments && !isActive) { return; }
      const threads = buildThreads(lineComments || []);
      const displayLine = isOldSide ? decodeOldLine(lineKey) : lineKey;
      out[changeKey] = (
        <div className="diff-line-comments-host">
          {threads.map((thread) => (
            <CommentThread
              key={thread.root.id}
              thread={thread}
              onResolve={onResolve}
              onReopen={onReopen}
              onDelete={onDelete}
              onMarkAddressed={onMarkAddressed}
              onReply={(rootId) => {
                setActiveLine(lineKey);
                setReplyTo(rootId);
              }}
            />
          ))}
          {isActive && (
            <CommentForm
              placeholder={
                replyTo
                  ? 'Add a reply…'
                  : isOldSide
                    ? `Comment on deleted line ${displayLine}…`
                    : `Comment on line ${displayLine}…`
              }
              onSubmit={(body) => onSubmit(lineKey, body, replyTo)}
              onCancel={() => { setActiveLine(null); setReplyTo(''); }}
              replyMode={!!replyTo}
            />
          )}
        </div>
      );
    }
    for (const hunk of renderedHunks) {
      for (const change of hunk.changes || []) {
        const newLn = computeNewLineNumber(change);
        if (newLn != null && newLn >= 0) {
          buildWidget(getChangeKey(change), newLn, false);
        } else if (change.type === 'delete') {
          const oldLn = computeOldLineNumber(change);
          if (oldLn != null && oldLn >= 0) {
            buildWidget(getChangeKey(change), encodeOldLine(oldLn), true);
          }
        }
      }
    }
    return out;
  }, [renderedHunks, commentsByLine, activeLine, replyTo, expanded]);

  // Gutter click → open the inline form at that line (new-side or old-side).
  const gutterEvents = useMemo(() => ({
    onClick: ({ change }) => {
      const newLn = computeNewLineNumber(change);
      if (newLn != null && newLn >= 0) {
        setActiveLine((current) => (current === newLn ? null : newLn));
        setReplyTo('');
        return;
      }
      if (change.type === 'delete') {
        const oldLn = computeOldLineNumber(change);
        if (oldLn != null && oldLn >= 0) {
          const encoded = encodeOldLine(oldLn);
          setActiveLine((current) => (current === encoded ? null : encoded));
          setReplyTo('');
        }
      }
    },
  }), []);

  // Right-click → paste path + selection into chat composer (the
  // existing affordance the operator already has).
  function onContextMenu(event) {
    if (typeof onAddToChat !== 'function') { return; }
    event.preventDefault();
    const fragment = buildChatFragmentFromSelection(path, repoId);
    if (fragment) { onAddToChat(fragment); }
  }

  const fileThreads = useMemo(
    () => buildThreads(fileLevelComments),
    [fileLevelComments],
  );

  // The file-level comment form is shown ONLY when the operator
  // explicitly opens it (activeLine === -1, set by Reply on a
  // thread OR the "Add file-level comment" entry button). Previously
  // the form auto-opened on every file that had no comments yet,
  // which planted an unrequested textarea + Add-comment button
  // under every clean file in a diff — visual noise that operators
  // never asked for. The entry button below still surfaces the form
  // when needed.
  const fileFormOpen = activeLine === -1;
  const fileFormReplyMode = !!replyTo && activeLine === -1;
  const conflictedBadge = conflicted ? (
    <span
      className="diff-file-conflicted"
      aria-label="merge conflict"
      title="This file has merge conflicts that must be resolved before it can be merged."
    >
      <Icon name="warning" />
    </span>
  ) : null;
  const collapseToggle = expanded ? (
    <button
      type="button"
      className="diff-file-collapse-toggle is-icon tooltip-below"
      onClick={() => setExpanded(false)}
      data-tooltip="Collapse diff"
      aria-label="Collapse diff"
    >
      <Icon name="chevron-down" />
    </button>
  ) : (
    <button
      type="button"
      className="diff-file-collapse-toggle is-icon tooltip-below"
      onClick={() => setExpanded(true)}
      data-tooltip="Expand diff"
      aria-label="Expand diff"
    >
      <Icon name="chevron-right" />
    </button>
  );

  async function loadBaseSourceLines() {
    if (baseSource.status === 'ready') { return baseSource.lines; }
    if (baseSource.status === 'loading') { return null; }
    const basePath = basePathForDiffFile(file, path);
    if (!basePath || basePath === '/dev/null') { return null; }
    setBaseSource({ status: 'loading', lines: null, error: '' });
    try {
      const body = await fetchBaseFileContent(taskId, {
        repoId,
        repoCwd,
        path: basePath,
      });
      if (body.binary || body.too_large) {
        const error = body.too_large ? 'file too large' : 'binary file';
        setBaseSource({ status: 'error', lines: null, error });
        return null;
      }
      const lines = splitSourceLines(body.content || '');
      setBaseSource({ status: 'ready', lines, error: '' });
      return lines;
    } catch (err) {
      setBaseSource({ status: 'error', lines: null, error: String(err) });
      return null;
    }
  }

  // Auto-reveal buried threads. A comment anchored to a line that
  // sits inside a collapsed "N hidden lines" gap gets no
  // react-diff-view widget, so the thread is invisible until the
  // operator manually clicks the ↑/↓ expanders enough times to drag
  // that line into a hunk — the "I have to load more just to see the
  // comment" trap. Here: whenever an open comment's line isn't
  // already rendered, pull the base file and expand a tight window
  // around it so open threads are ALWAYS on screen. Re-runs after
  // each expand (renderedHunks dep) until nothing is missing, and
  // again whenever the base source becomes available.
  useEffect(() => {
    if (!expanded || openCommentLines.length === 0) { return undefined; }
    const present = new Set();
    for (const hunk of renderedHunks) {
      for (const change of hunk.changes || []) {
        const ln = computeNewLineNumber(change);
        if (ln != null && ln >= 0) { present.add(ln); }
      }
    }
    const missing = openCommentLines.filter((ln) => !present.has(ln));
    if (missing.length === 0) { return undefined; }
    let cancelled = false;
    (async () => {
      const sourceLines = await loadBaseSourceLines();
      if (cancelled || !sourceLines) { return; }
      const ranges = pendingCommentExpansions(
        renderedHunks, missing, sourceLines.length,
      );
      if (ranges.length === 0) { return; }
      setRenderedHunks((current) => {
        let next = current;
        for (const range of ranges) {
          next = expandFromRawCode(next, sourceLines, range.start, range.end);
        }
        return next;
      });
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, openCommentLines, renderedHunks, baseSource]);

  async function onExpandGap(event, gap, direction) {
    event.preventDefault();
    const range = expansionRangeForGap(gap, direction, event.shiftKey);
    if (!range) { return; }
    const sourceLines = await loadBaseSourceLines();
    if (!sourceLines) {
      toast.show({
        kind: 'warning',
        title: 'Could not expand context',
        message: baseSource.error || 'base file is not available yet',
        durationMs: 5000,
      });
      return;
    }
    setRenderedHunks((current) => (
      expandFromRawCode(current, sourceLines, range.start, range.end)
    ));
  }

  function renderGapDecoration(gap) {
    const loading = baseSource.status === 'loading';
    const label = `${gap.count} hidden line${gap.count === 1 ? '' : 's'}`;
    return (
      <Decoration
        key={gap.key}
        className="diff-context-expander"
        contentClassName="diff-context-expander-cell"
      >
        <div className="diff-context-expander-inner">
          <button
            type="button"
            className="diff-context-expander-btn"
            onClick={(event) => onExpandGap(event, gap, 'above')}
            disabled={loading}
            aria-label={`Show hidden lines above (${label})`}
            title="Show lines from the top of this hidden block. Shift-click shows all."
          >
            ↑
          </button>
          <span className="diff-context-expander-label">{label}</span>
          <button
            type="button"
            className="diff-context-expander-btn"
            onClick={(event) => onExpandGap(event, gap, 'below')}
            disabled={loading}
            aria-label={`Show hidden lines below (${label})`}
            title="Show lines from the bottom of this hidden block. Shift-click shows all."
          >
            ↓
          </button>
        </div>
      </Decoration>
    );
  }

  function renderDiffChildren(hunks) {
    const sourceLineCount = baseSource.lines ? baseSource.lines.length : 0;
    const items = buildDiffRenderItems(hunks, sourceLineCount);
    return items.map((item) => {
      if (item.type === 'gap') { return renderGapDecoration(item); }
      return <Hunk key={item.key} hunk={item.hunk} />;
    });
  }

  const diffBody = expanded ? (
    <Diff
      viewType="unified"
      diffType={file.type}
      hunks={renderedHunks}
      tokens={tokens}
      widgets={widgets}
      gutterEvents={gutterEvents}
    >
      {(hunks) => renderDiffChildren(hunks)}
    </Diff>
  ) : null;
  // The standalone "+ Add file-level comment" entry button and its
  // empty-state hint paragraph were removed on request — the diff
  // view no longer offers a file-level-comment entry point. Inline
  // gutter comments and replies to existing review threads (which
  // still set ``activeLine === -1`` via a thread's Reply) keep
  // working through ``fileLevelForm`` below.
  const fileLevelForm = fileFormOpen ? (
    <CommentForm
      placeholder={fileFormReplyMode ? 'Add a reply…' : 'Add a file-level comment…'}
      onSubmit={(body) => onSubmit(-1, body, fileFormReplyMode ? replyTo : '')}
      onCancel={
        activeLine === -1
          ? () => { setActiveLine(null); setReplyTo(''); }
          : null
      }
      replyMode={fileFormReplyMode}
    />
  ) : null;
  const commentsLoadingMessage = commentsLoading && comments.length === 0 ? (
    <p className="diff-file-comments-empty">Loading comments…</p>
  ) : null;
  const commentsErrorMessage = !commentsLoading && commentsError ? (
    <p className="diff-file-comments-empty error">{commentsError}</p>
  ) : null;
  const commentThreads = !commentsError && fileThreads.length > 0 ? fileThreads.map((thread) => (
    <CommentThread
      key={thread.root.id}
      thread={thread}
      onResolve={onResolve}
      onReopen={onReopen}
      onDelete={onDelete}
      onMarkAddressed={onMarkAddressed}
      onReply={(rootId) => {
        setActiveLine(-1);
        setReplyTo(rootId);
      }}
    />
  )) : null;
  const commentsPanel = (
    commentsLoadingMessage
    || commentsErrorMessage
    || commentThreads
    || fileLevelForm
  ) ? (
    <div className="diff-file-comments">
      {commentsLoadingMessage}
      {commentsErrorMessage}
      {commentThreads}
      {fileLevelForm}
    </div>
  ) : null;
  const pathSegments = renderPathSegments(path);
  const focusPathButton = typeof onFocusInTree === 'function' ? (
    <button
      type="button"
      className="diff-file-path diff-file-path-button"
      onClick={() => onFocusInTree({ repoId, relativePath: path })}
      title="Show this file in the file tree"
    >
      {pathSegments}
    </button>
  ) : (
    <span className="diff-file-path">{pathSegments}</span>
  );

  return (
    <section
      className={`diff-file ${expanded ? 'is-expanded' : 'is-collapsed'}`}
      onContextMenu={onContextMenu}
      title="Click a line gutter to add an inline comment · right-click to paste path + selection into chat"
    >
      <header className="diff-file-header">
        {collapseToggle}
        <DiffHeaderKindIcon kind={file.type} />
        {conflictedBadge}
        {focusPathButton}
      </header>
      {/* Stable wrapper for everything below the sticky header. The
          ``.diff-file`` card uses ``overflow: visible`` so its sticky
          header keeps working, which means the card's rounded bottom
          can't clip its children. Clipping THIS wrapper (a non-sticky
          sibling) rounds the bottom to match the card. */}
      <div className="diff-file-body">
        {diffBody}
        {commentsPanel}
      </div>
    </section>
  );
}


// Lift the diff-selection chat fragment helper inline so the
// component can consume it without an extra import — kept narrow
// to avoid pulling the full ChangesTab dependency graph.
function buildChatFragmentFromSelection(path, repoId) {
  if (typeof window === 'undefined' || !window.getSelection) { return ''; }
  const safePath = String(path || '').trim();
  if (!safePath) { return ''; }
  const repoPrefix = repoId ? `${repoId}:` : '';
  const text = String(window.getSelection().toString() || '').trim();
  if (!text) { return `\`${repoPrefix}${safePath}\``; }
  const truncated = text.length > 8 * 1024
    ? `${text.slice(0, 8 * 1024)}\n… (selection truncated)`
    : text;
  return (
    `In \`${repoPrefix}${safePath}\` the following diff lines:\n`
    + '```\n'
    + truncated
    + '\n```'
  );
}
