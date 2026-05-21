"""Real-git companion for RepositoryService (NO MOCKS).

The existing ``tests/test_repository_service.py`` mocks ``_run_git``
and ``shutil.which`` heavily to avoid spawning real git. That's
fast but doesn't catch:
  * regressions in real git argument shape
  * idempotency bugs in clone / ensure flows
  * branch-state misreads when the working tree actually diverges
    from origin

This file builds real bare + working git repos in tempdirs and
exercises ``RepositoryService.ensure_clone`` and
``RepositoryService.branch_needs_push`` against them. Hermetic
(no shared paths, no shared remotes), parallel-safe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryService,
)


def _git_env() -> dict:
    return {
        **os.environ,
        'GIT_AUTHOR_NAME': 'repo-svc-real',
        'GIT_AUTHOR_EMAIL': 'repo@svc.real',
        'GIT_COMMITTER_NAME': 'repo-svc-real',
        'GIT_COMMITTER_EMAIL': 'repo@svc.real',
    }


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), env=_git_env(),
        check=check, capture_output=True, text=True,
    )


def _make_bare_origin(root: Path, *, branch: str = 'main') -> Path:
    origin = root / 'origin.git'
    origin.mkdir()
    _git(origin, 'init', '--bare', '--initial-branch', branch)
    # Seed the bare repo with an initial commit by pushing from a
    # throwaway clone — needed so origin/<branch> resolves later.
    seed = root / 'seed'
    seed.mkdir()
    _git(seed, 'init', '--initial-branch', branch)
    _git(seed, 'remote', 'add', 'origin', str(origin))
    (seed / 'README.md').write_text('seed\n', encoding='utf-8')
    _git(seed, 'add', 'README.md')
    _git(seed, 'commit', '-m', 'seed')
    _git(seed, 'push', '-u', 'origin', branch)
    return origin


def _make_repository(*, repo_id: str, local_path: Path, remote_url: Path,
                     destination_branch: str = 'main') -> types.SimpleNamespace:
    """Real-shaped Repository for the service to operate on."""
    return types.SimpleNamespace(
        id=repo_id,
        display_name=repo_id,
        local_path=str(local_path),
        remote_url=str(remote_url),
        destination_branch=destination_branch,
        repo_slug=repo_id,
        aliases=[repo_id],
        # The HTTP-auth helper is exercised by other tests; not needed
        # for local file:// remotes.
    )


@unittest.skipUnless(
    shutil.which('git'), 'git binary not available on this system',
)
class RepositoryServiceEnsureCloneRealTests(unittest.TestCase):
    """``ensure_clone`` against a real bare origin."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='repo-svc-clone-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.origin = _make_bare_origin(self.root)
        self.target = self.root / 'clone'
        self.repository = _make_repository(
            repo_id='client',
            local_path=self.target,
            remote_url=self.origin,
        )
        self.service = RepositoryService([self.repository], 1)

    def test_ensure_clone_clones_real_remote_into_target(self) -> None:
        self.assertFalse(self.target.exists())
        self.service.ensure_clone(self.repository, self.target)
        self.assertTrue((self.target / '.git').is_dir())
        # The seeded README from origin is present in the clone.
        self.assertTrue((self.target / 'README.md').is_file())

    def test_ensure_clone_is_idempotent_when_target_already_has_git_dir(self) -> None:
        self.service.ensure_clone(self.repository, self.target)
        # Snapshot mtime of HEAD — a re-call must NOT re-init / touch it.
        head_path = self.target / '.git' / 'HEAD'
        before = head_path.stat().st_mtime_ns
        # A re-call with the SAME target. Must be a no-op (no re-clone).
        self.service.ensure_clone(self.repository, self.target)
        after = head_path.stat().st_mtime_ns
        self.assertEqual(
            before, after,
            'ensure_clone re-initialised an existing clone; '
            'must be idempotent when target/.git exists',
        )

    def test_ensure_clone_creates_parent_directory_if_missing(self) -> None:
        nested = self.root / 'nested' / 'deeper' / 'clone'
        self.service.ensure_clone(self.repository, nested)
        self.assertTrue((nested / '.git').is_dir())

    def test_ensure_clone_raises_when_remote_url_is_blank(self) -> None:
        bad = _make_repository(
            repo_id='bad', local_path=self.target, remote_url=Path(''),
        )
        with self.assertRaises(Exception):  # service raises a config-style error
            self.service.ensure_clone(bad, self.root / 'bad-clone')


