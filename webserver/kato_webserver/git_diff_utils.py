"""Git helpers used by the planning UI's Files / Changes tabs.

The webserver's right pane needs three things from a repo:

* The current branch name (for the branch-safety lock).
* The tracked + untracked file tree (Files tab).
* A unified diff vs the destination branch that includes uncommitted
  modifications and untracked files (Changes tab) — that's the part
  ``git diff origin/master...HEAD`` alone misses.

Pure functions, no Flask. Each one returns ``''``/``[]`` on git failure
so the UI degrades gracefully (empty pane > stack trace).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


# Caps for synthesized "new file" diff hunks (untracked working-tree files
# that have no git index entry yet). Anything bigger gets a placeholder
# instead of dumping megabytes into the diff response.
UNTRACKED_FILE_LINE_LIMIT = 1500
UNTRACKED_FILE_BYTE_LIMIT = 256 * 1024

# Per-file cap for the main ``git diff`` output. A single changed file
# whose diff section exceeds this many lines has its body replaced
# with a one-line notice. Without this, a changeset that touches large
# minified build artifacts (bundled ``*.chunk.js`` / ``main.<hash>.js``)
# returns a multi-megabyte payload that the browser parses + renders
# all at once — the diff pane freezes on "Computing diff…". The file
# still appears in the tree with its real path; the operator opens it
# in the editor pane to see the full content.
#
# Both a line cap AND a byte cap are needed: minified bundles are
# often a HANDFUL of lines that are each hundreds of KB, so a
# line-count check alone would wave a multi-megabyte single-line diff
# straight through.
TRACKED_FILE_DIFF_LINE_LIMIT = 2000
TRACKED_FILE_DIFF_BYTE_LIMIT = 128 * 1024


def run_git(cwd: str, args: list[str], *, timeout: float) -> str | None:
    """Run ``git -C <cwd> <args>`` and return stdout, or None on any failure.

    Returning ``None`` rather than ``''`` lets callers tell "git failed"
    apart from "git ran and the answer was empty".
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ['git', '-C', cwd, *args],
            capture_output=True,
            text=True,
            # Pin UTF-8 so a smart-quote in a commit message or a
            # branch name doesn't blow up the stdout reader thread
            # with ``UnicodeDecodeError`` on Windows (default cp1252).
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def current_branch(cwd: str) -> str:
    """Abbreviated HEAD ref of ``cwd``, or '' on failure."""
    out = run_git(cwd, ['rev-parse', '--abbrev-ref', 'HEAD'], timeout=5)
    return out.strip() if out is not None else ''


def local_branch_exists(cwd: str, branch: str) -> bool:
    """True when a local ref named ``branch`` exists in ``cwd``."""
    if not branch:
        return False
    return run_git(
        cwd, ['rev-parse', '--verify', f'refs/heads/{branch}'], timeout=5,
    ) is not None


def remote_branch_exists(cwd: str, branch: str, remote: str = 'origin') -> bool:
    """True when ``<remote>/<branch>`` exists in ``cwd``."""
    if not branch:
        return False
    return run_git(
        cwd, ['rev-parse', '--verify', f'refs/remotes/{remote}/{branch}'],
        timeout=5,
    ) is not None


def ensure_branch_checked_out(cwd: str, branch: str) -> bool:
    """Best-effort: checkout ``branch`` in ``cwd`` when not already on it.

    A per-task workspace clone is supposed to live on the task branch.
    If it has drifted to ``master`` (e.g. because the previous kato
    session crashed mid-publish), this restores it. Tries the local
    branch first; falls back to ``origin/<branch>`` if no local ref
    exists yet (clone-checkout-fail path). Returns True iff the
    workspace ends up on ``branch`` after the call. Non-destructive:
    if the working tree is dirty and checkout would clobber, git
    refuses and we return False without forcing.
    """
    if not branch:
        return False
    if current_branch(cwd) == branch:
        return True
    if local_branch_exists(cwd, branch):
        if run_git(cwd, ['checkout', branch], timeout=15) is None:
            return False
    elif remote_branch_exists(cwd, branch):
        if run_git(
            cwd, ['checkout', '-b', branch, f'origin/{branch}'], timeout=15,
        ) is None:
            return False
    else:
        return False
    return current_branch(cwd) == branch


