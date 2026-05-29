import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchDiff, fetchTaskComments } from '../api.js';
import {
  buildDiffFileTree,
  diffDisplayPath,
  diffFileKey,
  isFileConflicted,
  parseRepoDiffs,
} from '../diffModel.js';
import { useChatComposer } from '../contexts/ChatComposerContext.jsx';
import { apiErrorMessage } from '../utils/apiError.js';
import DiffFileWithComments from './DiffFileWithComments.jsx';

const EMPTY_COMMENTS = [];

// Stable per-file anchor id. The left Changes list passes the same
// (repoId, relativePath) so the centre can scroll to the matching
// section. Exported for unit tests.
export function diffAnchorKey(repoId, path) {
  return `${repoId || ''}::${path}`;
}

/**
 * Centre-column diff viewer. Renders EVERY changed file (all repos)
 * as a stacked list of the same ``DiffFileWithComments`` the Changes
 * tab/​tree drive — review comments included. The left list is pure
 * navigation: clicking a file there just scrolls this pane to that
 * file's section (it does NOT swap to a single-file view).
 *
 * ``openFile`` shape: ``{ taskId, relativePath, repoId, view:'diff' }``
 * — ``relativePath``/``repoId`` are the scroll target, not a filter.
 */
export default function DiffPane({
  openFile,
  workspaceVersion = 0,
  onCommentSpawned,
  onFocusFileInTree,
  onCommentsChanged,
}) {
  const taskId = openFile?.taskId || '';
  const repoId = openFile?.repoId || '';
  const relativePath = openFile?.relativePath || openFile?.absolutePath || '';
  const openRequestId = openFile?.openRequestId || 0;
  const focusComment = !!openFile?.focusComment;

  const [state, setState] = useState({
    status: 'loading', repoDiffs: [], error: '',
  });
  // repoId -> { loading, error, byFile: Map(path -> comments[]) }
  const [commentsByRepo, setCommentsByRepo] = useState(() => new Map());
  const [commentsTick, setCommentsTick] = useState(0);

  const { appendToInput } = useChatComposer();
  const bodyRef = useRef(null);
  const fileRefs = useRef(new Map());

  useEffect(() => {
    if (!taskId) {
      setState({ status: 'error', repoDiffs: [], error: 'No task bound.' });
      return undefined;
    }
    let cancelled = false;
    setState((prev) => (
      prev.status === 'ready'
        ? prev
        : { status: 'loading', repoDiffs: [], error: '' }
    ));
    // No repoId filter — the operator wants to see ALL changed files.
    fetchDiff(taskId)
      .then((payload) => {
        if (cancelled) { return; }
        setState({
          status: 'ready', repoDiffs: parseRepoDiffs(payload), error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({ status: 'error', repoDiffs: [], error: String(err) });
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion]);

  // One comments fetch per repo present in the diff (grouped by file
  // path). Re-runs when a comment mutation bumps ``commentsTick``.
  useEffect(() => {
    if (!taskId || state.status !== 'ready') { return undefined; }
    let cancelled = false;
    const repoIds = state.repoDiffs
      .map((r) => r.repo_id)
      .filter(Boolean);
    Promise.all(repoIds.map((rid) => (
      fetchTaskComments(taskId, rid)
        .then((result) => [rid, result])
        .catch(() => [rid, { ok: false, error: 'failed to load comments' }])
    ))).then((entries) => {
      if (cancelled) { return; }
      const next = new Map();
      for (const [rid, result] of entries) {
        const byFile = new Map();
        if (result.ok) {
          const list = Array.isArray(result.body?.comments)
            ? result.body.comments : [];
          for (const comment of list) {
            const p = String(comment.file_path || '');
            if (!byFile.has(p)) { byFile.set(p, []); }
            byFile.get(p).push(comment);
          }
        }
        next.set(rid, {
          loading: false,
          error: result.ok ? '' : apiErrorMessage(result, 'failed to load comments'),
          byFile,
        });
      }
      setCommentsByRepo(next);
    });
    return () => { cancelled = true; };
  }, [taskId, state.status, state.repoDiffs, commentsTick]);

  const bumpComments = useCallback(() => {
    setCommentsTick((n) => n + 1);
    if (typeof onCommentsChanged === 'function') {
      onCommentsChanged();
    }
  }, [onCommentsChanged]);

  // Each file box owns its OWN header (``.diff-file-header``, rendered
  // by DiffFileWithComments). That header is ``position: sticky`` — it
  // pins while you read its file and is pushed off by the NEXT file's
  // header as you scroll in, GitHub/Bitbucket style. This works now
  // because .diff-pane-body is an explicitly-bounded (inset:0) real
  // scroll container; every earlier "nothing sticks" failure was that
  // container not existing. No floating bar, no scroll tracker — the
  // browser does the handoff natively.
  const totalFiles = useMemo(
    () => state.repoDiffs.reduce((n, r) => n + (r.files?.length || 0), 0),
    [state.repoDiffs],
  );

  // Locate the rendered file node for a (repoId, path): exact anchor
  // match first, then a path-only match if the repo wasn't carried.
  const resolveFileNode = useCallback((rid, path) => (
    fileRefs.current.get(diffAnchorKey(rid, path))
      || [...fileRefs.current.entries()].find(
        ([k]) => k.endsWith(`::${path}`),
      )?.[1]
  ), []);

  // Scroll the targeted file's section into view whenever the left
  // list hands us a new (repoId, path) — that's the "click just
  // scrolls to it" behaviour. Runs after the diff is rendered.
  useEffect(() => {
    if (state.status !== 'ready' || !relativePath) { return; }
    const node = resolveFileNode(repoId, relativePath);
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [state.status, repoId, relativePath, openRequestId, totalFiles, resolveFileNode]);

  // When the operator clicked a file's comment badge (not the name),
  // go one step further than the file-scroll above: scroll to the
  // file's first comment thread. Comments load asynchronously, so this
  // also depends on ``commentsByRepo`` — it re-fires once the threads
  // render and centres the first one. Until then it falls back to the
  // file section so the view at least lands on the right file.
  useEffect(() => {
    if (!focusComment || state.status !== 'ready' || !relativePath) { return; }
    const fileNode = resolveFileNode(repoId, relativePath);
    if (!fileNode) { return; }
    const thread = fileNode.querySelector('.diff-file-comment-thread');
    const target = thread || fileNode;
    if (typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({ behavior: 'smooth', block: thread ? 'center' : 'start' });
    }
  }, [
    focusComment, state.status, repoId, relativePath, openRequestId,
    totalFiles, commentsByRepo, resolveFileNode,
  ]);

  if (state.status === 'loading') {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message">Computing diff…</p>
      </div>
    );
  }
  if (state.status === 'error') {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message error">{state.error}</p>
      </div>
    );
  }
  if (totalFiles === 0) {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message">No changes on this task branch.</p>
      </div>
    );
  }

  return (
    <div className="diff-pane">
      <div className="diff-pane-body" ref={bodyRef}>
        {/* No floating bar. Each file box's own ``.diff-file-header``
            is ``position: sticky`` (CSS), so the title is ATTACHED to
            its diff box, pins while you read that file, and is pushed
            off by the next file's header as you scroll — exactly the
            GitHub/Bitbucket per-file-card behaviour. */}
        {state.repoDiffs.map((repo) => {
          const rawFiles = repo.files || [];
          if (rawFiles.length === 0) { return null; }
          // Same order as the file tree (folders-first, alphabetical).
          const { nodes } = buildDiffFileTree(rawFiles);
          const files = nodes.flatMap(function flatten(n) {
            return n.kind === 'folder' ? n.children.flatMap(flatten) : [n.file];
          });
          const repoComments = commentsByRepo.get(repo.repo_id);
          return (
            <section className="diff-pane-repo" key={repo.repo_id || repo.cwd}>
              {state.repoDiffs.length > 1 && (
                <h3 className="diff-pane-repo-name">
                  {repo.repo_id || repo.cwd || 'repo'}
                </h3>
              )}
              {files.map((file, index) => {
                const path = diffDisplayPath(file);
                const key = diffAnchorKey(repo.repo_id, path);
                const targetKey = diffAnchorKey(repoId, relativePath);
                const isTargetFile = key === targetKey
                  || (!repoId && path === relativePath);
                const forceExpandToken = isTargetFile ? openRequestId : 0;
                const conflicted = isFileConflicted(file, repo.conflictedFiles);
                return (
                  <div
                    key={diffFileKey(file)}
                    className="diff-pane-file"
                    data-diff-key={key}
                    ref={(el) => {
                      if (el) { fileRefs.current.set(key, el); }
                      else { fileRefs.current.delete(key); }
                    }}
                  >
                    <DiffFileWithComments
                      file={file}
                      initiallyExpanded={true}
                      forceExpandToken={forceExpandToken}
                      conflicted={!!conflicted}
                      repoId={repo.repo_id}
                      repoCwd={repo.cwd}
                      taskId={taskId}
                      onAddToChat={appendToInput}
                      onFocusInTree={onFocusFileInTree}
                      comments={repoComments?.byFile.get(path) || EMPTY_COMMENTS}
                      commentsLoading={!!repoComments?.loading}
                      commentsError={repoComments?.error || ''}
                      onMutated={bumpComments}
                      onCommentSpawned={onCommentSpawned}
                    />
                  </div>
                );
              })}
            </section>
          );
        })}
      </div>
    </div>
  );
}
