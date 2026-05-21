"""Real-git companion for GitClientMixin (NO MOCKS).

The existing ``test_git_client.py`` patches ``_run_git`` to avoid
shelling out to the git binary — fast, but it can't catch a bug
where the git arguments are wrong, the branch-ref parsing
misreads real output, or a future git release changes a flag.

This file does the opposite: spins up real bare + working git
repos in a tempdir, commits real files, branches, and pushes —
then exercises GitClientMixin methods against them. Hermetic
(no shared paths, no shared remotes) so the tests are
parallel-safe and don't depend on the operator's home git
config.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _GitOnly(GitClientMixin):
    """Concrete subclass — mixin needs a ``logger`` from its host class."""

    def __init__(self) -> None:
        import logging
        self.logger = logging.getLogger('GitClientMixinRealTest')


def _git_env() -> dict:
    return {
        **os.environ,
        'GIT_AUTHOR_NAME': 'real-git-test',
        'GIT_AUTHOR_EMAIL': 'real@git.test',
        'GIT_COMMITTER_NAME': 'real-git-test',
        'GIT_COMMITTER_EMAIL': 'real@git.test',
    }


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), env=_git_env(),
        check=check, capture_output=True, text=True,
    )


@unittest.skipUnless(
    shutil.which('git'), 'git binary not available on this system',
)
class GitClientMixinRealRepoTests(unittest.TestCase):
    """Each test gets a fresh bare origin + working clone on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='git-mixin-real-')
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.origin = root / 'origin.git'
        self.work = root / 'work'
        self.origin.mkdir()
        self.work.mkdir()
        _git(self.origin, 'init', '--bare', '--initial-branch', 'main')
        _git(self.work, 'init', '--initial-branch', 'main')
        _git(self.work, 'remote', 'add', 'origin', str(self.origin))
        (self.work / 'README.md').write_text(
            'initial\n', encoding='utf-8',
        )
        _git(self.work, 'add', 'README.md')
        _git(self.work, 'commit', '-m', 'baseline')
        _git(self.work, 'push', '-u', 'origin', 'main')
        self.client = _GitOnly()

    # ----- _current_branch -----

    def test_current_branch_returns_real_checked_out_branch(self) -> None:
        self.assertEqual(self.client._current_branch(str(self.work)), 'main')
        _git(self.work, 'checkout', '-b', 'feature/x')
        self.assertEqual(
            self.client._current_branch(str(self.work)), 'feature/x',
        )

    def test_current_branch_with_unusual_name_is_preserved_verbatim(self) -> None:
        # Names with slashes and uppercase — git accepts them; the
        # parser must return them verbatim, not lower-cased or split.
        _git(self.work, 'checkout', '-b', 'PROJ-9/My-Feature')
        self.assertEqual(
            self.client._current_branch(str(self.work)),
            'PROJ-9/My-Feature',
        )

    # ----- _working_tree_status -----

    def test_working_tree_status_empty_when_clean(self) -> None:
        # Clean tree → empty porcelain output.
        status = self.client._working_tree_status(str(self.work))
        self.assertEqual(status.strip(), '')

    def test_working_tree_status_surfaces_modified_file(self) -> None:
        (self.work / 'README.md').write_text('changed\n', encoding='utf-8')
        status = self.client._working_tree_status(str(self.work))
        self.assertIn('README.md', status)
        # Modified file shows ' M' or 'M ' in porcelain v1.
        self.assertRegex(status, r'M.*README\.md')

    def test_working_tree_status_surfaces_untracked_file(self) -> None:
        (self.work / 'new.txt').write_text('hi', encoding='utf-8')
        status = self.client._working_tree_status(str(self.work))
        self.assertIn('new.txt', status)
        self.assertRegex(status, r'\?\?.*new\.txt')

    # ----- _git_reference_exists -----

    def test_git_reference_exists_for_local_branch(self) -> None:
        self.assertTrue(
            self.client._git_reference_exists(str(self.work), 'main'),
        )
        self.assertFalse(
            self.client._git_reference_exists(
                str(self.work), 'never-existed',
            ),
        )

    def test_git_reference_exists_for_remote_tracking_branch(self) -> None:
        # After ``push -u``, ``origin/main`` is a real remote-tracking
        # ref — the inventory + diff paths rely on this resolving.
        self.assertTrue(
            self.client._git_reference_exists(str(self.work), 'origin/main'),
        )

    # ----- _ahead_count + _left_right_commit_counts -----

    def test_ahead_count_zero_immediately_after_push(self) -> None:
        # Fresh clone in sync with origin → 0 ahead.
        # Signature: _ahead_count(local_path, comparison_ref, branch_name)
        # → counts commits in branch_name not in comparison_ref.
        ahead = self.client._ahead_count(
            str(self.work), 'origin/main', 'main',
        )
        self.assertEqual(ahead, 0)

    def test_ahead_count_reflects_real_local_commits(self) -> None:
        # Add three local commits — ahead must be 3.
        for i in range(3):
            (self.work / f'file{i}.txt').write_text(
                str(i), encoding='utf-8',
            )
            _git(self.work, 'add', f'file{i}.txt')
            _git(self.work, 'commit', '-m', f'local commit {i}')
        ahead = self.client._ahead_count(
            str(self.work), 'origin/main', 'main',
        )
        self.assertEqual(ahead, 3)

    def test_left_right_commit_counts_real_divergence(self) -> None:
        # Diverge: local has 2 commits, origin has 1 different commit
        # not on local. left=2 (local ahead), right=1 (local behind).
        # Build a second clone to act as the "another developer pushed".
        other = self.work.parent / 'other'
        other.mkdir()
        _git(other, 'init', '--initial-branch', 'main')
        _git(other, 'remote', 'add', 'origin', str(self.origin))
        _git(other, 'fetch', 'origin', 'main')
        _git(other, 'checkout', '-b', 'main', 'origin/main')
        (other / 'from-other.txt').write_text('hi', encoding='utf-8')
        _git(other, 'add', 'from-other.txt')
        _git(other, 'commit', '-m', 'other developer commit')
        _git(other, 'push', 'origin', 'main')

        # Our clone: 2 local commits not yet pushed.
        for i in range(2):
            (self.work / f'local{i}.txt').write_text(
                str(i), encoding='utf-8',
            )
            _git(self.work, 'add', f'local{i}.txt')
            _git(self.work, 'commit', '-m', f'local {i}')
        _git(self.work, 'fetch', 'origin', 'main')

        # Signature: _left_right_commit_counts(local_path, left, right).
        # ``main...origin/main`` → left = commits in main not in
        # origin/main; right = commits in origin/main not in main.
        left, right = self.client._left_right_commit_counts(
            str(self.work), 'main', 'origin/main',
        )
        self.assertEqual(left, 2)
        self.assertEqual(right, 1)

    # ----- _push_branch (round-trip through origin) -----

    def test_push_branch_publishes_local_commits_to_origin(self) -> None:
        _git(self.work, 'checkout', '-b', 'feature/push-real')
        (self.work / 'feat.txt').write_text('ship\n', encoding='utf-8')
        _git(self.work, 'add', 'feat.txt')
        _git(self.work, 'commit', '-m', 'feature commit')

        # Signature: _push_branch(local_path, branch_name, repository=None).
        self.client._push_branch(str(self.work), 'feature/push-real')
        result = _git(self.origin, 'branch', '--list', 'feature/push-real')
        self.assertIn('feature/push-real', result.stdout)

    def test_clear_stale_git_index_lock_removes_lockfile(self) -> None:
        lock = self.work / '.git' / 'index.lock'
        lock.write_text('stale', encoding='utf-8')
        self.assertTrue(lock.is_file())
        cleared = self.client._clear_stale_git_index_lock(str(self.work))
        self.assertTrue(cleared)
        self.assertFalse(lock.is_file())

    def test_clear_stale_git_index_lock_when_no_lock_returns_false(self) -> None:
        cleared = self.client._clear_stale_git_index_lock(str(self.work))
        self.assertFalse(cleared)


if __name__ == '__main__':
    unittest.main()