def detect_default_branch(cwd: str) -> str:
    """Repo's default branch as published by the remote, or '' on failure.

    This is a *fallback* used by the diff endpoint when the kato
    config has no ``destination_branch`` for the repo. It is NOT
    the right answer for diffing a kato task branch — kato always
    forks a task off the configured ``destination_branch`` for
    that repo, which may not be the remote's default. Probing
    ``origin/main`` or ``origin/master`` blindly produced wrong
    diffs (the operator saw hundreds of unrelated commits because
    the task base was ``develop``); we used to do that and stopped.

    Resolution order:

    1. ``git symbolic-ref refs/remotes/origin/HEAD`` — works when
       the local clone has its HEAD ref set (the common case).
    2. ``git ls-remote --symref origin HEAD`` — asks the remote
       directly. Works even when step 1 returns nothing because
       the workspace clone never had ``origin/HEAD`` set.

    Empty string means we could not determine the remote default
    — the caller surfaces a precise error so the operator can fix
    the config rather than silently picking a wrong base.
    """
    return _branch_from_local_head(cwd) or _branch_from_ls_remote(cwd)


def _branch_from_local_head(cwd: str) -> str:
    """Read ``refs/remotes/origin/HEAD`` if the clone has it set."""
    out = run_git(
        cwd, ['symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], timeout=5,
    )
    if out is None:
        return ''
    ref = out.strip()
    return ref.split('/', 1)[1] if '/' in ref else ref


def _branch_from_ls_remote(cwd: str) -> str:
    """Ask the remote what HEAD points at, via ``git ls-remote --symref``.

    Output format::

        ref: refs/heads/develop\\tHEAD
        <sha>\\tHEAD

    Independent of the local clone's HEAD ref state — works even
    when the local clone never set ``refs/remotes/origin/HEAD``,
    which is the case kato hit in production with Bitbucket repos
    whose default branch is ``develop``.
    """
    out = run_git(cwd, ['ls-remote', '--symref', 'origin', 'HEAD'], timeout=10)
    if out is None:
        return ''
    for line in out.splitlines():
        if not line.startswith('ref:'):
            continue
        # ``ref: refs/heads/<branch>\tHEAD`` → grab the branch name.
        ref_part = line.split('\t', 1)[0]
        if ':' not in ref_part:  # pragma: no cover - defensive; ``line`` starts with ``ref:`` so the prefix always contains ':'.
            continue
        ref = ref_part.split(':', 1)[1].strip()
        prefix = 'refs/heads/'
        if ref.startswith(prefix):
            return ref[len(prefix):]
        return ref
    return ''


def tracked_file_tree(cwd: str) -> list[dict[str, Any]]:
    """Tracked + untracked-but-not-ignored files as a nested tree.

    Uses ``git ls-files --cached --others --exclude-standard`` so the tree
    matches what a developer sees in their editor.
    """
    out = run_git(
        cwd,
        ['ls-files', '--cached', '--others', '--exclude-standard'],
        timeout=15,
    )
    if out is None:
        return []
    paths = sorted({line.strip() for line in out.splitlines() if line.strip()})
    return _paths_to_tree(paths)


def conflicted_paths(cwd: str) -> list[str]:
    """Return repo-relative paths of files with unmerged (conflicted) entries.

    ``git ls-files --unmerged`` emits one line per conflicted-stage
    entry — typically three per file (stages 1/2/3). We dedupe by
    path and sort for stable output.

    Empty list when the repo has no conflicts (the common case),
    when the directory isn't a git repo, or when ``git`` isn't on
    PATH. Best-effort: a failure here must not block the diff
    payload from rendering.
    """
    output = run_git(cwd, ['ls-files', '--unmerged'], timeout=10)
    if not output:
        return []
    paths: set[str] = set()
    for line in output.splitlines():
        # Format: ``<mode> <hash> <stage>\t<path>``
        if '\t' not in line:
            continue
        path = line.split('\t', 1)[1].strip()
        if path:
            paths.add(path)
    return sorted(paths)


def _diff_base(cwd: str, base_ref: str) -> str:
    """The ref the working tree is diffed against: ``merge-base(base_ref, HEAD)``.

    ``base_ref`` is the current *tip* of the destination branch (e.g.
    ``origin/master``). Diffing the working tree straight against it is
    two-dot semantics, so the moment ``master`` advances past the task
    branch's fork point, every file added to ``master`` *after* the fork
    shows up as a phantom DELETION — the operator saw thousands of
    deleted lines (migrations, services) that weren't in the PR at all.

    The PR uses three-dot ``base...HEAD`` (i.e. the merge-base). Anchoring
    on the merge-base here makes the Changes tab / Files tree agree with
    the PR, while ``git diff <merge-base>`` still spans merge-base →
    working tree so uncommitted work stays visible.

    Falls back to ``base_ref`` when there is no common ancestor (unrelated
    histories) or git cannot resolve the merge-base — no worse than the
    old tip-diff behaviour in that corner case.
    """
    if not cwd or not base_ref:
        return base_ref
    out = run_git(cwd, ['merge-base', base_ref, 'HEAD'], timeout=10)
    return (out or '').strip() or base_ref


def changed_paths(cwd: str, base_ref: str) -> list[str]:
    """Repo-relative paths that differ from ``base_ref``.

    Same coverage as the Changes-tab diff (``diff_against_base``) so
    the Files tree and the Changes tab agree on "what changed":

      * ``git diff --name-only <merge-base>`` — tracked files with
        committed OR uncommitted edits since the branch forked
        (merge-base of ``base_ref`` and HEAD — see ``_diff_base`` for
        why the tip of ``base_ref`` is the wrong anchor);
      * ``git ls-files --others --exclude-standard`` — untracked,
        non-ignored files Claude just wrote (not yet in the index,
        so the diff above misses them).

    Best-effort: an empty list on any git failure (no upstream,
    bad base ref, git not on PATH) — the tree just renders without
    change colouring rather than erroring.
    """
    if not cwd or not base_ref:
        return []
    paths: set[str] = set()
    tracked = run_git(cwd, ['diff', '--name-only', _diff_base(cwd, base_ref)], timeout=20)
    if tracked:
        for line in tracked.splitlines():
            path = line.strip()
            if path:
                paths.add(path)
    untracked = run_git(
        cwd, ['ls-files', '--others', '--exclude-standard'], timeout=15,
    )
    if untracked:
        for line in untracked.splitlines():
            path = line.strip()
            if path:
                paths.add(path)
    return sorted(paths)


def list_branch_commits(
    cwd: str,
    base_ref: str,
    *,
    limit: int = 50,
) -> list[dict]:
    """Recent commits on HEAD ahead of ``base_ref``, newest first.

    Returns one ``{sha, short_sha, subject, author, epoch}`` dict
    per commit. Drives the Files-tab "view changes from commit"
    dropdown — the operator picks a commit and the UI shows only
    that commit's diff. Empty list on any failure (no upstream,
    detached HEAD, malformed log output) — the dropdown just
    renders empty in that case rather than spamming an error.

    ``--no-merges`` because merge commits don't represent kato's
    own work; the operator's mental model is "what did kato
    change", and merges are bookkeeping. ``--max-count`` keeps
    the dropdown scannable even on long-running task branches.
    """
    if not cwd or not base_ref:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    fmt = '%H%x09%h%x09%ct%x09%an%x09%s'
    out = run_git(
        cwd,
        [
            'log',
            f'--max-count={bounded_limit}',
            '--no-merges',
            f'--pretty=format:{fmt}',
            f'{base_ref}..HEAD',
        ],
        timeout=15,
    )
    if not out:
        return []
    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split('\t', 4)
        if len(parts) < 5:
            continue
        sha, short_sha, epoch_text, author, subject = parts
        try:
            epoch = float(epoch_text)
        except ValueError:
            epoch = 0.0
        commits.append({
            'sha': sha.strip(),
            'short_sha': short_sha.strip(),
            'epoch': epoch,
            'author': author.strip(),
            'subject': subject.strip(),
        })
    return commits


def diff_for_commit(cwd: str, sha: str) -> str:
    """Unified diff for a single commit's changes.

    Equivalent to ``git show --no-color <sha>`` minus the leading
    commit header — we want the file-by-file diff payload only,
    so the existing react-diff-view ``parseDiff`` can render it
    the same way it renders the branch-vs-base diff.
    """
    safe_sha = str(sha or '').strip()
    if not cwd or not safe_sha:
        return ''
    return run_git(
        cwd,
        ['show', '--no-color', '--pretty=format:', safe_sha],
        timeout=30,
    ) or ''


def blob_size_at_ref(cwd: str, ref: str, path: str) -> int | None:
    """Size of ``path`` at ``ref``, or None when git cannot read it."""
    safe_path = str(path or '').strip().lstrip('/')
    safe_ref = str(ref or '').strip()
    if not cwd or not safe_ref or not safe_path:
        return None
    out = run_git(cwd, ['cat-file', '-s', f'{safe_ref}:{safe_path}'], timeout=10)
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def file_text_at_ref(cwd: str, ref: str, path: str) -> str | None:
    """Text content of ``path`` at ``ref``, or None when git cannot read it."""
    safe_path = str(path or '').strip().lstrip('/')
    safe_ref = str(ref or '').strip()
    if not cwd or not safe_ref or not safe_path:
        return None
    return run_git(cwd, ['show', f'{safe_ref}:{safe_path}'], timeout=15)


def diff_against_base(cwd: str, base_ref: str) -> str:
    """Unified diff that surfaces committed AND uncommitted work vs ``base_ref``.

    The Changes tab is the single source of truth the user looks at while
    chatting — they want to see what Claude has done so far, regardless
    of whether it's been committed yet. We union three things:

      * ``git diff <merge-base>`` — working tree (tracked + staged) vs the
        merge-base of ``base_ref`` and HEAD (NOT the destination tip — see
        ``_diff_base``). Catches both committed and uncommitted edits in
        one call, and matches the PR's three-dot diff so master advancing
        past the fork point doesn't surface phantom deletions.
      * Untracked-but-not-ignored files — Claude's freshly-written files
        won't appear in the diff above until they're added to the index,
        so we synthesize one ``new file`` hunk per untracked path.
      * Large untracked files get a placeholder hunk instead of dumping
        megabytes into the response.
    """
    main_diff = run_git(cwd, ['diff', _diff_base(cwd, base_ref)], timeout=30) or ''
    return _elide_oversized_file_diffs(main_diff) + _untracked_files_as_diff(cwd)


def _elide_oversized_file_diffs(diff_text: str) -> str:
    """Replace any single file's huge diff body with a short notice.

    ``git diff`` is a concatenation of per-file sections, each starting
    with ``diff --git a/… b/…``. A changeset that rewrites large
    minified bundles produces sections tens of thousands of lines long;
    shipping them all freezes the browser diff pane. We keep every
    section's HEADER (the ``diff --git`` / ``index`` / mode / ``---`` /
    ``+++`` lines — so react-diff-view still resolves the path and the
    add/delete/modify kind) and, when the section is over the line cap,
    swap its hunks for one context-line hunk. A context line (leading
    space) parses safely for add, delete and modify alike and renders
    as a single neutral informational row — no false +/- counts.
    """
    if not diff_text:
        return diff_text
    lines = diff_text.split('\n')
    sections: list[list[str]] = []
    for line in lines:
        if line.startswith('diff --git ') or not sections:
            sections.append([])
        sections[-1].append(line)
    rebuilt: list[str] = []
    for section in sections:
        oversized = (
            len(section) > TRACKED_FILE_DIFF_LINE_LIMIT
            # +1 per line for the '\n' the join re-adds.
            or sum(len(ln) + 1 for ln in section) > TRACKED_FILE_DIFF_BYTE_LIMIT
        )
        if (
            not oversized
            or not section
            or not section[0].startswith('diff --git ')
        ):
            rebuilt.extend(section)
            continue
        hunk_start = next(
            (i for i, ln in enumerate(section) if ln.startswith('@@ ')),
            None,
        )
        if hunk_start is None:
            # No hunk (rename-only / binary stub) — leave it untouched.
            rebuilt.extend(section)
            continue
        header = section[:hunk_start]
        body_bytes = sum(len(ln) + 1 for ln in section[hunk_start:])
        notice = (
            f'(diff too large to display: ~{body_bytes // 1024 or 1} KB '
            f'across {len(section) - hunk_start} hunk lines elided — '
            f'open the file in the editor pane to view the full change)'
        )
        rebuilt.extend(header)
        rebuilt.append('@@ -1 +1 @@')
        rebuilt.append(f' {notice}')
    return '\n'.join(rebuilt)


# ----- internals -----


def _paths_to_tree(paths: list[str]) -> list[dict[str, Any]]:
    root: dict[str, dict[str, Any]] = {}
    for path in paths:
        parts = path.split('/')
        cursor = root
        for index, part in enumerate(parts):
            is_leaf = index == len(parts) - 1
            entry = cursor.setdefault(
                part,
                {
                    'name': part,
                    'path': '/'.join(parts[: index + 1]),
                    'children': None if is_leaf else {},
                },
            )
            if not is_leaf:
                cursor = entry['children']
    return _materialize_tree(root)


def _materialize_tree(level: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in level.values():
        item = {'name': entry['name'], 'path': entry['path']}
        if entry['children'] is not None:
            item['children'] = _materialize_tree(entry['children'])
        items.append(item)
    items.sort(key=lambda item: ('children' not in item, item['name']))
    return items


def _untracked_files_as_diff(cwd: str) -> str:
    out = run_git(
        cwd,
        ['ls-files', '--others', '--exclude-standard'],
        timeout=15,
    )
    if not out:
        return ''
    chunks: list[str] = []
    for line in out.splitlines():
        path = line.strip()
        if path:
            chunks.append(_synthesize_new_file_hunk(cwd, path))
    return ''.join(chunks)


def _synthesize_new_file_hunk(cwd: str, relative_path: str) -> str:
    full_path = Path(cwd) / relative_path
    header = (
        f'diff --git a/{relative_path} b/{relative_path}\n'
        'new file mode 100644\n'
        '--- /dev/null\n'
        f'+++ b/{relative_path}\n'
    )
    try:
        size = full_path.stat().st_size
    except OSError:
        return header + '@@ -0,0 +1 @@\n+(unreadable)\n'
    if size > UNTRACKED_FILE_BYTE_LIMIT:
        return header + (
            f'@@ -0,0 +1 @@\n'
            f'+(file too large to preview: {size} bytes)\n'
        )
    try:
        text = full_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return header + '@@ -0,0 +1 @@\n+(binary file — open in editor)\n'
    lines = text.splitlines()
    truncated = len(lines) > UNTRACKED_FILE_LINE_LIMIT
    if truncated:
        lines = lines[:UNTRACKED_FILE_LINE_LIMIT]
    body_lines = [f'+{line}' for line in lines]
    if truncated:
        body_lines.append(f'+(... truncated at {UNTRACKED_FILE_LINE_LIMIT} lines)')
    body = '\n'.join(body_lines) + '\n' if body_lines else '+\n'
    hunk_header = f'@@ -0,0 +1,{len(body_lines)} @@\n'
    return header + hunk_header + body
