// Pure diff model + tree helpers. These used to live in
// ``ChangesTab.jsx`` alongside the now-removed Changes-tab React
// component. The component and its tab were deleted; the pure logic
// it shared with FilesTab / DiffPane / DiffFileWithComments lives
// here so those keep one source of truth for "what a diff is".
//
// No React, no hooks, no JSX — only data shaping. The
// ``react-diff-view`` stylesheet is imported here as a side effect
// because ChangesTab was the module that used to load it; FilesTab
// and DiffPane import this module, so the diff CSS stays available
// in exactly the contexts it was before.
import { parseDiff } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { basenameOf } from './utils/basenameOf.js';

export function diffLabelForStatus(status) {
  switch (String(status || '').toLowerCase()) {
    case 'review':
      return 'already pushed (PR open)';
    case 'done':
      return 'merged';
    case 'errored':
      return 'publish errored';
    case 'terminated':
      return 'terminated';
    default:
      return '';
  }
}

// Shape the wire payload into a uniform per-repo list. Handles both the
// new ``diffs: [...]`` envelope and the legacy single-repo flat shape.
// Exported so the centre DiffPane parses the /diff payload exactly
// the way the file tree does (same cached react-diff-view parse,
// same repo/file shape) — one source of truth for "what a diff is".
export function parseRepoDiffs(payload) {
  const diffs = Array.isArray(payload?.diffs) ? payload.diffs : null;
  if (diffs && diffs.length > 0) {
    return diffs.map((entry) => normalizeDiff(entry));
  }
  return [normalizeDiff(payload)];
}

// Cache parsed diffs by (repo, raw bytes). Auto-poll fires every 5s
// while the tab is open; on an idle workspace the diff bytes are
// unchanged across ticks, so reparsing them via ``parseDiff`` is
// pure waste. Keying on ``repoId|raw`` collapses identical-payload
// polls to a Map lookup. The cache is bounded to the most recent
// few entries per repo so a long-lived tab with churning diffs
// doesn't leak memory.
const PARSED_DIFF_CACHE = new Map();
const PARSED_DIFF_CACHE_MAX = 32;
function parseDiffCached(repoId, raw) {
  const key = `${repoId}|${raw.length}|${raw}`;
  const hit = PARSED_DIFF_CACHE.get(key);
  if (hit) { return hit; }
  const parsed = parseDiff(raw);
  PARSED_DIFF_CACHE.set(key, parsed);
  if (PARSED_DIFF_CACHE.size > PARSED_DIFF_CACHE_MAX) {
    // Drop the oldest entry. Map preserves insertion order.
    const oldestKey = PARSED_DIFF_CACHE.keys().next().value;
    PARSED_DIFF_CACHE.delete(oldestKey);
  }
  return parsed;
}

function normalizeDiff(entry) {
  const raw = String(entry?.diff || '');
  const cwd = String(entry?.cwd || '');
  // Older server responses don't carry repo_id; derive from the cwd's
  // last path segment so the accordion still has a meaningful heading.
  const repoId = String(entry?.repo_id || '') || basenameOf(cwd);
  const conflicts = Array.isArray(entry?.conflicted_files)
    ? entry.conflicted_files.map(String)
    : [];
  return {
    repo_id: repoId,
    cwd,
    base: String(entry?.base || ''),
    head: String(entry?.head || ''),
    error: String(entry?.error || ''),
    files: raw ? parseDiffCached(repoId, raw) : [],
    conflictedFiles: new Set(conflicts),
  };
}

// Re-exported (defined in ``utils/basenameOf.js``) so existing
// importers + the diffModel test keep their ``diffModel.js`` import.
export { basenameOf };

export function diffFileKey(file) {
  const oldPath = file.oldPath || '';
  const newPath = file.newPath || '';
  return `${file.type}:${oldPath}->${newPath}`;
}

// ``repo:path`` label for clipboard / chat references. Drops the
// ``repo:`` prefix when there is no repo id (single-repo workspace)
// and falls back to the repo id alone when no path is supplied.
// Shared by FilesTab and DiffFileWithComments so both produce the
// exact same relative-path string.
export function formatRepoRelativePath(repoId, relativePath) {
  const repo = String(repoId || '').trim();
  const path = String(relativePath || '').trim();
  if (!repo) { return path; }
  if (!path) { return repo; }
  return `${repo}:${path}`;
}

