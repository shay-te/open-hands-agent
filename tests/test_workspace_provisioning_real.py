"""Real-services tests for ``provision_task_workspace_clones`` (NO MOCKS).

The provisioning function runs a parallel ``ensure_clone`` per repo
into the per-task workspace. The existing coverage in
``tests/test_main_coverage.py`` and friends patches the cloning
step out — we exercise it for real here against local bare git
repos. Catches:

  * Parallel-clone race bugs (the function uses ThreadPoolExecutor)
  * Workspace metadata not written before clones land
  * Per-repo path computation regressions
  * The inventory-vs-clone-path rewrite (originals must not mutate)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.service.repository_service import (
    RepositoryService,
)
from kato_core_lib.data_layers.service.workspace_provisioning_service import (
    provision_task_workspace_clones,
)

from tests.chaos_lib import build_real_workspace_service


def _git_env() -> dict:
    return {
        **os.environ,
        'GIT_AUTHOR_NAME': 'provision-real',
        'GIT_AUTHOR_EMAIL': 'p@real',
        'GIT_COMMITTER_NAME': 'provision-real',
        'GIT_COMMITTER_EMAIL': 'p@real',
    }


def _git(cwd: Path, *args: str) -> None:
    subprocess.check_call(
        ['git', *args], cwd=str(cwd), env=_git_env(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _make_bare_with_initial_commit(root: Path, name: str) -> Path:
    bare = root / f'{name}.git'
    bare.mkdir()
    _git(bare, 'init', '--bare', '--initial-branch', 'main')
    seed = root / f'{name}-seed'
    seed.mkdir()
    _git(seed, 'init', '--initial-branch', 'main')
    _git(seed, 'remote', 'add', 'origin', str(bare))
    (seed / f'{name}.txt').write_text('hi', encoding='utf-8')
    _git(seed, 'add', f'{name}.txt')
    _git(seed, 'commit', '-m', f'seed {name}')
    _git(seed, 'push', '-u', 'origin', 'main')
    return bare


@unittest.skipUnless(
    shutil.which('git'), 'git binary not available on this system',
)
class ProvisionTaskWorkspaceClonesRealTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-provision-real-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace_service = build_real_workspace_service(
            self.root / 'workspaces',
        )

    def _repo(self, repo_id: str, bare: Path) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id=repo_id,
            display_name=repo_id,
            local_path=str(self.root / 'inventory' / repo_id),  # original
            remote_url=str(bare),
            destination_branch='main',
            repo_slug=repo_id,
            aliases=[repo_id],
        )

    def _service(self, repositories) -> RepositoryService:
        return RepositoryService(list(repositories), 1)

    def test_provisions_single_repo_into_per_task_workspace(self) -> None:
        bare = _make_bare_with_initial_commit(self.root, 'client')
        repo = self._repo('client', bare)
        task = types.SimpleNamespace(id='PROJ-1', summary='do it', tags=[])
        service = self._service([repo])

        result = provision_task_workspace_clones(
            self.workspace_service, service, task, [repo],
        )

        self.assertEqual(len(result), 1)
        # The returned Repository's local_path now points at the
        # per-task workspace clone, NOT the inventory path.
        provisioned = result[0]
        self.assertNotEqual(provisioned.local_path, repo.local_path)
        expected_clone = self.workspace_service.repository_path('PROJ-1', 'client')
        self.assertEqual(Path(provisioned.local_path), expected_clone)
        # The clone is real on disk with a .git folder + the seed file.
        self.assertTrue((expected_clone / '.git').is_dir())
        self.assertTrue((expected_clone / 'client.txt').is_file())
        # Original inventory object was NOT mutated.
        self.assertNotEqual(repo.local_path, str(expected_clone))

    def test_provisions_three_repos_in_parallel_all_land_on_disk(self) -> None:
        bares = [
            _make_bare_with_initial_commit(self.root, name)
            for name in ('client', 'backend', 'shared')
        ]
        repos = [self._repo(name, bare)
                 for name, bare in zip(('client', 'backend', 'shared'), bares)]
        task = types.SimpleNamespace(id='PROJ-MULTI', summary='multi', tags=[])
        service = self._service(repos)

        result = provision_task_workspace_clones(
            self.workspace_service, service, task, repos,
        )

        self.assertEqual(len(result), 3)
        # All three clones landed on disk under the task workspace.
        for repo in result:
            self.assertTrue((Path(repo.local_path) / '.git').is_dir())
            self.assertTrue((Path(repo.local_path) / f'{repo.id}.txt').is_file())
        # Workspace metadata records all three repo ids.
        record = self.workspace_service.get('PROJ-MULTI')
        self.assertEqual(
            sorted(record.repository_ids),
            ['backend', 'client', 'shared'],
        )

    def test_provision_is_idempotent_when_clones_already_exist(self) -> None:
        bare = _make_bare_with_initial_commit(self.root, 'client')
        repo = self._repo('client', bare)
        task = types.SimpleNamespace(id='PROJ-2', summary='', tags=[])
        service = self._service([repo])

        # First call clones.
        provision_task_workspace_clones(
            self.workspace_service, service, task, [repo],
        )
        clone_path = self.workspace_service.repository_path('PROJ-2', 'client')
        head_mtime_before = (clone_path / '.git' / 'HEAD').stat().st_mtime_ns

        # Second call must be a no-op (clone already on disk).
        provision_task_workspace_clones(
            self.workspace_service, service, task, [repo],
        )
        head_mtime_after = (clone_path / '.git' / 'HEAD').stat().st_mtime_ns
        self.assertEqual(
            head_mtime_before, head_mtime_after,
            'idempotent re-call must not re-init the existing clone',
        )

    def test_provision_with_empty_repository_list_is_a_noop(self) -> None:
        task = types.SimpleNamespace(id='PROJ-3', summary='', tags=[])
        service = self._service([])
        result = provision_task_workspace_clones(
            self.workspace_service, service, task, [],
        )
        self.assertEqual(result, [])
        # No workspace folder created when there's nothing to provision.
        self.assertFalse(
            self.workspace_service.workspace_path('PROJ-3').exists(),
        )

    def test_provision_with_no_workspace_service_returns_inventory_unchanged(
        self,
    ) -> None:
        bare = _make_bare_with_initial_commit(self.root, 'client')
        repo = self._repo('client', bare)
        task = types.SimpleNamespace(id='PROJ-4', summary='', tags=[])
        service = self._service([repo])
        # workspace_service=None → legacy "use existing clones" path.
        result = provision_task_workspace_clones(None, service, task, [repo])
        self.assertEqual(result, [repo])
        # No clones created — the inventory original is returned as-is.
        self.assertEqual(result[0].local_path, repo.local_path)


if __name__ == '__main__':
    unittest.main()
