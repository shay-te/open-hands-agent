import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { Tree } from 'react-arborist';
import {
  fetchDiff,
  fetchFileTree,
  fetchRepoCommits,
  fetchTaskComments,
  syncTaskRepositories,
} from './api.js';
import AddRepositoryModal from './components/AddRepositoryModal.jsx';
import CommitDiffModal from './components/CommitDiffModal.jsx';
import Icon from './components/Icon.jsx';
import StickyHeader from './components/StickyHeader.jsx';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';
import {
  buildDiffFileTree,
  changedFileOpenTarget,
  countFileChangeStats,
  diffDisplayPath,
  parseRepoDiffs,
} from './diffModel.js';
import { toast } from './stores/toastStore.js';
import { copyTextToClipboard } from './utils/clipboard.js';
import {
  activateTreeNode,
  attachIds,
  folderContainsChange,
  matchTreeNode,
  normalizeTrees,
} from './FilesTabHelpers.js';
import { cssEscapeAttr } from './utils/dom.js';


// Same auto-poll cadence as ChangesTab. Keeps the file tree in sync
// with disk when files change outside of Claude's tool flow (manual
// edits, pulls, syncs). Honors document visibility so a background
// kato tab doesn't keep hammering the server.
const AUTO_POLL_INTERVAL_MS = 5000;
const EMPTY_DIFF_META = new Map();
const EMPTY_COMMENT_META = new Map();
const EMPTY_STATS = { added: 0, deleted: 0 };

// repoKey -> Map(repo-relative file path -> open thread count). A
// "thread" is a top-of-thread comment (``parent_id`` empty); replies
// don't add to the count, matching the Bitbucket 💬 N convention.
export function buildFilesCommentMeta(comments) {
  const byRepo = new Map();
  for (const comment of comments || []) {
    if (String(comment?.parent_id || '')) { continue; }
    if (comment?.status === 'resolved') { continue; }
    const filePath = String(comment?.file_path || '').trim();
    if (!filePath) { continue; }
    const repoId = String(comment?.repo_id || '').trim();
    const key = repoId || '';
    let fileMap = byRepo.get(key);
    if (!fileMap) { fileMap = new Map(); byRepo.set(key, fileMap); }
    fileMap.set(filePath, (fileMap.get(filePath) || 0) + 1);
  }
  return byRepo;
}
const DIFF_KIND_ICON = {
  add: 'plus',
  delete: 'minus',
  modify: 'edit',
  rename: 'edit',
  copy: 'edit',
};

