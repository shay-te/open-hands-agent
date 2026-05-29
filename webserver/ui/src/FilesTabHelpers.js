import { basenameOf } from './utils/basenameOf.js';

export function normalizeTrees(payload) {
  const trees = Array.isArray(payload?.trees) ? payload.trees : null;
  if (trees && trees.length > 0) {
    return trees.map((entry) => {
      const cwd = String(entry?.cwd || '');
      const conflicts = Array.isArray(entry?.conflicted_files)
        ? entry.conflicted_files.map(String)
        : [];
      const changed = Array.isArray(entry?.changed_files)
        ? entry.changed_files.map(String)
        : [];
      return {
        repo_id: String(entry?.repo_id || '') || basenameOf(cwd),
        cwd,
        tree: entry?.tree || [],
        conflictedFiles: new Set(conflicts),
        changedFiles: new Set(changed),
      };
    });
  }
  const legacyCwd = String(payload?.cwd || '');
  const legacyConflicts = Array.isArray(payload?.conflicted_files)
    ? payload.conflicted_files.map(String)
    : [];
  const legacyChanged = Array.isArray(payload?.changed_files)
    ? payload.changed_files.map(String)
    : [];
  return [{
    repo_id: basenameOf(legacyCwd),
    cwd: legacyCwd,
    tree: payload?.tree || [],
    conflictedFiles: new Set(legacyConflicts),
    changedFiles: new Set(legacyChanged),
  }];
}

export function attachIds(nodes, cwd = '') {
  if (!Array.isArray(nodes)) { return []; }
  return nodes.map((node) => {
    const next = {
      ...node,
      id: node.path,
      relativePath: relativePathForRepo(node.path, cwd),
    };
    if (Array.isArray(node.children)) {
      next.children = attachIds(node.children, cwd);
    }
    return next;
  });
}

// True when ``changedFiles`` holds a path that lives inside the
// folder at ``folderRelativePath`` (the folder itself or any
// descendant). Used to tint the whole ancestor chain of a changed
// file in the tree.
//
// An empty / falsy ``folderRelativePath`` is the synthetic repo
// root — it returns ``false`` so the root of all is never tinted
// ("colour up to the root, but not the root of all"). No relative
// file path starts with "/", so prefixing with the folder + "/"
// also naturally can't match against an empty root path.
export function folderContainsChange(folderRelativePath, changedFiles) {
  const folder = String(folderRelativePath || '');
  if (!folder) { return false; }
  if (!changedFiles || typeof changedFiles.forEach !== 'function') {
    return false;
  }
  const prefix = folder + '/';
  for (const changed of changedFiles) {
    const path = String(changed || '');
    if (path === folder || path.startsWith(prefix)) { return true; }
  }
  return false;
}

// Left-click activation. Folders expand/collapse. Files are a
// no-op here on purpose: a left-click on a file only OPENS it in
// the editor pane (handled by the caller via ``onOpenFile``) — it
// must NOT also paste the path into the chat composer. Pasting a
// path is the explicit RIGHT-click affordance instead.
export function activateTreeNode(node) {
  if (node.isInternal) {
    node.toggle();
  }
}

// Lenient, VS-Code / Cmd+P-style fuzzy match for the file search.
//
// Matching is checked against BOTH the basename and the full
// relative path, and succeeds if EITHER:
//
//   1. a plain case-insensitive substring hit (fast path — also
//      what makes "src/auth" find ``src/auth.py`` and "" match
//      everything), OR
//   2. a separator-insensitive subsequence: lowercase both sides,
//      strip every non-alphanumeric character, then check the
//      query's characters appear IN ORDER in the target.
//
// (2) is what makes the search forgiving the way the operator
// expects:
//   * "fileservice"  → matches ``file_service.py``  (underscore /
//     dot / dash / slash differences don't matter)
//   * "tmpd" / "TMPD" → matches ``TestMePleaseDude`` (initialism /
//     camel-hump pickup falls out of subsequence-over-alnum)
//   * "authpy"        → matches ``src/auth.py``      (ends-with /
//     contains, not just starts-with)
//
// Empty / whitespace-only term matches everything. Folders only
// need to match themselves — react-arborist already keeps the
// ancestors of any matching descendant visible.
export function matchTreeNode(node, term) {
  const raw = String(term || '').trim().toLowerCase();
  if (!raw) { return true; }
  const data = node?.data || {};
  const name = String(data.name || '').toLowerCase();
  const relativePath = String(data.relativePath || '').toLowerCase();

  // 1) Substring fast path — preserves the exact "type the path"
  //    behaviour (``src/auth``) and is the cheapest common case.
  if (name.includes(raw) || relativePath.includes(raw)) { return true; }

  // 2) Separator-insensitive subsequence over alphanumerics.
  const needle = raw.replace(/[^a-z0-9]/g, '');
  if (!needle) { return true; }
  return _isSubsequence(needle, name.replace(/[^a-z0-9]/g, ''))
    || _isSubsequence(needle, relativePath.replace(/[^a-z0-9]/g, ''));
}

// True when every character of ``needle`` appears in ``haystack``
// in order (not necessarily contiguously). O(haystack) and
// allocation-free — runs per node on every keystroke.
function _isSubsequence(needle, haystack) {
  if (!needle) { return true; }
  if (needle.length > haystack.length) { return false; }
  let i = 0;
  for (let j = 0; j < haystack.length && i < needle.length; j += 1) {
    if (haystack[j] === needle[i]) { i += 1; }
  }
  return i === needle.length;
}

function relativePathForRepo(path, cwd) {
  const normalizedPath = String(path || '').replace(/\\/g, '/');
  const normalizedCwd = String(cwd || '').replace(/\\/g, '/').replace(/\/+$/, '');
  const cwdPrefix = normalizedCwd + '/';
  if (normalizedCwd && normalizedPath.startsWith(cwdPrefix)) {
    return normalizedPath.slice(cwdPrefix.length);
  }
  return normalizedPath.replace(/^\/+/, '');
}
