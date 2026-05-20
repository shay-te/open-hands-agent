"""``Merge master`` — fetch + merge the default branch into a task
branch so the (git-blocked) agent can resolve conflicts by editing
files.

Two layers:
  * RepositoryService.merge_default_branch_into_clone — preflight
    refusals (mocked) + a real on-disk git repo for the clean-merge
    and conflict paths (the conflict path is the whole point: markers
    must be LEFT in the tree, not aborted).
  * agent_service.merge_default_branch_for_task — aggregation across
    repos (mocked repo-service outcomes).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.repository_service import (
    RepositoryService,
)
from tests.utils import build_test_cfg


def _make_service():
    return RepositoryService(build_test_cfg(), 3)


def _git(cwd, *args):
    subprocess.run(
        ['git', *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _build_repo_with_diverged_default(tmp: Path):
    """Create an origin + a clone whose task branch is behind the
    default branch, with a change that will/​won't conflict depending
    on the file written. Returns (clone_path, repository_ns)."""
    origin = tmp / 'origin.git'
    work = tmp / 'seed'
    work.mkdir()
    _git(work, 'init', '-q')
    _git(work, 'config', 'user.email', 't@example.com')
    _git(work, 'config', 'user.name', 'Test')
    _git(work, 'checkout', '-q', '-b', 'main')
    (work / 'shared.txt').write_text('base\n', encoding='utf-8')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-q', '-m', 'base')
    _git(work, 'clone', '-q', '--bare', str(work), str(origin))
    _git(work, 'remote', 'add', 'origin', str(origin))
    _git(work, 'push', '-q', 'origin', 'main')

    clone = tmp / 'clone'
    _git(tmp, 'clone', '-q', str(origin), str(clone))
    _git(clone, 'config', 'user.email', 't@example.com')
    _git(clone, 'config', 'user.name', 'Test')
    _git(clone, 'checkout', '-q', '-b', 'feat/x', 'main')
    _git(clone, 'commit', '-q', '--allow-empty', '-m', 'task work')

    # Advance main on origin so feat/x is behind by one commit.
    _git(work, 'checkout', '-q', 'main')
    (work / 'shared.txt').write_text('CHANGED ON MAIN\n', encoding='utf-8')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-q', '-m', 'main moved')
    _git(work, 'push', '-q', 'origin', 'main')

    repo = SimpleNamespace(id='client', local_path=str(clone),
                           destination_branch='main')
    return clone, repo


class MergePreflightTests(unittest.TestCase):
    """Mocked refusals — never reach a real git repo."""

    def setUp(self) -> None:
        self.service = _make_service()
        self.service._validate_local_path = MagicMock()

    def test_no_local_path(self) -> None:
        repo = SimpleNamespace(id='c', local_path='')
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertFalse(out['merged'])
        self.assertEqual(out['reason'], 'no_local_path')

    def test_wrong_branch_checked_out(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='other'):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'wrong_branch_checked_out')

    def test_dirty_working_tree_refused(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, '_working_tree_status',
                          return_value=' M file.py'):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'dirty_working_tree')


class MergeRealGitTests(unittest.TestCase):
    """Real on-disk git: clean merge AND the conflict path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.service = _make_service()
        self.service._validate_local_path = MagicMock()

    def test_clean_merge_brings_default_branch_in(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # feat/x didn't touch shared.txt → clean fast-content merge.
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertTrue(out['merged'], out)
        self.assertTrue(out['updated'])
        self.assertEqual(out['default_branch'], 'main')
        self.assertEqual(
            (clone / 'shared.txt').read_text(encoding='utf-8'),
            'CHANGED ON MAIN\n',
        )

    def test_conflict_leaves_markers_in_tree_not_aborted(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # Make feat/x edit the SAME line main changed → real conflict.
        (clone / 'shared.txt').write_text('CHANGED ON FEAT\n',
                                          encoding='utf-8')
        _git(clone, 'add', '-A')
        _git(clone, 'commit', '-q', '-m', 'feat edits shared')
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertFalse(out['merged'])
        self.assertTrue(out['conflicts'])
        self.assertIn('shared.txt', out['conflicted_files'])
        # The whole point: markers + MERGE_HEAD must be LEFT so the
        # agent can resolve them; the merge was NOT aborted.
        self.assertTrue((clone / '.git' / 'MERGE_HEAD').exists())
        self.assertIn(
            '<<<<<<<',
            (clone / 'shared.txt').read_text(encoding='utf-8'),
        )

    def test_already_up_to_date_is_a_noop(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # First merge brings main in cleanly...
        self.service.merge_default_branch_into_clone(repo, 'feat/x')
        # ...second merge has nothing left to do.
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertTrue(out['merged'])
        self.assertFalse(out['updated'])
        self.assertEqual(out['commits_merged'], 0)


class AgentAggregationTests(unittest.TestCase):
    """merge_default_branch_for_task rolls per-repo outcomes up."""

    def _service(self):
        from kato_core_lib.data_layers.service.agent_service import AgentService
        svc = AgentService.__new__(AgentService)
        svc.logger = MagicMock()
        svc._repository_service = MagicMock()
        svc._repository_service.build_branch_name.return_value = 'feat/x'
        return svc

    def test_empty_task_id(self) -> None:
        svc = self._service()
        out = svc.merge_default_branch_for_task('  ')
        self.assertFalse(out['merged'])
        self.assertEqual(out['error'], 'empty task id')

    def test_conflicts_surface_with_files(self) -> None:
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': False, 'conflicts': True, 'default_branch': 'main',
            'conflicted_files': ['a.py', 'b.py'],
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertTrue(out['has_conflicts'])
        self.assertEqual(
            out['conflicted_repositories'][0]['conflicted_files'],
            ['a.py', 'b.py'],
        )

    def test_clean_merge_aggregates(self) -> None:
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': True, 'updated': True, 'commits_merged': 3,
            'default_branch': 'main',
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertTrue(out['merged'])
        self.assertFalse(out['has_conflicts'])
        self.assertEqual(
            out['merged_repositories'][0]['commits_merged'], 3,
        )

    def test_no_workspace_context_returns_error(self) -> None:
        # _resolve_publish_context yields no repos (clone gone /
        # task never provisioned) → explicit error, no merge attempt.
        svc = self._service()
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([], None, None),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertFalse(out['merged'])
        self.assertEqual(out['task_id'], 'T-1')
        self.assertEqual(out['error'], 'no workspace context for this task')
        svc._repository_service.merge_default_branch_into_clone.assert_not_called()

    def test_repo_merge_exception_is_isolated_as_failed(self) -> None:
        # One repo raising must not abort the run — it lands in
        # failed_repositories with the error string.
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.side_effect = (
            RuntimeError('git exploded')
        )
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertFalse(out['merged'])
        self.assertEqual(out['failed_repositories'][0]['repository_id'], 'client')
        self.assertIn('git exploded', out['failed_repositories'][0]['error'])
        svc.logger.exception.assert_called()

    def test_already_contains_default_branch_is_skipped(self) -> None:
        # merged=True but updated=False → branch already had the
        # default branch; reported as an already_up_to_date skip.
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': True, 'updated': False,
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        skipped = out['skipped_repositories'][0]
        self.assertEqual(skipped['repository_id'], 'client')
        self.assertEqual(skipped['reason'], 'already_up_to_date')

    def test_unmergeable_outcome_is_skipped_with_reason(self) -> None:
        # Neither merged nor conflicts (e.g. preflight refused) →
        # skipped with the outcome's own reason/detail surfaced.
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': False, 'conflicts': False,
            'reason': 'dirty_tree', 'detail': 'uncommitted changes',
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        skipped = out['skipped_repositories'][0]
        self.assertEqual(skipped['reason'], 'dirty_tree')
        self.assertEqual(skipped['detail'], 'uncommitted changes')


# ---------------------------------------------------------------------------
# Coverage for defensive branches in _merge_preflight + merge_default_branch_into_clone
# ---------------------------------------------------------------------------


class MergePreflightDefensiveBranchTests(unittest.TestCase):
    """Cover the remaining ``return fail(...)`` branches in
    ``_merge_preflight`` that the original tests didn't reach."""

    def setUp(self) -> None:
        self.service = _make_service()
        self.service._validate_local_path = MagicMock()

    def test_not_a_git_repo(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/no/git/here')
        with patch.object(Path, 'is_dir', return_value=False):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'not_a_git_repo')

    def test_no_branch_argument(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True):
            out = self.service.merge_default_branch_into_clone(repo, '   ')
        self.assertEqual(out['reason'], 'no_branch')

    def test_current_branch_lookup_failure(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          side_effect=RuntimeError('git rev-parse failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'branch_lookup_failed')
        self.assertIn('git rev-parse failed', out['detail'])

    def test_status_check_failure(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, '_working_tree_status',
                          side_effect=RuntimeError('git status failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'status_check_failed')

    def test_default_branch_unknown(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, '_working_tree_status',
                          return_value=''), \
             patch.object(self.service, 'destination_branch',
                          side_effect=ValueError('no default branch')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'default_branch_unknown')


class MergeDefaultBranchPostPreflightBranchTests(unittest.TestCase):
    """Defensive branches AFTER ``_merge_preflight`` returns OK —
    fetch failure, remote-lookup failure, missing remote default,
    commit-count failure, merge-fail-with-abort."""

    def setUp(self) -> None:
        self.service = _make_service()
        # All preflight checks pass; default branch is 'master'.
        self.service._merge_preflight = MagicMock(
            return_value={'default_branch': 'master'},
        )

    def test_fetch_failure_surfaces_fetch_failed(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git',
                          side_effect=RuntimeError('network down')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'fetch_failed')
        self.assertIn('network down', out['detail'])

    def test_remote_lookup_exception_surfaces_remote_lookup_failed(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          side_effect=RuntimeError('rev-parse failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'remote_lookup_failed')

    def test_remote_default_missing(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=False):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'remote_default_missing')

    def test_commit_count_failure(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=True), \
             patch.object(self.service, '_left_right_commit_counts',
                          side_effect=RuntimeError('count failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'commit_count_failed')

    def test_non_zero_merge_with_no_conflict_aborts(self) -> None:
        # Lines 769-775: ``git merge`` returns non-zero but
        # ``_unmerged_paths`` is empty — so the merge failed for some
        # other reason (refusing for an unrelated cause). We must
        # abort the merge and return reason='merge_failed'.
        repo = SimpleNamespace(id='c', local_path='/x')
        # Behind-count > 0 so we actually attempt the merge.
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=True), \
             patch.object(self.service, '_left_right_commit_counts',
                          return_value=(0, 3)), \
             patch.object(self.service, '_run_git_subprocess',
                          return_value=SimpleNamespace(
                              returncode=1, stderr='merge bailed',
                              stdout='',
                          )), \
             patch.object(self.service, '_unmerged_paths', return_value=[]):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'merge_failed')
        self.assertIn('merge bailed', out['detail'])


class UnmergedPathsBranchTest(unittest.TestCase):
    """Line 784: ``_unmerged_paths`` returns [] when git exits non-zero."""

    def test_returns_empty_list_on_git_failure(self) -> None:
        service = _make_service()
        with patch.object(
            service, '_run_git_subprocess',
            return_value=SimpleNamespace(returncode=1, stdout='', stderr='x'),
        ):
            self.assertEqual(service._unmerged_paths('/x'), [])


class GetRepositoryRaisesWhenAllFallbacksMissTests(unittest.TestCase):
    """Line 179: the final ``raise ValueError`` in
    ``RepositoryService.get_repository`` when both the inventory
    lookup AND the direct-folder fallback miss."""

    def test_raises_when_inventory_and_direct_lookup_both_miss(self) -> None:
        service = _make_service()
        with patch.object(service, '_ensure_repositories', return_value=[]), \
             patch.object(service, '_discover_repository_at_named_folder',
                          return_value=None):
            with self.assertRaisesRegex(ValueError, 'unknown repository id: nope'):
                service.get_repository('nope')

    def test_returns_direct_lookup_when_inventory_missed_repo(self) -> None:
        # Line 178: ``return direct`` — the inventory walk doesn't
        # include the repo but the direct-folder lookup finds it.
        # This is the fix path for the Windows-operator case where the
        # full inventory walk missed a repo that nevertheless exists
        # at REPOSITORY_ROOT_PATH/<id>/.
        service = _make_service()
        stub_repo = SimpleNamespace(
            id='ob-love-admin-client',
            local_path='/repos/ob-love-admin-client',
        )
        with patch.object(service, '_ensure_repositories', return_value=[]), \
             patch.object(service, '_discover_repository_at_named_folder',
                          return_value=stub_repo) as discover:
            result = service.get_repository('ob-love-admin-client')
        discover.assert_called_once_with('ob-love-admin-client')
        self.assertIs(result, stub_repo)


class WorkspaceHasTaskChangesDefensiveBranchTests(unittest.TestCase):
    """Lines 479-480, 483-484, 495-496 in ``workspace_has_task_changes``:
    OSError on ``.git`` is_dir check, exception in ``_current_branch``,
    exception in ``_ahead_count``."""

    def test_oserror_on_git_dir_check_returns_true(self) -> None:
        # Lines 479-480: ``(Path(local_path) / '.git').is_dir()`` raises
        # OSError (path too long, perms, etc.). Fail-open → return True.
        service = _make_service()
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', side_effect=OSError('denied')):
            self.assertTrue(
                service.workspace_has_task_changes(repo, 'feat/x'),
            )

    def test_current_branch_exception_returns_true(self) -> None:
        # Lines 483-484: fail-open on ``_current_branch`` failure.
        service = _make_service()
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch',
                          side_effect=RuntimeError('git failed')):
            self.assertTrue(
                service.workspace_has_task_changes(repo, 'feat/x'),
            )

    def test_destination_branch_exception_returns_true(self) -> None:
        # Lines 495-496: fail-open on destination_branch / _ahead_count.
        service = _make_service()
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(service, 'destination_branch',
                          side_effect=ValueError('no default')):
            self.assertTrue(
                service.workspace_has_task_changes(repo, 'feat/x'),
            )


if __name__ == '__main__':
    unittest.main()