export default function FilesTab({
  taskId,
  workspaceVersion = 0,
  focusFilterSignal = 0,
  focusFileTarget = null,
  onOpenFile,
}) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    trees: [],
    diffMetaByRepo: new Map(),
    commentMetaByRepo: new Map(),
    error: '',
  });
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [query, setQuery] = useState('');
  // The input itself stays bound to ``query`` (controlled, no input
  // lag), but the tree filter reads ``deferredQuery`` so the
  // potentially expensive node walk in ``matchTreeNode`` runs in a
  // lower-priority render. On a huge workspace, typing into the
  // filter previously walked every tree node on each keystroke and
  // janked the input.
  const deferredQuery = useDeferredValue(query);
  // Bumped after a successful repo-sync OR the auto-poll. Both
  // funnel into the fetch effect's dep array so the tree re-renders
  // when either fires.
  const [syncTick, setSyncTick] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [showAllFiles, setShowAllFiles] = useState(false);
  const [pathMenu, setPathMenu] = useState(null);
  const inFlightRef = useRef(false);
  const containerRef = useRef(null);
  const filterInputRef = useRef(null);
  const [size, setSize] = useState({ width: 320, height: 480 });

  // Cmd/Ctrl+P from the parent flips the right pane to Files (already
  // handled in RightPane) and bumps ``focusFilterSignal``; on every
  // bump we focus + select the input so the operator's first
  // keystroke after the shortcut goes into the filter, not somewhere
  // else.
  useEffect(() => {
    if (focusFilterSignal === 0) { return; }
    const node = filterInputRef.current;
    if (!node) { return; }
    node.focus();
    node.select();
  }, [focusFilterSignal]);

  // Reset the filter when switching tasks — every task has its own
  // file tree, so a stale query from the previous task would be
  // confusing if the same string doesn't match anything in the new
  // tree.
  useEffect(() => {
    setQuery('');
  }, [taskId]);

  useEffect(() => {
    if (!focusFileTarget || state.status !== 'ready') { return; }
    const targetPath = String(focusFileTarget.relativePath || '').trim();
    if (!targetPath) { return; }
    for (const repoTree of state.trees) {
      const repoKey = repoTree.repo_id || repoTree.cwd;
      const diffMeta = state.diffMetaByRepo.get(repoKey) || EMPTY_DIFF_META;
      if (!focusTargetMatchesRepo(focusFileTarget, repoTree, state.trees.length)) {
        continue;
      }
      if (!diffMeta.has(targetPath)) { continue; }
      setQuery('');
      setShowAllFiles(false);
      setCollapsed((prev) => {
        if (!prev.has(repoKey)) { return prev; }
        const next = new Set(prev);
        next.delete(repoKey);
        return next;
      });
      break;
    }
  }, [focusFileTarget, state.status, state.trees, state.diffMetaByRepo]);

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    inFlightRef.current = true;
    // Only flip to ``loading`` on the FIRST fetch for this taskId.
    // Subsequent refetches (driven by workspaceVersion bumps every 1.2s
    // during active tool use, or the auto-poll every 5s, or the
    // refresh button) keep the existing tree visible until the new
    // payload arrives — otherwise the tab body flashes "Loading…"
    // between every turn.
    setState((prev) => (
      prev.status === 'ready' || prev.status === 'error'
        ? prev
        : {
            status: 'loading', trees: [], diffMetaByRepo: new Map(),
            commentMetaByRepo: new Map(), error: '',
          }
    ));
    const diffMetaPromise = fetchDiff(taskId)
      .then((payload) => {
        return buildFilesDiffMeta(parseRepoDiffs(payload));
      })
      .catch((err) => {
        // Decoration only: keep the file browser usable if diff parsing fails.
        console.warn('Failed to load file-tree diff metadata', err);
        return new Map();
      });
    const commentMetaPromise = fetchTaskComments(taskId)
      .then((result) => buildFilesCommentMeta(result?.body?.comments || []))
      .catch(() => new Map());
    Promise.all([fetchFileTree(taskId), diffMetaPromise, commentMetaPromise])
      .then(([payload, diffMetaByRepo, commentMetaByRepo]) => {
        if (cancelled) { return; }
        setState({
          status: 'ready',
          trees: normalizeTrees(payload),
          diffMetaByRepo,
          commentMetaByRepo,
          error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState((prev) => ({
          status: 'error',
          trees: prev.trees,
          diffMetaByRepo: prev.diffMetaByRepo,
          commentMetaByRepo: prev.commentMetaByRepo,
          error: String(err),
        }));
      })
      .finally(() => {
        if (cancelled) { return; }
        inFlightRef.current = false;
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion, syncTick]);

  // Auto-poll while the tab is mounted so external changes (manual
  // edits, pulls, the sync button on a different kato tab) appear
  // without waiting for a Claude tool event to bump
  // ``workspaceVersion``. Visibility-aware so a background tab
  // doesn't keep churning the file walker on the server.
  useEffect(() => {
    if (!taskId || typeof window === 'undefined') { return undefined; }
    let timerId = null;
    function tick() {
      if (typeof document !== 'undefined' && document.hidden) { return; }
      if (inFlightRef.current) { return; }
      setSyncTick((n) => n + 1);
    }
    timerId = window.setInterval(tick, AUTO_POLL_INTERVAL_MS);
    return () => {
      if (timerId !== null) { window.clearInterval(timerId); }
    };
  }, [taskId]);


  // Blank state on task switch so we don't show stale data while
  // the new fetch is in flight.
  useEffect(() => {
    setState({
      status: 'loading',
      trees: [],
      diffMetaByRepo: new Map(),
      commentMetaByRepo: new Map(),
      error: '',
    });
  }, [taskId]);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof ResizeObserver === 'undefined') { return; }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) { return; }
      setSize({
        width: Math.max(160, Math.floor(entry.contentRect.width)),
        height: Math.max(200, Math.floor(entry.contentRect.height)),
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const repoIds = useMemo(() => {
    return state.trees.map((entry) => entry.repo_id || entry.cwd);
  }, [state.trees]);

  function toggleRepo(repoKey) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(repoKey)) { next.delete(repoKey); } else { next.add(repoKey); }
      return next;
    });
  }
  function collapseAll() { setCollapsed(new Set(repoIds)); }
  function expandAll() { setCollapsed(new Set()); }
  function openPathMenu(event, relativePath, repoId = '') {
    event.preventDefault();
    event.stopPropagation();
    const path = String(relativePath || '').trim();
    if (!path) { return; }
    setPathMenu({
      x: event.clientX,
      y: event.clientY,
      relativePath: path,
      repoId: String(repoId || '').trim(),
    });
  }
  function closePathMenu() {
    setPathMenu(null);
  }
  async function copyPathMenuRelativePath() {
    const path = String(pathMenu?.relativePath || '').trim();
    const repoPath = formatRepoRelativePath(pathMenu?.repoId, path);
    closePathMenu();
    if (!path) { return; }
    try {
      await copyTextToClipboard(repoPath);
      toast.show({
        kind: 'success',
        title: 'Copied relative path',
        message: repoPath,
        durationMs: 2500,
      });
    } catch (err) {
      toast.show({
        kind: 'error',
        title: 'Copy failed',
        message: String(err?.message || err || 'clipboard unavailable'),
        durationMs: 5000,
      });
    }
  }

  useEffect(() => {
    if (!pathMenu) { return undefined; }
    function onPointerDown() { closePathMenu(); }
    function onKeyDown(event) {
      if (event.key === 'Escape') { closePathMenu(); }
    }
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [pathMenu]);

  // Sync icon: re-resolve the task's repositories from YouTrack /
  // Jira / etc. tags + description, and clone any that aren't yet on
  // disk. Pure additive — repos already cloned (or repos no longer
  // on the task) stay untouched. Lets the operator add a
  // ``kato:repo:<name>`` tag and pull the new repo into the
  // workspace from the UI without re-running the whole task.
  async function onSyncRepositories() {
    if (!taskId || syncing) { return; }
    setSyncing(true);
    const result = await syncTaskRepositories(taskId);
    setSyncing(false);
    const { title, message, kind } = formatSyncResult(result);
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 7000,
    });
    // Bump the local sync-tick so the file tree refetches and any
    // newly-cloned repos render. Even on a no-op sync the refetch
    // is harmless and keeps the tree in sync with disk.
    if (result.ok) { setSyncTick((n) => n + 1); }
  }

  // Tracks repos already in the workspace so the "+ Add repository"
  // picker filters them out — same source the file tree uses, so no
  // extra fetch needed.
  const attachedRepoIds = useMemo(() => {
    const set = new Set();
    for (const tree of state.trees) {
      const id = String(tree?.repo_id || '').trim();
      if (id) { set.add(id.toLowerCase()); }
    }
    return set;
  }, [state.trees]);
  const hasChangedFiles = useMemo(() => {
    for (const fileMeta of state.diffMetaByRepo.values()) {
      if (fileMeta.size > 0) { return true; }
    }
    return false;
  }, [state.diffMetaByRepo]);
  const allFilesButtonClass = [
    'files-tab-text-btn',
    showAllFiles ? 'active' : '',
  ].filter(Boolean).join(' ');
  const allFilesToggle = hasChangedFiles ? (
    <button
      type="button"
      className={allFilesButtonClass}
      data-tooltip={showAllFiles ? 'Showing all files' : 'Show all files'}
      aria-label={showAllFiles ? 'Showing all files' : 'Show all files'}
      aria-pressed={showAllFiles ? 'true' : 'false'}
      onClick={() => setShowAllFiles((prev) => !prev)}
    >
      All
    </button>
  ) : null;

  const toolbar = (
    <span className="files-tab-toolbar">
      {allFilesToggle}
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip={
          'Add repository — pick from kato\'s inventory, tag the '
          + 'task with ``kato:repo:<id>``, and clone it into the '
          + 'workspace. Filters out repos already attached.'
        }
        aria-label="Add repository to task"
        onClick={() => setAddModalOpen(true)}
        disabled={!taskId}
      >
        <Icon name="folder-plus" />
      </button>
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip={
          'Sync repositories — clone any repos this task touches '
          + 'that aren’t in the workspace yet (driven by '
          + '``kato:repo:<name>`` tags + description). Never removes '
          + 'a repo from disk; purely additive.'
        }
        aria-label="Sync task repositories"
        onClick={onSyncRepositories}
        disabled={syncing || !taskId}
      >
        <Icon name="refresh" spin={syncing} />
      </button>
      {repoIds.length > 1 && (
        <>
          <button
            type="button"
            className="files-tab-icon-btn"
            data-tooltip="Expand all repositories — show every file in every workspace."
            aria-label="Expand all repositories"
            onClick={expandAll}
          >
            <Icon name="plus" />
          </button>
          <button
            type="button"
            className="files-tab-icon-btn"
            data-tooltip="Collapse all repositories — keep only the repository names visible."
            aria-label="Collapse all repositories"
            onClick={collapseAll}
          >
            <Icon name="minus" />
          </button>
        </>
      )}
    </span>
  );

  let body;
  if (state.status === 'loading') {
    body = <p className="files-tab-message">Loading files…</p>;
  } else if (state.status === 'error') {
    body = <p className="files-tab-message error">{state.error}</p>;
  } else if (state.trees.length === 0) {
    body = <p className="files-tab-message">No tracked files in this task.</p>;
  } else {
    body = state.trees.map((repoTree) => {
      const repoKey = repoTree.repo_id || repoTree.cwd;
      const diffMeta = state.diffMetaByRepo.get(repoKey) || EMPTY_DIFF_META;
      const commentMeta = state.commentMetaByRepo.get(repoTree.repo_id)
        || state.commentMetaByRepo.get(repoKey)
        || state.commentMetaByRepo.get('')
        || EMPTY_COMMENT_META;
      return (
        <RepoTree
          key={repoKey}
          repoTree={repoTree}
          width={size.width}
          collapsed={collapsed.has(repoKey)}
          onToggle={() => toggleRepo(repoKey)}
          onPickFile={appendToInput}
          onOpenFile={onOpenFile}
          onOpenPathMenu={openPathMenu}
          searchTerm={deferredQuery}
          conflictedFiles={repoTree.conflictedFiles}
          changedFiles={repoTree.changedFiles}
          diffMeta={diffMeta}
          commentMeta={commentMeta}
          showAllFiles={showAllFiles}
          taskId={taskId}
          focusFileTarget={focusFileTarget}
        />
      );
    });
  }

  const filterRow = (
    <div className="files-tab-filter">
      <span className="files-tab-filter-icon" aria-hidden="true">
        <Icon name="search" />
      </span>
      <input
        ref={filterInputRef}
        type="search"
        className="files-tab-filter-input"
        placeholder="Search files… (Cmd+P)"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') { setQuery(''); } }}
        aria-label="Search files in this task's workspace"
        spellCheck={false}
        autoComplete="off"
      />
      {query && (
        <button
          type="button"
          className="files-tab-filter-clear"
          onClick={() => setQuery('')}
          aria-label="Clear search"
          title="Clear (Esc)"
        >
          ×
        </button>
      )}
    </div>
  );

  const header = (
    <header className="files-tab-header">
      {filterRow}
      {toolbar}
    </header>
  );
  return (
    <div className="files-tab">
      {header}
      <div className="files-tab-body" ref={containerRef}>
        {body}
      </div>
      {pathMenu && (
        <div
          className="files-tab-context-menu"
          style={{ left: pathMenu.x, top: pathMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
          role="menu"
        >
          <button
            type="button"
            className="files-tab-context-menu-item"
            onClick={copyPathMenuRelativePath}
            role="menuitem"
          >
            Copy relative path
          </button>
        </div>
      )}
      {addModalOpen && (
        <AddRepositoryModal
          taskId={taskId}
          alreadyAttachedIds={attachedRepoIds}
          onClose={() => setAddModalOpen(false)}
          onAdded={() => {
            // Bump the sync tick so the file tree refetches and the
            // newly-cloned repo appears as a top-level entry without
            // waiting for the auto-poll.
            setSyncTick((n) => n + 1);
          }}
        />
      )}
    </div>
  );
}

// Render the sync-repos result into a toast title + message. Three
// outcomes the operator cares about, mapped to kind / wording:
//   * already in sync — green, "no missing repos"
//   * added N — green, lists the names so the operator can see what
//     showed up in the tree
//   * partial / failed — red or amber, surfaces the error
// Exported for tests. Pure mapping from a sync api result to the
// kind/title/message of the operator-facing toast.
export function formatSyncResult(result) {
  const body = (result && result.body) || {};
  if (!result || !result.ok) {
    return {
      kind: 'error',
      title: 'Sync repositories failed',
      message: (result && result.error) || body.error || 'unknown error',
    };
  }
  const added = body.added_repositories || [];
  const failed = body.failed_repositories || [];
  if (failed.length) {
    const errs = failed
      .map((entry) => `${entry.repository_id}: ${entry.error}`)
      .join('\n');
    return {
      kind: added.length ? 'warning' : 'error',
      title: added.length ? 'Sync partially succeeded' : 'Sync failed',
      message: added.length
        ? `✓ added ${added.length} repo(s): ${added.join(', ')}\n✗ ${errs}`
        : `✗ ${errs}`,
    };
  }
  if (added.length === 0) {
    return {
      kind: 'success',
      title: 'Repositories already in sync',
      message: 'No missing repositories — the workspace already has every repo this task touches.',
    };
  }
  return {
    kind: 'success',
    title: `Added ${added.length} repository(ies)`,
    message: `✓ cloned: ${added.join(', ')}`,
  };
}

export function buildFilesDiffMeta(repoDiffs) {
  const byRepo = new Map();
  for (const repoDiff of repoDiffs || []) {
    const fileMeta = new Map();
    for (const file of repoDiff.files || []) {
      const path = diffDisplayPath(file);
      fileMeta.set(path, {
        file,
        kind: file.type || 'modify',
        stats: countFileChangeStats(file),
      });
    }
    const repoId = String(repoDiff.repo_id || '').trim();
    const cwd = String(repoDiff.cwd || '').trim();
    if (repoId) { byRepo.set(repoId, fileMeta); }
    if (cwd) { byRepo.set(cwd, fileMeta); }
  }
  return byRepo;
}


function RepoTree({
  repoTree, width, collapsed, onToggle, onPickFile,
  onOpenFile, onOpenPathMenu,
  searchTerm = '', conflictedFiles, changedFiles, diffMeta = EMPTY_DIFF_META,
  commentMeta = EMPTY_COMMENT_META,
  showAllFiles = false, taskId = '', focusFileTarget = null,
}) {
  const repoRef = useRef(null);
  const treeData = useMemo(() => {
    return attachIds(repoTree.tree, repoTree.cwd);
  }, [repoTree.tree, repoTree.cwd]);
  const heading = repoTree.repo_id || repoTree.cwd || 'repo';
  const repoId = String(repoTree.repo_id || '').trim();
  const changedFilesList = useMemo(() => {
    return Array.from(diffMeta.values())
      .map((meta) => meta.file)
      .filter(Boolean);
  }, [diffMeta]);
  const changedTree = useMemo(() => {
    return buildDiffFileTree(changedFilesList);
  }, [changedFilesList]);
  const filteredChangedNodes = useMemo(() => {
    return filterChangedFileTree(changedTree.nodes, searchTerm);
  }, [changedTree.nodes, searchTerm]);
  const hasChangedFiles = changedTree.nodes.length > 0;
  // While filtering, expand by default so the operator sees every
  // matching descendant without clicking through ancestor folders.
  const isFiltering = !!searchTerm.trim();
  const treeHeight = Math.max(120, Math.min(treeData.length * 28 + 8, 800));
  const chevronName = collapsed ? 'chevron-right' : 'chevron-down';
  const [closedChangedFolders, setClosedChangedFolders] = useState(() => new Set());
  const [selectedChangedKey, setSelectedChangedKey] = useState('');
  // Per-repo commit dropdown state. Populated lazily on first
  // open so we don't fetch ``/commits`` for every repo on every
  // file-tree refetch (would be 5+ extra HTTP calls per
  // workspace-version bump otherwise).
  const [commitsState, setCommitsState] = useState({
    status: 'idle', items: [], error: '',
  });
  const [commitMenuOpen, setCommitMenuOpen] = useState(false);
  const [activeCommit, setActiveCommit] = useState(null);

  useEffect(() => {
    if (!focusTargetMatchesRepo(focusFileTarget, repoTree, 1)) { return undefined; }
    const targetPath = String(focusFileTarget?.relativePath || '').trim();
    if (!targetPath) { return undefined; }
    const focusInfo = findChangedFileFocusInfo(changedTree.nodes, targetPath);
    if (!focusInfo) { return undefined; }
    setSelectedChangedKey(changedFileSelectionKey(focusInfo.file));
    setClosedChangedFolders((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const key of focusInfo.ancestorKeys) {
        if (next.delete(key)) { changed = true; }
      }
      return changed ? next : prev;
    });
    const timer = window.requestAnimationFrame(() => {
      const root = repoRef.current;
      if (!root) { return; }
      const selector = `[data-changed-file-path="${cssEscapeAttr(targetPath)}"]`;
      const row = root.querySelector(selector);
      if (row && typeof row.scrollIntoView === 'function') {
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
    return () => window.cancelAnimationFrame(timer);
  }, [
    focusFileTarget,
    focusFileTarget?.requestId,
    searchTerm,
    changedTree.nodes,
    repoTree,
  ]);

  async function ensureCommitsLoaded() {
    if (!taskId || !repoId) { return; }
    if (commitsState.status === 'ready' || commitsState.status === 'loading') {
      return;
    }
    setCommitsState({ status: 'loading', items: [], error: '' });
    const result = await fetchRepoCommits(taskId, repoId, { limit: 50 });
    if (!result.ok) {
      setCommitsState({
        status: 'error', items: [],
        error: String(result.error || 'failed to load commits'),
      });
      return;
    }
    setCommitsState({
      status: 'ready',
      items: Array.isArray(result.body?.commits) ? result.body.commits : [],
      error: '',
    });
  }

  function toggleCommitMenu(event) {
    // Stop the click from bubbling to the header — header click
    // is "expand/collapse repo", which we explicitly DON'T want
    // when the operator clicks the commit-list icon.
    event.stopPropagation();
    if (!commitMenuOpen) { ensureCommitsLoaded(); }
    setCommitMenuOpen((prev) => !prev);
  }

  function pickCommit(commit) {
    setCommitMenuOpen(false);
    setActiveCommit(commit);
  }
  function toggleChangedFolder(key) {
    setClosedChangedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(key)) { next.delete(key); } else { next.add(key); }
      return next;
    });
  }
  function selectChangedFile(file) {
    setSelectedChangedKey(changedFileSelectionKey(file));
    if (typeof onOpenFile === 'function') {
      onOpenFile(changedFileOpenTarget({
        cwd: repoTree.cwd,
        repo_id: repoId,
      }, file));
    }
  }
  const changedTreeContent = hasChangedFiles && filteredChangedNodes.length > 0 ? (
    <ChangedFilesTree
      nodes={filteredChangedNodes}
      stats={changedTree.stats}
      conflictedFiles={conflictedFiles}
      commentMeta={commentMeta}
      closedFolders={closedChangedFolders}
      selectedKey={selectedChangedKey}
      onToggleFolder={toggleChangedFolder}
      onSelectFile={selectChangedFile}
      onOpenPathMenu={onOpenPathMenu}
      repoId={repoId}
    />
  ) : null;
  const emptyChangedSearch = hasChangedFiles && filteredChangedNodes.length === 0 ? (
    <p className="files-tab-message">No changed files match this search.</p>
  ) : null;
  let body;
  if (collapsed) {
    body = null;
  } else if (!showAllFiles && changedTreeContent) {
    body = changedTreeContent;
  } else if (!showAllFiles && emptyChangedSearch) {
    body = emptyChangedSearch;
  } else if (treeData.length === 0) {
    body = <p className="files-tab-message">No tracked files in this repo.</p>;
  } else {
    body = (
      <Tree
        data={treeData}
        width={width}
        height={treeHeight}
        rowHeight={28}
        indent={14}
        openByDefault={isFiltering}
        searchTerm={searchTerm}
        searchMatch={matchTreeNode}
        disableDrag
        disableDrop
        disableEdit
      >
        {(props) => (
          <Node
            {...props}
            onPickFile={onPickFile}
            onOpenFile={onOpenFile}
            onOpenPathMenu={onOpenPathMenu}
            conflictedFiles={conflictedFiles}
            changedFiles={changedFiles}
            diffMeta={diffMeta}
            commentMeta={commentMeta}
            repoId={repoId}
          />
        )}
      </Tree>
    );
  }
  return (
    <section className="files-tab-repo" ref={repoRef}>
      <StickyHeader
        as="header"
        className="files-tab-repo-header"
        title={repoTree.cwd}
        onClick={onToggle}
      >
        <span className="files-tab-repo-chevron">
          <Icon name={chevronName} />
        </span>
        <span className="files-tab-repo-name">{heading}</span>
        {repoId && taskId && (
          <button
            type="button"
            className="files-tab-repo-commits-btn tooltip-end"
            onClick={toggleCommitMenu}
            aria-haspopup="listbox"
            aria-expanded={commitMenuOpen ? 'true' : 'false'}
            data-tooltip="Commit history — pick a commit on this repo's task branch to scope the Changes tab to that single commit's diff."
            aria-label={`View commit history for ${heading}`}
          >
            <Icon name="history" />
          </button>
        )}
      </StickyHeader>
      {commitMenuOpen && (
        <CommitDropdown
          state={commitsState}
          onPick={pickCommit}
          onClose={() => setCommitMenuOpen(false)}
        />
      )}
      {body}
      {activeCommit && (
        <CommitDiffModal
          taskId={taskId}
          repoId={repoId}
          commit={activeCommit}
          onClose={() => setActiveCommit(null)}
        />
      )}
    </section>
  );
}


