import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Editor from '@monaco-editor/react';
import {
  createTaskComment,
  deleteTaskComment,
  fetchFileContent,
  fetchTaskComments,
  markTaskCommentAddressed,
  reopenTaskComment,
  resolveTaskComment,
} from '../api.js';
import {
  CommentBubble,
  CommentForm,
  buildThreads,
} from './CommentWidgets.jsx';
import { useChatComposer } from '../contexts/ChatComposerContext.jsx';
import { toast } from '../stores/toastStore.js';

/**
 * Read-only Monaco editor that lives in the middle column.
 *
 * Driven by a single ``openFile`` prop — when it changes, the pane
 * refetches the file via /api/sessions/<task_id>/file and renders
 * it with VS-Code dark theme + syntax highlighting.
 *
 * Comments: the operator can right-click → "Add comment", or hover
 * a line and click the ``+`` glyph in the gutter, to attach a
 * review-style comment to that line. Comments are persisted via the
 * SAME ``/api/sessions/<task>/comments`` endpoints the Changes tab
 * uses (no parallel storage). Once submitted, kato auto-runs against
 * the comment when its turn ends (queued) or immediately if idle —
 * the ``kato_status`` badge above each bubble reflects that lifecycle.
 *
 * ``openFile`` shape:
 *   ``{ taskId, absolutePath, relativePath, repoId }``.
 */