@unittest.skipUnless(
    shutil.which('git'), 'git binary not available on this system',
)
class RepositoryServiceBranchNeedsPushRealTests(unittest.TestCase):
    """``branch_needs_push`` against a real clone with branch state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='repo-svc-push-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.origin = _make_bare_origin(self.root)
        self.clone = self.root / 'clone'
        _git(self.root, 'clone', str(self.origin), 'clone')
        self.repository = _make_repository(
            repo_id='client',
            local_path=self.clone,
            remote_url=self.origin,
        )
        self.service = RepositoryService([self.repository], 1)

    def test_returns_false_when_not_on_the_named_branch(self) -> None:
        # Workspace is on ``main``; asked about a branch we're not on.
        self.assertFalse(
            self.service.branch_needs_push(self.repository, 'some-other'),
        )

    def test_returns_false_when_branch_is_in_sync_with_origin(self) -> None:
        # Fresh clone: local main == origin/main, no dirty tree.
        self.assertFalse(
            self.service.branch_needs_push(self.repository, 'main'),
        )

    def test_returns_true_after_a_local_only_commit_on_the_branch(self) -> None:
        # Create a task branch with a new commit; not yet pushed.
        _git(self.clone, 'checkout', '-b', 'PROJ-9')
        (self.clone / 'feat.txt').write_text('ship\n', encoding='utf-8')
        _git(self.clone, 'add', 'feat.txt')
        _git(self.clone, 'commit', '-m', 'local')
        # Service's destination is 'main' (configured); local PROJ-9
        # is ahead of origin/main → push would publish work.
        self.assertTrue(
            self.service.branch_needs_push(self.repository, 'PROJ-9'),
        )

    def test_returns_false_when_local_path_directory_is_missing(self) -> None:
        gone = _make_repository(
            repo_id='gone',
            local_path=self.root / 'never-existed',
            remote_url=self.origin,
        )
        self.assertFalse(self.service.branch_needs_push(gone, 'main'))

    def test_returns_false_when_branch_name_is_blank(self) -> None:
        self.assertFalse(self.service.branch_needs_push(self.repository, ''))
        self.assertFalse(self.service.branch_needs_push(self.repository, '   '))


@unittest.skipUnless(
    shutil.which('git'), 'git binary not available on this system',
)
class RepositoryServiceBuildBranchNameTests(unittest.TestCase):
    """Pure-function branch-name derivation; doesn't need git, but kept
    here so the file holds one suite per public method."""

    def setUp(self) -> None:
        # Minimal repo — build_branch_name only reads Task.id.
        self.repo = _make_repository(
            repo_id='client',
            local_path=Path('/tmp/never-used'),
            remote_url=Path('/tmp/also-not-used'),
        )
        self.service = RepositoryService([self.repo], 1)

    def _task(self, task_id: str) -> Task:
        # Task only needs .id for build_branch_name.
        return types.SimpleNamespace(id=task_id, summary='', tags=[])

    def test_branch_name_is_normalised_task_id(self) -> None:
        self.assertEqual(
            self.service.build_branch_name(self._task('PROJ-1'), self.repo),
            'PROJ-1',
        )

    def test_branch_name_strips_leading_trailing_whitespace(self) -> None:
        self.assertEqual(
            self.service.build_branch_name(
                self._task('  PROJ-7  '), self.repo,
            ),
            'PROJ-7',
        )

    def test_branch_name_preserves_internal_slashes_and_case(self) -> None:
        # The downstream git operations accept these; build_branch_name
        # must not over-normalise.
        self.assertEqual(
            self.service.build_branch_name(
                self._task('feat/PROJ-9'), self.repo,
            ),
            'feat/PROJ-9',
        )


if __name__ == '__main__':
    unittest.main()