function CommitDropdown({ state, onPick, onClose }) {
  // Light-touch "click outside" behaviour: a backdrop catches
  // outside clicks without trapping mouse events on the rest of
  // the page (a real popover library would be overkill for one
  // dropdown).
  return (
    <>
      <div
        className="files-tab-commit-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <ul className="files-tab-commit-menu" role="listbox">
        {state.status === 'loading' && (
          <li className="files-tab-commit-empty">Loading commits…</li>
        )}
        {state.status === 'error' && (
          <li className="files-tab-commit-empty error">{state.error}</li>
        )}
        {state.status === 'ready' && state.items.length === 0 && (
          <li className="files-tab-commit-empty">
            No commits on the task branch yet.
          </li>
        )}
        {state.status === 'ready' && state.items.map((commit) => (
          <li key={commit.sha}>
            <button
              type="button"
              role="option"
              className="files-tab-commit-row"
              onClick={() => onPick(commit)}
              aria-selected="false"
              title={commit.sha}
            >
              <code className="files-tab-commit-sha">{commit.short_sha}</code>
              <span className="files-tab-commit-subject">
                {commit.subject || '(no subject)'}
              </span>
              <span className="files-tab-commit-author">{commit.author}</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function ChangedFilesTree({
  nodes, stats, conflictedFiles, commentMeta = EMPTY_COMMENT_META,
  closedFolders, selectedKey, onToggleFolder, onSelectFile, onOpenPathMenu,
  repoId = '',
}) {
  const rows = nodes.map((node) => (
    <ChangedFilesTreeNode
      key={node.key}
      node={node}
      depth={0}
      relativePath=""
      conflictedFiles={conflictedFiles}
      commentMeta={commentMeta}
      closedFolders={closedFolders}
      selectedKey={selectedKey}
      onToggleFolder={onToggleFolder}
      onSelectFile={onSelectFile}
      onOpenPathMenu={onOpenPathMenu}
      repoId={repoId}
    />
  ));
  return (
    <div className="files-changed-tree-wrap">
      <div className="diff-tree-summary files-tree-summary">
        <span className="diff-tree-title">Lines updated</span>
        <FilesLineStats stats={stats} />
      </div>
      <div className="diff-file-tree files-changed-tree">
        {rows}
      </div>
    </div>
  );
}

function ChangedFilesTreeNode({
  node, depth, relativePath, conflictedFiles, commentMeta = EMPTY_COMMENT_META,
  closedFolders, selectedKey, onToggleFolder, onSelectFile, onOpenPathMenu,
  repoId = '',
}) {
  if (node.kind === 'folder') {
    const isClosed = closedFolders.has(node.key);
    const folderPath = joinRelativePath(relativePath, node.name);
    const childRows = isClosed ? null : node.children.map((child) => (
      <ChangedFilesTreeNode
        key={child.key}
        node={child}
        depth={depth + 1}
        relativePath={folderPath}
        conflictedFiles={conflictedFiles}
        commentMeta={commentMeta}
        closedFolders={closedFolders}
        selectedKey={selectedKey}
        onToggleFolder={onToggleFolder}
        onSelectFile={onSelectFile}
        onOpenPathMenu={onOpenPathMenu}
        repoId={repoId}
      />
    ));
    const chevron = isClosed ? 'chevron-right' : 'chevron-down';
    return (
      <div className="diff-file-tree-group">
        <button
          type="button"
          className="diff-file-tree-row files-changed-tree-row is-folder"
          style={{ '--depth': depth }}
          onClick={() => onToggleFolder(node.key)}
          onContextMenu={(event) => onOpenPathMenu(event, folderPath, repoId)}
        >
          <span className="diff-file-tree-guide" />
          <span className="diff-file-tree-chevron"><Icon name={chevron} /></span>
          <span className="diff-file-tree-label files-changed-tree-folder">
            {node.name}
          </span>
        </button>
        {childRows}
      </div>
    );
  }
  const file = node.file;
  const path = diffDisplayPath(file);
  const kind = file.type || 'modify';
  const selected = selectedKey === changedFileSelectionKey(file);
  const conflicted = fileIsConflictedForFilesTree(file, conflictedFiles);
  const className = [
    'diff-file-tree-row',
    'files-changed-tree-row',
    'is-file',
    `kind-${kind}`,
    selected ? 'selected' : '',
    conflicted ? 'conflicted' : '',
  ].filter(Boolean).join(' ');
  const conflictBadge = conflicted ? (
    <span className="diff-file-row-conflict" aria-label="merge conflict">
      <Icon name="warning" />
    </span>
  ) : null;
  return (
    <button
      type="button"
      className={className}
      style={{ '--depth': depth }}
      data-changed-file-path={path}
      title={`Open ${path} in the centre diff`}
      onClick={() => onSelectFile(file)}
      onContextMenu={(event) => onOpenPathMenu(event, path, repoId)}
    >
      <span className="diff-file-tree-guide" />
      <FilesDiffKindIcon kind={kind} />
      {conflictBadge}
      <span className="diff-file-tree-label files-changed-tree-label">
        {node.name}
      </span>
      <CommentCountBadge count={commentMeta.get(path) || 0} />
      <FilesLineStats stats={node.stats} />
    </button>
  );
}

function Node({
  node, style, onPickFile, onOpenFile, conflictedFiles,
  changedFiles, diffMeta = EMPTY_DIFF_META,
  commentMeta = EMPTY_COMMENT_META, repoId = '', onOpenPathMenu,
}) {
  const isFolder = node.isInternal;
  const relativePath = String(node.data?.relativePath || '');
  const changeMeta = !isFolder ? diffMeta.get(relativePath) : null;
  const commentCount = !isFolder ? (commentMeta.get(relativePath) || 0) : 0;
  function onActivate() {
    // Left-click a FILE: only open it in the editor pane. It must
    // NOT also paste the path into the chat composer — pasting is
    // the explicit RIGHT-click affordance (see onContextMenu).
    if (!isFolder) {
      if (typeof onOpenFile === 'function') {
        onOpenFile({
          absolutePath: String(node.data?.path || ''),
          relativePath: String(node.data?.relativePath || ''),
          repoId,
        });
      }
      return;
    }
    // Folder: expand / collapse.
    activateTreeNode(node);
  }
  function onContextMenu(event) {
    if (typeof onOpenPathMenu !== 'function') { return; }
    onOpenPathMenu(event, relativePath, repoId);
  }
  const isConflicted = !isFolder
    && conflictedFiles
    && conflictedFiles.size > 0
    && conflictedFiles.has(relativePath);
  // A file kato has touched on this branch (committed or not) —
  // same set the Changes tab shows. Conflict wins visually since
  // it's the more urgent signal, so only flag ``changed`` when the
  // file isn't already flagged conflicted.
  const isChanged = !isFolder
    && !isConflicted
    && (
      !!changeMeta
      || (
        changedFiles
        && changedFiles.size > 0
        && changedFiles.has(relativePath)
      )
    );
  const displayChangeMeta = changeMeta || (isChanged
    ? { kind: 'modify', stats: EMPTY_STATS }
    : null);
  // A folder inherits the "changed" tint when it (transitively)
  // holds a file kato touched on this branch — so the ancestor
  // chain lights up all the way up and the operator sees where the
  // edits live without expanding. ``relativePath`` is empty for a
  // synthetic repo root; ``folderContainsChange`` returns false for
  // it (no relative path can start with "/"), which is exactly the
  // "colour up to the root, but NOT the root of all" rule — the
  // repo container (the .files-tab-repo header, not a tree row)
  // never gets the tint.
  const folderChanged = isFolder
    && changedFiles
    && changedFiles.size > 0
    && folderContainsChange(relativePath, changedFiles);
  const rowClass = [
    'tree-row',
    node.isSelected ? 'selected' : '',
    isConflicted ? 'conflicted' : '',
    isChanged ? 'changed' : '',
    folderChanged ? 'changed-ancestor' : '',
  ].filter(Boolean).join(' ');
  const level = Number.isFinite(node.level) ? node.level : 0;
  const rowStyle = { ...style, '--level': level };
  const folderChevron = isFolder ? (
    <span className="tree-row-chevron">
      <Icon name={node.isOpen ? 'chevron-down' : 'chevron-right'} />
    </span>
  ) : null;
  // No generic folder / file icons — the all-files tree must look
  // exactly like the changed-files tree (chevron + name only). Only
  // the change-KIND marker (pencil/＋/－) stays, on changed files,
  // so both trees read identically.
  const fileSpacer = !isFolder ? (
    <span className="tree-row-chevron tree-row-chevron-placeholder" />
  ) : null;
  const fileIcon = !isFolder && displayChangeMeta ? (
    <FilesDiffKindIcon kind={displayChangeMeta.kind} />
  ) : null;
  const conflictBadge = isConflicted ? (
    <span className="tree-row-conflict" aria-label="merge conflict">
      <Icon name="warning" />
    </span>
  ) : null;
  const lineStats = displayChangeMeta ? (
    <FilesLineStats stats={displayChangeMeta.stats} />
  ) : null;
  // Tooltip: spell out left- vs right-click semantics so the
  // right-click affordance is discoverable. Conflict tooltip wins
  // when set since it's the more urgent signal.
  let tooltip;
  if (isConflicted) {
    tooltip = 'Merge conflict — needs resolution';
  } else if (isChanged) {
    tooltip = 'Modified on this task branch — right-click for path options';
  } else if (folderChanged) {
    tooltip = 'Contains files modified on this task branch — right-click for path options';
  } else if (isFolder) {
    tooltip = 'Click to expand · right-click for path options';
  } else {
    tooltip = 'Click to open · right-click for path options';
  }
  return (
    <div
      className={rowClass}
      style={rowStyle}
      onClick={onActivate}
      onContextMenu={onContextMenu}
      title={tooltip}
    >
      <span className="tree-row-level-guides" aria-hidden="true" />
      {folderChevron}
      {fileSpacer}
      {fileIcon}
      {conflictBadge}
      <span className="tree-row-name">{node.data.name}</span>
      <CommentCountBadge count={commentCount} />
      {lineStats}
    </div>
  );
}

export function filterChangedFileTree(nodes, term) {
  const raw = String(term || '').trim().toLowerCase();
  if (!raw) { return nodes || []; }
  const matches = [];
  for (const node of nodes || []) {
    if (node.kind === 'folder') {
      const childMatches = filterChangedFileTree(node.children, raw);
      const folderMatches = String(node.name || '').toLowerCase().includes(raw);
      if (folderMatches || childMatches.length > 0) {
        matches.push({
          ...node,
          children: folderMatches ? node.children : childMatches,
        });
      }
    } else if (changedFileNodeMatches(node, raw)) {
      matches.push(node);
    }
  }
  return matches;
}

function changedFileNodeMatches(node, raw) {
  const name = String(node.name || '').toLowerCase();
  const path = diffDisplayPath(node.file).toLowerCase();
  return name.includes(raw) || path.includes(raw);
}

function changedFileSelectionKey(file) {
  return `${file.type || 'modify'}:${diffDisplayPath(file)}`;
}

function joinRelativePath(parent, child) {
  const left = String(parent || '').replace(/\/+$/, '');
  const right = String(child || '').replace(/^\/+/, '');
  if (!left) { return right; }
  if (!right) { return left; }
  return `${left}/${right}`;
}

function formatRepoRelativePath(repoId, relativePath) {
  const repo = String(repoId || '').trim();
  const path = String(relativePath || '').trim();
  if (!repo) { return path; }
  if (!path) { return repo; }
  return `${repo}:${path}`;
}

function focusTargetMatchesRepo(target, repoTree, repoCount) {
  if (!target) { return false; }
  const targetRepo = String(target.repoId || '').trim();
  if (!targetRepo) { return repoCount === 1; }
  return targetRepo === String(repoTree.repo_id || '').trim()
    || targetRepo === String(repoTree.cwd || '').trim();
}

function findChangedFileFocusInfo(nodes, targetPath, ancestors = []) {
  for (const node of nodes || []) {
    if (node.kind === 'file' && diffDisplayPath(node.file) === targetPath) {
      return { file: node.file, ancestorKeys: ancestors };
    }
    if (node.kind === 'folder') {
      const found = findChangedFileFocusInfo(
        node.children,
        targetPath,
        [...ancestors, node.key],
      );
      if (found) { return found; }
    }
  }
  return null;
}

function fileIsConflictedForFilesTree(file, conflictedSet) {
  if (!conflictedSet || conflictedSet.size === 0) { return false; }
  const oldPath = file.oldPath || '';
  const newPath = file.newPath || '';
  return conflictedSet.has(oldPath) || conflictedSet.has(newPath);
}

function FilesDiffKindIcon({ kind }) {
  const iconName = DIFF_KIND_ICON[kind] || 'edit';
  return (
    <span className={`diff-file-row-kind tree-row-kind kind-${kind || 'modify'}`}>
      <Icon name={iconName} />
    </span>
  );
}

// Bitbucket-style 💬 N on a tree row when the file has open comment
// threads. Renders nothing at 0 so clean files stay clean.
function CommentCountBadge({ count }) {
  if (!count || count < 1) { return null; }
  return (
    <span
      className="tree-row-comments"
      title={`${count} comment thread${count === 1 ? '' : 's'} on this file`}
      aria-label={`${count} comment${count === 1 ? '' : 's'}`}
    >
      <Icon name="comment" />
      {count}
    </span>
  );
}

function FilesLineStats({ stats }) {
  const added = stats?.added > 0 ? (
    <span className="diff-line-stat is-add">{`+${stats.added}`}</span>
  ) : null;
  const deleted = stats?.deleted > 0 ? (
    <span className="diff-line-stat is-delete">{`-${stats.deleted}`}</span>
  ) : null;
  if (!added && !deleted) { return null; }
  return (
    <span className="diff-line-stats tree-row-line-stats">
      {added}
      {deleted}
    </span>
  );
}
