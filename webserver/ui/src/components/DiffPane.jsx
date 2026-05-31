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
import { decideAutoExpand } from './diffFileSize.js';
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
  // Signature of the last comments payload we committed. The comments
  // poll re-fires on every diff refresh (workspaceVersion bumps ~1.2s
  // during tool use); without this guard each fire built a brand-new
  // Map + new per-file arrays even when nothing changed, giving every
  // file box a new ``comments`` prop identity and re-rendering the whole
  // stacked diff. Skip the setState when the payload is unchanged so the
  // memoized file boxes can bail.
  const commentsSigRef = useRef('');

  const { appendToInput } = useChatComposer();
  const bodyRef = useRef(null);
  const fileRefs = useRef(new Map());
  // Last open-request id we auto-scrolled for. Guards the scroll-to-file
  // effect so it fires only on a fresh open request — never on a
  // background diff refresh (same id), which would yank the operator
  // away from the code they are reading. Starts at -1 (a value
  // openRequestId never takes) so the FIRST open still scrolls.
  const lastScrolledRequestRef = useRef(-1);
  // Same guard for the scroll-to-comment-thread effect below. It must
  // depend on commentsByRepo (the thread only exists once comments load),
  // but commentsByRepo also changes on every poll — a status flip the
  // poll picks up would otherwise re-scroll the pane to the thread while
  // the operator is reading. Marked handled only once we hit the real
  // thread, so the file→thread upgrade still works but poll re-fires
  // don't. Starts at -1 so the first open (openRequestId 0) still scrolls.
  const lastCommentScrolledRequestRef = useRef(-1);

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
      // Identical payload to last time → keep the existing state object
      // (and all its per-file comment arrays) so referential equality
      // holds and the memoized file boxes skip re-rendering.
      const sig = JSON.stringify(entries);
      if (sig === commentsSigRef.current) { return; }
      commentsSigRef.current = sig;
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

  // Flatten every repo's files in render order (folders-first,
  // alphabetical) ONCE — also stops the per-render buildDiffFileTree +
  // flatMap that used to run on every poll.
  const repoFileGroups = useMemo(() => (
    state.repoDiffs
      .map((repo) => {
        const rawFiles = repo.files || [];
        if (rawFiles.length === 0) { return null; }
        const { nodes } = buildDiffFileTree(rawFiles);
        const files = nodes.flatMap(function flatten(n) {
          return n.kind === 'folder' ? n.children.flatMap(flatten) : [n.file];
        });
        return { repo, files };
      })
      .filter(Boolean)
  ), [state.repoDiffs]);

  // Per-file auto-expand decision keyed by anchor (repo + path). The
  // cumulative-line budget (diffFileSize.js) runs across ALL files of
  // ALL repos in render order — the browser pays to mount + tokenize
  // every expanded file regardless of repo, so the budget must span
  // them. This is the protection the pane was missing: it used to
  // force-expand and synchronously tokenize EVERY file in the PR on
  // open, the dominant cause of diff-open lag (worst on Safari).
  const expandByKey = useMemo(() => {
    const flat = [];
    for (const { repo, files } of repoFileGroups) {
      for (const file of files) {
        flat.push({ key: diffAnchorKey(repo.repo_id, diffDisplayPath(file)), file });
      }
    }
    const decisions = decideAutoExpand(flat.map((entry) => entry.file));
    const map = new Map();
    flat.forEach((entry, i) => { map.set(entry.key, decisions[i]); });
    return map;
  }, [repoFileGroups]);

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
    // Only scroll for a NEW open request (the operator clicked a file in
    // the list). A background diff refresh re-runs this effect with the
    // SAME openRequestId — scrolling then would move the page out from
    // under the operator mid-read. Mark the request handled only once we
    // actually scroll, so a click that lands before the diff renders
    // still scrolls when it becomes ready.
    if (openRequestId === lastScrolledRequestRef.current) { return; }
    const node = resolveFileNode(repoId, relativePath);
    if (node && typeof node.scrollIntoView === 'function') {
      lastScrolledRequestRef.current = openRequestId;
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
    // Already centred this open request on its thread → don't re-scroll
    // when a later comments poll changes commentsByRepo's identity.
    if (openRequestId === lastCommentScrolledRequestRef.current) { return; }
    const fileNode = resolveFileNode(repoId, relativePath);
    if (!fileNode) { return; }
    const thread = fileNode.querySelector('.diff-file-comment-thread');
    const target = thread || fileNode;
    if (typeof target.scrollIntoView === 'function') {
      // Mark handled only when we reached the actual thread — until the
      // comments load we scroll to the file as a fallback and keep
      // retrying so the final landing is the thread, not the file top.
      if (thread) { lastCommentScrolledRequestRef.current = openRequestId; }
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
        {repoFileGroups.map(({ repo, files }) => {
          const repoComments = commentsByRepo.get(repo.repo_id);
          return (
            <section className="diff-pane-repo" key={repo.repo_id || repo.cwd}>
              {repoFileGroups.length > 1 && (
                <h3 className="diff-pane-repo-name">
                  {repo.repo_id || repo.cwd || 'repo'}
                </h3>
              )}
              {files.map((file) => {
                const path = diffDisplayPath(file);
                const key = diffAnchorKey(repo.repo_id, path);
                const targetKey = diffAnchorKey(repoId, relativePath);
                const isTargetFile = key === targetKey
                  || (!repoId && path === relativePath);
                const forceExpandToken = isTargetFile ? openRequestId : 0;
                // Cumulative-budget decision; the file the operator
                // actually opened always starts expanded (and stays
                // force-expanded via forceExpandToken), so the thing they
                // clicked is never hidden behind a "Show diff" button.
                const initiallyExpanded = isTargetFile
                  || (expandByKey.get(key) ?? false);
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
                      initiallyExpanded={initiallyExpanded}
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