// True when either side of a diff file appears in the conflicted-set.
// Empty / missing sets short-circuit to false. Shared by the file
// tree (FilesTab) and the stacked diff pane so both flag the same
// rows as conflicted.
export function isFileConflicted(file, conflictedSet) {
  if (!conflictedSet || conflictedSet.size === 0) { return false; }
  const oldPath = file.oldPath || '';
  const newPath = file.newPath || '';
  return conflictedSet.has(oldPath) || conflictedSet.has(newPath);
}

// The real, human path of a diff entry. react-diff-view sets the
// missing side to ``/dev/null`` for pure add/delete — a deleted file
// must show its OLD path, not ``/dev/null`` (the screenshot bug:
// every deleted row read "/dev/null"). Exported for tests + reused
// by DiffPane / DiffFileWithComments so list rows and centre anchors
// agree on the path.
export function diffDisplayPath(file) {
  const real = (p) => (p && p !== '/dev/null' ? p : '');
  const newP = real(file.newPath);
  const oldP = real(file.oldPath);
  if (file.type === 'delete') { return oldP || newP || '(unknown)'; }
  return newP || oldP || '(unknown)';
}

// The ``openFile`` payload a changed-file row hands to the centre
// pane. DiffPane (via App.handleOpenFile) needs an ABSOLUTE path; the
// repo's cwd joined with the diff-relative path is it. Exported so
// the join/edge logic is unit-tested without rendering the accordion.
export function changedFileOpenTarget(repoDiff, file) {
  const path = diffDisplayPath(file);
  const cwd = String(repoDiff.cwd || '').replace(/\/+$/, '');
  return {
    absolutePath: cwd ? `${cwd}/${path}` : path,
    relativePath: path,
    repoId: repoDiff.repo_id,
    view: 'diff',
  };
}

export function countFileChangeStats(file) {
  const stats = { added: 0, deleted: 0 };
  for (const hunk of file?.hunks || []) {
    for (const change of hunk?.changes || []) {
      if (change?.type === 'insert' || change?.isInsert) {
        stats.added += 1;
      } else if (change?.type === 'delete' || change?.isDelete) {
        stats.deleted += 1;
      }
    }
  }
  return stats;
}

export function buildDiffFileTree(files) {
  const root = _createFolderNode('');
  for (const file of files || []) {
    const path = diffDisplayPath(file);
    const parts = path.split('/').filter(Boolean);
    const fileName = parts.pop() || path;
    let folder = root;
    const folderStack = [root];
    for (const part of parts) {
      folder = _ensureFolder(folder, part);
      folderStack.push(folder);
    }
    const stats = countFileChangeStats(file);
    folder.children.push({
      kind: 'file',
      key: diffFileKey(file),
      name: fileName,
      file,
      stats,
    });
    for (const item of folderStack) {
      _addStats(item, stats);
    }
  }
  _sortTree(root);
  return {
    nodes: root.children.map((node) => _compressFolder(node)),
    stats: root.stats,
  };
}

function _createFolderNode(name) {
  return {
    kind: 'folder',
    key: `folder:${name}`,
    name,
    children: [],
    stats: { added: 0, deleted: 0 },
  };
}

function _ensureFolder(parent, name) {
  let folder = parent.children.find((child) => (
    child.kind === 'folder' && child.name === name
  ));
  if (!folder) {
    folder = _createFolderNode(name);
    folder.key = `${parent.key}/${name}`;
    parent.children.push(folder);
  }
  return folder;
}

function _addStats(folder, stats) {
  folder.stats.added += stats.added;
  folder.stats.deleted += stats.deleted;
}

function _sortTree(folder) {
  folder.children.sort((a, b) => {
    if (a.kind !== b.kind) {
      return a.kind === 'folder' ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });
  for (const child of folder.children) {
    if (child.kind === 'folder') {
      _sortTree(child);
    }
  }
}

function _compressFolder(node) {
  if (node.kind !== 'folder') { return node; }
  const compressedChildren = node.children.map((child) => _compressFolder(child));
  const next = {
    ...node,
    children: compressedChildren,
  };
  if (next.children.length === 1 && next.children[0].kind === 'folder') {
    const child = next.children[0];
    return {
      ...child,
      key: `${next.key}/${child.key}`,
      name: `${next.name}/${child.name}`,
    };
  }
  return next;
}