export default function EditorPane({ openFile, onCommentSpawned }) {
  const [state, setState] = useState({
    loading: false,
    error: '',
    content: '',
    binary: false,
    tooLarge: false,
  });
  const [comments, setComments] = useState([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentsError, setCommentsError] = useState('');
  // ``activeLine`` is the line number where the inline composer is
  // currently open. ``null`` means no composer.
  const [activeLine, setActiveLine] = useState(null);
  // Reply state inside the comment list panel. Map of {threadId: bool}.
  const [replyTo, setReplyTo] = useState('');

  const { appendToInput } = useChatComposer();
  const taskId = openFile?.taskId || '';
  const repoId = openFile?.repoId || '';
  const filePath = openFile?.relativePath || openFile?.absolutePath || '';

  // Refs so Monaco actions (registered once) always read latest
  // values without closing over stale state.
  const openFileRef = useRef(openFile);
  const appendRef = useRef(appendToInput);
  const setActiveLineRef = useRef(setActiveLine);
  useEffect(() => { openFileRef.current = openFile; }, [openFile]);
  useEffect(() => { appendRef.current = appendToInput; }, [appendToInput]);
  useEffect(() => { setActiveLineRef.current = setActiveLine; }, []);

  // Monaco editor instance + decoration ids for hover line +
  // glyph-margin ``+``. Stored as refs because the hover effect is
  // event-driven (mouse move) and shouldn't trigger React re-renders.
  const editorRef = useRef(null);
  const hoverDecorationsRef = useRef([]);
  // Inline new-comment composer is rendered INTO a Monaco "view
  // zone" anchored at the clicked line (GitHub / VS Code style) —
  // not at the bottom of the pane. ``zoneNode`` is the DOM node
  // Monaco owns; we portal the React composer into it. ``zoneRef``
  // holds the live IViewZone so its height can be reflowed as the
  // textarea grows. File-level comments (line === -1) have no
  // editor line to anchor to, so they fall back to a bottom block.
  const [zoneNode, setZoneNode] = useState(null);
  const zoneIdRef = useRef(null);
  const zoneObjRef = useRef(null);

  // Comments scoped to the currently-open file. The /comments
  // endpoint returns the whole task's set (across repos + files);
  // filtering client-side keeps the request count low (one fetch
  // per file open vs. one per line interaction).
  const fileComments = useMemo(
    () => comments.filter((c) => String(c.file_path || '') === filePath),
    [comments, filePath],
  );
  const commentsByLine = useMemo(() => {
    const map = new Map();
    for (const c of fileComments) {
      const ln = Number(c.line);
      if (Number.isFinite(ln) && ln >= 0) {
        if (!map.has(ln)) { map.set(ln, []); }
        map.get(ln).push(c);
      }
    }
    return map;
  }, [fileComments]);
  // Hooks must be top-of-component (no conditional returns above
  // them), so build the threads list here even though it's only
  // rendered in the happy-path body below.
  const threads = useMemo(() => buildThreads(fileComments), [fileComments]);

  // Re-fetch the task's comment list. Used after every mutation so
  // the chip strip + bubbles reflect the new state without a poll.
  const refreshComments = useCallback(async () => {
    if (!taskId) {
      setComments([]); setCommentsError(''); return;
    }
    setCommentsLoading(true);
    try {
      const result = await fetchTaskComments(taskId, repoId);
      if (result.ok) {
        setComments(Array.isArray(result.body?.comments) ? result.body.comments : []);
        setCommentsError('');
      } else {
        setCommentsError(String(result.error || 'failed to load comments'));
      }
    } finally {
      setCommentsLoading(false);
    }
  }, [taskId, repoId]);

  useEffect(() => { refreshComments(); }, [refreshComments]);

  async function onCommentSubmit(line, body, parentId = '') {
    if (!body.trim()) { return false; }
    const result = await createTaskComment(taskId, {
      repo: repoId,
      file_path: filePath,
      line,
      body: body.trim(),
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
    refreshComments();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
    return true;
  }

  async function onResolve(comment) {
    const result = await resolveTaskComment(taskId, comment.id);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Resolve failed',
        message: (result.body && result.body.error) || result.error || 'resolve failed',
      });
      return;
    }
    refreshComments();
  }
  async function onReopen(comment) {
    const result = await reopenTaskComment(taskId, comment.id);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Reopen failed',
        message: (result.body && result.body.error) || result.error || 'reopen failed',
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
    refreshComments();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }
  async function onDelete(comment) {
    const result = await deleteTaskComment(taskId, comment.id);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Delete failed',
        message: (result.body && result.body.error) || result.error || 'delete failed',
      });
      return;
    }
    refreshComments();
  }
  async function onMarkAddressed(comment) {
    const result = await markTaskCommentAddressed(taskId, comment.id, '');
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Mark addressed failed',
        message: (result.body && result.body.error) || result.error || 'mark addressed failed',
      });
      return;
    }
    toast.show({
      kind: 'success',
      title: 'Marked addressed',
      message: '✓ "Kato addressed this review comment" reply posted',
      durationMs: 5000,
    });
    refreshComments();
  }

  function handleEditorMount(editor, monaco) {
    editorRef.current = editor;

    // Right-click → "Add to chat" pushes the selected line range
    // into the chat composer as ``file:N-M``.
    editor.addAction({
      id: 'kato.addSelectionToChat',
      label: 'Add to chat',
      contextMenuGroupId: 'kato',
      contextMenuOrder: 0,
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyA],
      run: (ed) => {
        const file = openFileRef.current;
        const append = appendRef.current;
        if (!file || typeof append !== 'function') { return; }
        const selection = ed.getSelection();
        const path = file.relativePath || file.absolutePath || '';
        if (!path) { return; }
        const repoPrefix = file.repoId ? `${file.repoId}:` : '';
        let reference;
        if (!selection || selection.isEmpty()) {
          const pos = ed.getPosition();
          reference = pos ? `${repoPrefix}${path}:${pos.lineNumber}` : `${repoPrefix}${path}`;
        } else if (selection.startLineNumber === selection.endLineNumber) {
          reference = `${repoPrefix}${path}:${selection.startLineNumber}`;
        } else {
          reference = `${repoPrefix}${path}:${selection.startLineNumber}-${selection.endLineNumber}`;
        }
        append(`${reference}\n`);
      },
    });

    // Right-click → "Add comment" opens the inline composer.
    editor.addAction({
      id: 'kato.addCommentOnSelection',
      label: 'Add comment',
      contextMenuGroupId: 'kato',
      contextMenuOrder: 1,
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyC],
      run: (ed) => {
        const pos = ed.getPosition();
        setActiveLineRef.current(pos ? pos.lineNumber : 1);
      },
    });

    // Hover: highlight the active line + show a ``+`` glyph in the
    // gutter so the operator can click to add a comment on that
    // line. Decorations are managed via deltaDecorations so we
    // don't leak references across hover transitions.
    editor.onMouseMove((e) => {
      const line = e?.target?.position?.lineNumber || 0;
      if (!line) {
        hoverDecorationsRef.current = editor.deltaDecorations(
          hoverDecorationsRef.current, [],
        );
        return;
      }
      hoverDecorationsRef.current = editor.deltaDecorations(
        hoverDecorationsRef.current,
        [
          {
            range: new monaco.Range(line, 1, line, 1),
            options: {
              isWholeLine: true,
              className: 'kato-line-hover',
              glyphMarginClassName: 'kato-add-comment-glyph',
              glyphMarginHoverMessage: { value: 'Add comment on this line' },
            },
          },
        ],
      );
    });
    editor.onMouseLeave?.(() => {
      hoverDecorationsRef.current = editor.deltaDecorations(
        hoverDecorationsRef.current, [],
      );
    });
    // Click on the gutter glyph → open the composer for that line.
    editor.onMouseDown((e) => {
      const monacoTypes = monaco.editor.MouseTargetType;
      const t = e?.target?.type;
      const isGlyph = t === monacoTypes.GUTTER_GLYPH_MARGIN;
      if (!isGlyph) { return; }
      const line = e?.target?.position?.lineNumber || 0;
      if (line) {
        setActiveLineRef.current(line);
      }
    });
  }

  // Switching files must not leave a stale inline composer (or its
  // view zone) anchored on the previous file's line.
  useEffect(() => {
    setActiveLine(null);
    setReplyTo('');
  }, [filePath]);

  // Add / move / remove the inline-composer view zone whenever the
  // target line changes. Line < 0 (file-level) gets no zone — it
  // renders in the bottom panel instead.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || typeof editor.changeViewZones !== 'function') {
      return undefined;
    }
    function removeZone() {
      if (zoneIdRef.current === null) { return; }
      editor.changeViewZones((acc) => acc.removeZone(zoneIdRef.current));
      zoneIdRef.current = null;
      zoneObjRef.current = null;
    }
    removeZone();
    if (activeLine === null || activeLine < 1) {
      setZoneNode(null);
      return undefined;
    }
    const dom = document.createElement('div');
    dom.className = 'editor-pane-zone-host';
    const zone = {
      afterLineNumber: activeLine,
      // Seed height; the ResizeObserver effect below keeps it in
      // sync with the actual composer height as the textarea grows.
      heightInPx: 200,
      domNode: dom,
    };
    editor.changeViewZones((acc) => {
      zoneIdRef.current = acc.addZone(zone);
    });
    zoneObjRef.current = zone;
    editor.revealLineInCenterIfOutsideViewport(activeLine);
    setZoneNode(dom);
    return removeZone;
  }, [activeLine]);

  // Keep the Monaco view zone exactly as tall as the composer it
  // hosts — Monaco zones don't auto-size to their DOM child, so we
  // measure and reflow on every content/size change.
  useEffect(() => {
    if (!zoneNode || typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const editor = editorRef.current;
    const sync = () => {
      if (!editor || zoneIdRef.current === null || !zoneObjRef.current) {
        return;
      }
      const next = Math.max(120, zoneNode.scrollHeight + 12);
      if (next !== zoneObjRef.current.heightInPx) {
        zoneObjRef.current.heightInPx = next;
        editor.changeViewZones((acc) => acc.layoutZone(zoneIdRef.current));
      }
    };
    const observer = new ResizeObserver(sync);
    observer.observe(zoneNode);
    sync();
    return () => observer.disconnect();
  }, [zoneNode]);

  // Scroll the editor to a line when the operator clicks a chip.
  function jumpToLine(line) {
    const editor = editorRef.current;
    if (!editor || !line) { return; }
    editor.revealLineInCenter(line);
    editor.setPosition({ lineNumber: line, column: 1 });
    editor.focus();
  }

  useEffect(() => {
    if (!openFile || !openFile.taskId || !openFile.absolutePath) {
      setState({
        loading: false, error: '', content: '',
        binary: false, tooLarge: false,
      });
      return undefined;
    }
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    fetchFileContent(openFile.taskId, openFile.absolutePath)
      .then((body) => {
        if (cancelled) { return; }
        setState({
          loading: false,
          error: '',
          content: body?.content || '',
          binary: !!body?.binary,
          tooLarge: !!body?.too_large,
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({
          loading: false,
          error: String(err && err.message ? err.message : err) || 'failed to load file',
          content: '', binary: false, tooLarge: false,
        });
      });
    return () => { cancelled = true; };
  }, [openFile?.taskId, openFile?.absolutePath]);

  if (!openFile || !openFile.absolutePath) {
    return (
      <section id="editor-pane">
        <div className="editor-pane-empty">
          <p>Pick a file from the left tree to preview it here.</p>
          <p className="editor-pane-empty-hint">
            Files open read-only — kato is the one editing the
            workspace; this view is for seeing what the agent does.
          </p>
        </div>
      </section>
    );
  }

  const language = languageForPath(openFile.relativePath || openFile.absolutePath);

  let body;
  if (state.loading) {
    body = <div className="editor-pane-message">Loading…</div>;
  } else if (state.tooLarge) {
    body = (
      <div className="editor-pane-message">
        File is too large for the in-browser preview (max 1 MB).
      </div>
    );
  } else if (state.binary) {
    body = (
      <div className="editor-pane-message">
        Binary file — no text preview available.
      </div>
    );
  } else if (state.error) {
    body = (
      <div className="editor-pane-message editor-pane-message-error">
        {state.error}
      </div>
    );
  } else {
    body = (
      <Editor
        theme="vs-dark"
        language={language}
        value={state.content}
        path={openFile.absolutePath}
        onMount={handleEditorMount}
        options={{
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          renderLineHighlight: 'none',
          smoothScrolling: true,
          automaticLayout: true,
          padding: { top: 8, bottom: 8 },
          guides: { indentation: true, bracketPairs: true },
          glyphMargin: true,
        }}
      />
    );
  }

  return (
    <section id="editor-pane">
      <header className="editor-pane-header">
        <span className="editor-pane-path" title={openFile.absolutePath}>
          {openFile.relativePath || openFile.absolutePath}
        </span>
        <span className="editor-pane-readonly-pill">read-only</span>
      </header>
      {fileComments.length > 0 && (
        <ChipStrip
          comments={fileComments}
          onJump={(line) => jumpToLine(line)}
          onAddOnLine={(line) => setActiveLine(line)}
        />
      )}
      <div className="editor-pane-body">
        {body}
      </div>
      {/* Line comment: rendered INLINE at the clicked line via a
          Monaco view zone (GitHub / VS Code style), portaled into
          the zone's DOM node. */}
      {activeLine !== null && activeLine >= 1 && zoneNode && createPortal(
        <div className="editor-pane-composer-wrap editor-pane-composer-inline">
          <header className="editor-pane-composer-head">
            Add comment on {openFile.relativePath || openFile.absolutePath}:{activeLine}
          </header>
          <CommentForm
            placeholder="What should kato do about this line?"
            onSubmit={(b) => onCommentSubmit(activeLine, b)}
            onCancel={() => setActiveLine(null)}
            draftKey={`kato.comment.draft.${taskId}|${repoId}|${filePath}|line:${activeLine}|root`}
          />
        </div>,
        zoneNode,
      )}
      {/* File-level comment (line -1) has no editor line to anchor
          to, so it stays in a bottom block. */}
      {activeLine === -1 && (
        <div className="editor-pane-composer-wrap">
          <header className="editor-pane-composer-head">
            Add a file-level comment on {openFile.relativePath || openFile.absolutePath}
          </header>
          <CommentForm
            placeholder="What should kato do about this file?"
            onSubmit={(b) => onCommentSubmit(activeLine, b)}
            onCancel={() => setActiveLine(null)}
            draftKey={`kato.comment.draft.${taskId}|${repoId}|${filePath}|file|root`}
          />
        </div>
      )}
      {threads.length > 0 && (
        <div className="editor-pane-comments-panel">
          <header className="editor-pane-comments-panel-head">
            Comments on this file ({threads.length})
          </header>
          {commentsError && (
            <p className="editor-pane-message editor-pane-message-error">
              {commentsError}
            </p>
          )}
          {threads.map(({ root, replies }) => (
            <div key={root.id} className="editor-pane-comment-thread">
              <div className="editor-pane-comment-anchor">
                {root.line >= 0 ? (
                  <button
                    type="button"
                    className="editor-pane-comment-jump"
                    onClick={() => jumpToLine(root.line)}
                    title="Jump to this line in the editor"
                  >
                    line {root.line}
                  </button>
                ) : (
                  <span className="editor-pane-comment-jump is-file">file-level</span>
                )}
              </div>
              <CommentBubble
                comment={root}
                isRoot
                onResolve={() => onResolve(root)}
                onReopen={() => onReopen(root)}
                onDelete={() => onDelete(root)}
                onReply={() => setReplyTo(root.id)}
                onMarkAddressed={() => onMarkAddressed(root)}
              />
              {replies.map((r) => (
                <CommentBubble
                  key={r.id}
                  comment={r}
                  isRoot={false}
                  onDelete={() => onDelete(r)}
                />
              ))}
              {replyTo === root.id && (
                <CommentForm
                  placeholder="Reply…"
                  replyMode
                  onSubmit={(b) => onCommentSubmit(root.line, b, root.id)}
                  onCancel={() => setReplyTo('')}
                  draftKey={`kato.comment.draft.${taskId}|${repoId}|${filePath}|reply:${root.id}|root`}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}


// Compact chip strip above the editor — one chip per comment on
// the current file. Status colour mirrors the kato_status badge
// CommentBubble shows ("queued" / "in_progress" / "addressed" /
// "failed"); clicking a chip scrolls the editor to that line.
function ChipStrip({ comments, onJump, onAddOnLine }) {
  const chips = comments
    .filter((c) => !c.parent_id) // only top-of-thread chips
    .map((c) => {
      const kStatus = (c.kato_status || 'idle').toLowerCase();
      const label = c.line >= 0 ? `L${c.line}` : 'file';
      const preview = String(c.body || '').slice(0, 80);
      return (
        <button
          key={c.id}
          type="button"
          className={`editor-pane-chip kato-${kStatus} status-${c.status || 'open'}`}
          onClick={() => (c.line >= 0 ? onJump(c.line) : onAddOnLine(-1))}
          title={preview}
        >
          <span className="editor-pane-chip-line">{label}</span>
          <span className="editor-pane-chip-status">{statusLabel(c)}</span>
          <span className="editor-pane-chip-body">{preview}</span>
        </button>
      );
    });
  return <div className="editor-pane-chip-strip">{chips}</div>;
}

function statusLabel(c) {
  if (c.status === 'resolved') { return '✓ resolved'; }
  switch ((c.kato_status || 'idle').toLowerCase()) {
    case 'queued': return '⏳ queued';
    case 'in_progress': return '⟳ working';
    case 'addressed': return '✓ done';
    case 'failed': return '✗ failed';
    default: return 'open';
  }
}


// Map a file path to a Monaco language id. Monaco ships with a
// long built-in list; we only translate uncommon extensions.
function languageForPath(path) {
  if (!path) { return 'plaintext'; }
  const lower = String(path).toLowerCase();
  if (lower.endsWith('.tsx')) { return 'typescript'; }
  if (lower.endsWith('.jsx')) { return 'javascript'; }
  if (lower.endsWith('.ts')) { return 'typescript'; }
  if (lower.endsWith('.js') || lower.endsWith('.mjs') || lower.endsWith('.cjs')) {
    return 'javascript';
  }
  if (lower.endsWith('.py')) { return 'python'; }
  if (lower.endsWith('.scss')) { return 'scss'; }
  if (lower.endsWith('.less')) { return 'less'; }
  if (lower.endsWith('.css')) { return 'css'; }
  if (lower.endsWith('.html') || lower.endsWith('.htm')) { return 'html'; }
  if (lower.endsWith('.json')) { return 'json'; }
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) { return 'markdown'; }
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) { return 'yaml'; }
  if (lower.endsWith('.sh') || lower.endsWith('.bash')) { return 'shell'; }
  if (lower.endsWith('.go')) { return 'go'; }
  if (lower.endsWith('.rs')) { return 'rust'; }
  if (lower.endsWith('.java')) { return 'java'; }
  if (lower.endsWith('.rb')) { return 'ruby'; }
  if (lower.endsWith('.xml') || lower.endsWith('.svg')) { return 'xml'; }
  if (lower.endsWith('.sql')) { return 'sql'; }
  if (lower.endsWith('.dockerfile') || lower.endsWith('/dockerfile')) {
    return 'dockerfile';
  }
  return 'plaintext';
}
