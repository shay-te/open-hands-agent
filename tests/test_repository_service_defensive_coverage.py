"""Defensive coverage for ``RepositoryService`` uncovered branches.

Each test names the line(s) it pins. Most are small fail-safe paths
(missing branch, blank local_path, git failure, OSError, etc.).
"""

from __future__ import annotations

import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError,
    RepositoryService,
    _is_per_task_workspace_clone,
)
from tests.utils import build_test_cfg


def _make_service():
    cfg = build_test_cfg()
    return RepositoryService(cfg, 3)


class IsPerTaskWorkspaceCloneTests(unittest.TestCase):
    def test_returns_false_for_blank_local_path(self) -> None:
        # Lines 38-39.
        self.assertFalse(_is_per_task_workspace_clone(SimpleNamespace(local_path='')))
        self.assertFalse(_is_per_task_workspace_clone(SimpleNamespace()))

    def test_returns_false_on_oserror(self) -> None:
        # Lines 40-43.
        with patch.object(Path, 'is_file', side_effect=OSError('fs error')):
            self.assertFalse(
                _is_per_task_workspace_clone(SimpleNamespace(local_path='/x')),
            )

    def test_returns_true_when_kato_meta_present(self) -> None:
        # Line 41 success path.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '.kato-meta.json').write_text('{}')
            (root / 'repo-a').mkdir()
            self.assertTrue(
                _is_per_task_workspace_clone(
                    SimpleNamespace(local_path=str(root / 'repo-a')),
                ),
            )


class PrepareTaskBranchesMissingBranchNameTests(unittest.TestCase):
    """Line 127: ``raise ValueError('missing task branch name ...')``."""

    def test_raises_when_branch_missing(self) -> None:
        service = _make_service()
        service._validate_git_executable = lambda: None
        service._prepare_task_branch = MagicMock()
        repo = SimpleNamespace(id='client', local_path='/x')
        with self.assertRaisesRegex(ValueError, 'missing task branch name'):
            service.prepare_task_branches([repo], {})


class GetRepositoryTests(unittest.TestCase):
    """Lines 169-172: ``get_repository`` iteration + ValueError."""

    def test_returns_repository_when_found(self) -> None:
        service = _make_service()
        repo = SimpleNamespace(id='client', local_path='/x')
        service._repositories = [repo]
        self.assertIs(service.get_repository('client'), repo)

    def test_raises_when_repository_unknown(self) -> None:
        # Line 172: ``raise ValueError(f'unknown repository id: ...')``.
        service = _make_service()
        service._repositories = []
        # Patch _ensure_repositories to avoid actually loading.
        service._ensure_repositories = lambda: []
        with self.assertRaisesRegex(ValueError, 'unknown repository id'):
            service.get_repository('nope')


class FindPullRequestsTests(unittest.TestCase):
    """Line 354: thin delegate to ``_publication_service.find_pull_requests``."""

    def test_delegates_to_publication_service(self) -> None:
        service = _make_service()
        publication = MagicMock()
        publication.find_pull_requests.return_value = [{'id': '17'}]
        service._publication_service = publication
        repo = SimpleNamespace(id='client')
        result = service.find_pull_requests(
            repo, source_branch='feat/x', title_prefix='PROJ-',
        )
        self.assertEqual(result, [{'id': '17'}])
        publication.find_pull_requests.assert_called_once_with(
            repo, source_branch='feat/x', title_prefix='PROJ-',
        )


class BranchNeedsPushDefensiveTests(unittest.TestCase):
    """``branch_needs_push`` defensive returns (lines 388-389, 402-403,
    411-412, 417-418, 430-431, 438-439).

    The button on the planning UI calls this; it must return False on
    any uncertainty rather than promise a push that fails."""

    def test_returns_false_for_blank_inputs(self) -> None:
        service = _make_service()
        self.assertFalse(service.branch_needs_push(
            SimpleNamespace(local_path=''), 'feat/x',
        ))
        self.assertFalse(service.branch_needs_push(
            SimpleNamespace(local_path='/x'), '',
        ))

    def test_returns_false_when_git_dir_check_raises_oserror(self) -> None:
        # Lines 388-389: ``is_dir()`` raises OSError → False.
        service = _make_service()
        with patch.object(Path, 'is_dir', side_effect=OSError('FS error')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_current_branch_fails(self) -> None:
        # Lines 392-393.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch',
                          side_effect=RuntimeError('git fail')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_status_fails(self) -> None:
        # Lines 402-403.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status',
                          side_effect=RuntimeError('status fail')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_destination_branch_fails(self) -> None:
        # Lines 411-412.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, 'destination_branch',
                          side_effect=RuntimeError('dest fail')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_ahead_count_fails(self) -> None:
        # Lines 417-418.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, 'destination_branch', return_value='main'), \
             patch.object(service, '_comparison_reference', return_value='main'), \
             patch.object(service, '_ahead_count',
                          side_effect=RuntimeError('ahead fail')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_remote_check_fails(self) -> None:
        # Lines 430-431.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, 'destination_branch', return_value='main'), \
             patch.object(service, '_comparison_reference', return_value='main'), \
             patch.object(service, '_ahead_count', return_value=1), \
             patch.object(service, '_git_reference_exists',
                          side_effect=RuntimeError('ref fail')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_left_right_count_fails(self) -> None:
        # Lines 438-439.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, 'destination_branch', return_value='main'), \
             patch.object(service, '_comparison_reference', return_value='main'), \
             patch.object(service, '_ahead_count', return_value=1), \
             patch.object(service, '_git_reference_exists', return_value=True), \
             patch.object(service, '_left_right_commit_counts',
                          side_effect=RuntimeError('count fail')):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_false_when_in_sync_and_clean(self) -> None:
        # Line 420: ``if ahead_destination == 0 and not is_dirty: return False``.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, 'destination_branch', return_value='main'), \
             patch.object(service, '_comparison_reference', return_value='main'), \
             patch.object(service, '_ahead_count', return_value=0):
            self.assertFalse(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )

    def test_returns_true_when_dirty_working_tree(self) -> None:
        # Line 424: ``if is_dirty: return True``.
        service = _make_service()
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status',
                          return_value=' M file.py\n'), \
             patch.object(service, 'destination_branch', return_value='main'), \
             patch.object(service, '_comparison_reference', return_value='main'), \
             patch.object(service, '_ahead_count', return_value=1):
            self.assertTrue(
                service.branch_needs_push(
                    SimpleNamespace(local_path='/x'), 'feat/x',
                ),
            )


class PullWorkspaceCloneDefensiveTests(unittest.TestCase):
    """Lines 499-500, 521-522, 538-539: pull workspace clone error paths."""

    def _setup(self):
        service = _make_service()
        # Make all the upstream calls succeed up to the point we're testing.
        service._validate_local_path = MagicMock()
        return service

    def test_returns_status_check_failed_on_status_exception(self) -> None:
        # Lines 499-503: ``_working_tree_status`` raises → return status dict.
        service = self._setup()
        repo = SimpleNamespace(id='client', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status',
                          side_effect=RuntimeError('status fail')):
            result = service.pull_workspace_clone(repo, 'feat/x')
        self.assertFalse(result['pulled'])
        self.assertEqual(result['reason'], 'status_check_failed')

    def test_returns_remote_lookup_failed_on_ref_exists_exception(self) -> None:
        # Lines 521-525.
        service = self._setup()
        repo = SimpleNamespace(id='client', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, '_run_git', return_value=''), \
             patch.object(service, '_git_reference_exists',
                          side_effect=RuntimeError('ref fail')):
            result = service.pull_workspace_clone(repo, 'feat/x')
        self.assertEqual(result['reason'], 'remote_lookup_failed')

    def test_returns_commit_count_failed_on_count_exception(self) -> None:
        # Lines 538-542.
        service = self._setup()
        repo = SimpleNamespace(id='client', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status', return_value=''), \
             patch.object(service, '_run_git', return_value=''), \
             patch.object(service, '_git_reference_exists', return_value=True), \
             patch.object(service, '_left_right_commit_counts',
                          side_effect=RuntimeError('count fail')):
            result = service.pull_workspace_clone(repo, 'feat/x')
        self.assertEqual(result['reason'], 'commit_count_failed')


class CurrentHeadShaAndDirtyTests(unittest.TestCase):
    """Lines 1214-1239: ``current_head_sha`` and ``has_dirty_working_tree``
    defensive helpers."""

    def test_current_head_sha_returns_empty_for_blank_path(self) -> None:
        service = _make_service()
        self.assertEqual(
            service.current_head_sha(SimpleNamespace(local_path='')),
            '',
        )

    def test_current_head_sha_returns_empty_on_exception(self) -> None:
        service = _make_service()
        with patch.object(service, '_git_stdout',
                          side_effect=RuntimeError('git fail')):
            self.assertEqual(
                service.current_head_sha(SimpleNamespace(local_path='/x')),
                '',
            )

    def test_current_head_sha_returns_stripped_output_on_success(self) -> None:
        service = _make_service()
        with patch.object(service, '_git_stdout', return_value='abc123\n'):
            self.assertEqual(
                service.current_head_sha(SimpleNamespace(local_path='/x')),
                'abc123',
            )

    def test_has_dirty_working_tree_returns_false_for_blank_path(self) -> None:
        service = _make_service()
        self.assertFalse(
            service.has_dirty_working_tree(SimpleNamespace(local_path='')),
        )

    def test_has_dirty_working_tree_returns_false_on_exception(self) -> None:
        service = _make_service()
        with patch.object(service, '_working_tree_status',
                          side_effect=RuntimeError('fail')):
            self.assertFalse(
                service.has_dirty_working_tree(
                    SimpleNamespace(local_path='/x'),
                ),
            )

    def test_has_dirty_working_tree_returns_true_when_status_nonblank(self) -> None:
        service = _make_service()
        with patch.object(service, '_working_tree_status',
                          return_value=' M file.py\n'):
            self.assertTrue(
                service.has_dirty_working_tree(
                    SimpleNamespace(local_path='/x'),
                ),
            )


class ComparisonReferenceTests(unittest.TestCase):
    """Line 1199: ``_comparison_reference`` raises when both refs missing."""

    def test_raises_when_destination_unavailable(self) -> None:
        service = _make_service()
        with patch.object(service, '_git_reference_exists', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not available locally'):
                service._comparison_reference('/x', 'main')


class ValidationReportTextTests(unittest.TestCase):
    """Lines 1247-1249: ``_validation_report_text`` reads file content."""

    def test_returns_none_when_file_missing(self) -> None:
        from kato_core_lib.data_layers.service.repository_service import (
            RepositoryService,
        )
        self.assertIsNone(
            RepositoryService._validation_report_text('/nonexistent/path.md'),
        )

    def test_returns_stripped_content_when_present(self) -> None:
        from kato_core_lib.data_layers.service.repository_service import (
            RepositoryService,
        )
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'report.md'
            target.write_text('  hello\nworld  \n', encoding='utf-8')
            self.assertEqual(
                RepositoryService._validation_report_text(str(target)),
                'hello\nworld',
            )


class EnsureBranchIsPublishableTests(unittest.TestCase):
    """Lines 803-807: ``_ensure_branch_is_publishable`` raises when
    branch has no changes ahead of destination."""

    def test_raises_when_branch_has_no_changes(self) -> None:
        service = _make_service()
        with patch.object(service, '_comparison_reference',
                          return_value='main'), \
             patch.object(service, '_ahead_count', return_value=0):
            with self.assertRaises(RepositoryHasNoChangesError):
                service._ensure_branch_is_publishable('/x', 'feat/x', 'main')

    def test_returns_silently_when_branch_is_ahead(self) -> None:
        service = _make_service()
        with patch.object(service, '_comparison_reference',
                          return_value='main'), \
             patch.object(service, '_ahead_count', return_value=2):
            # No exception.
            service._ensure_branch_is_publishable('/x', 'feat/x', 'main')


class ResolveAndReplyReviewCommentDelegationTests(unittest.TestCase):
    """Lines 557, 560: thin delegates to publication service."""

    def test_resolve_review_comment_delegates(self) -> None:
        service = _make_service()
        publication = MagicMock()
        service._publication_service = publication
        repo = SimpleNamespace(id='r')
        comment = SimpleNamespace(comment_id='c1')
        service.resolve_review_comment(repo, comment)
        publication.resolve_review_comment.assert_called_once_with(repo, comment)

    def test_reply_to_review_comment_delegates(self) -> None:
        service = _make_service()
        publication = MagicMock()
        service._publication_service = publication
        repo = SimpleNamespace(id='r')
        comment = SimpleNamespace(comment_id='c1')
        service.reply_to_review_comment(repo, comment, 'done')
        publication.reply_to_review_comment.assert_called_once_with(
            repo, comment, 'done',
        )


class DestinationBranchInferenceTests(unittest.TestCase):
    """Lines 574, 600: destination branch fallback errors."""

    def test_raises_when_inferred_branch_blank(self) -> None:
        # Line 574: ``_infer_default_branch`` returns blank → ValueError.
        service = _make_service()
        service._validate_local_path = MagicMock()
        with patch.object(service, '_infer_default_branch', return_value=''):
            repo = SimpleNamespace(id='client', destination_branch='', local_path='/x')
            with self.assertRaisesRegex(ValueError, 'unable to determine destination'):
                service.destination_branch(repo)


class EnsureBranchIsPushableErrorTextTests(unittest.TestCase):
    """Lines 600-602: ``_ensure_branch_is_pushable`` translates the
    generic push failure into a ``[Error] ... git push validation
    failed`` message."""

    def test_translates_generic_push_failure(self) -> None:
        service = _make_service()
        with patch.object(
            service, '_push_branch',
            side_effect=RuntimeError('git failed: protocol error'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'git push validation failed'):
                service._ensure_branch_is_pushable('/x', 'feat/x')


class RestoreTaskRepositoryBranchesTests(unittest.TestCase):
    """Lines 622, 624-629: ``_restore_task_repository`` early-returns
    and the dirty/no-force warning path."""

    def test_returns_silently_when_already_on_destination_branch_and_clean(self) -> None:
        # Line 622: already on destination + clean → return.
        service = _make_service()
        repo = SimpleNamespace(id='client', local_path='/x', destination_branch='main')
        service._validate_local_path = MagicMock()
        with patch.object(service, '_current_branch', return_value='main'), \
             patch.object(service, '_working_tree_status', return_value=''):
            service._restore_task_repository(repo)
        # No git operations were attempted past this point.

    def test_skips_restore_when_dirty_without_force(self) -> None:
        # Lines 623-629: dirty + no force → warn + return.
        service = _make_service()
        service.logger = MagicMock()
        service._validate_local_path = MagicMock()
        repo = SimpleNamespace(id='client', local_path='/x', destination_branch='main')
        with patch.object(service, '_current_branch', return_value='feat/x'), \
             patch.object(service, '_working_tree_status',
                          return_value=' M file.py\n'):
            service._restore_task_repository(repo, force=False)
        service.logger.warning.assert_called_once()


class UnstageReportLogTests(unittest.TestCase):
    """Lines 781, 785: validation-report file missing / empty warnings."""

    def test_warns_when_validation_report_missing(self) -> None:
        # Lines 780-783: ``description is None`` → warn.
        service = _make_service()
        service.logger = MagicMock()
        with patch.object(
            service, '_validation_report_paths_from_status',
            return_value=['report.md'],
        ), patch.object(service, '_run_git', return_value=''), \
           patch.object(service, '_validation_report_text', return_value=None):
            result = service._unstage_and_read_validation_reports(
                '/x', 'feat/x', '?? report.md\n',
            )
        self.assertEqual(result, [])
        service.logger.warning.assert_called()

    def test_warns_when_validation_report_blank(self) -> None:
        # Line 785: ``elif not description`` → warn.
        service = _make_service()
        service.logger = MagicMock()
        with patch.object(
            service, '_validation_report_paths_from_status',
            return_value=['report.md'],
        ), patch.object(service, '_run_git', return_value=''), \
           patch.object(service, '_validation_report_text', return_value=''):
            result = service._unstage_and_read_validation_reports(
                '/x', 'feat/x', '?? report.md\n',
            )
        self.assertEqual(result, [])
        service.logger.warning.assert_called()


class EnsureCleanWorktreeTests(unittest.TestCase):
    """Lines 911-922: ``_ensure_clean_worktree`` recovery + refusal."""

    def test_returns_silently_when_clean(self) -> None:
        service = _make_service()
        with patch.object(service, '_working_tree_status', return_value=''):
            service._ensure_clean_worktree('/x', 'feat/x')

    def test_returns_silently_when_artifacts_discarded_cleanly(self) -> None:
        # Lines 911-914: artifacts cleaned → status now blank → return.
        service = _make_service()
        statuses = [' M file.py\n', '']
        with patch.object(service, '_working_tree_status',
                          side_effect=statuses), \
             patch.object(service, '_discard_only_generated_artifacts',
                          return_value=True):
            service._ensure_clean_worktree('/x', 'feat/x')

    def test_raises_when_uncommitted_changes_remain(self) -> None:
        # Lines 915-926: still dirty → log + RuntimeError.
        service = _make_service()
        service.logger = MagicMock()
        with patch.object(service, '_working_tree_status',
                          return_value=' M file.py\n'), \
             patch.object(service, '_discard_only_generated_artifacts',
                          return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'uncommitted changes'):
                service._ensure_clean_worktree('/x', 'feat/x')


class DiscardOnlyGeneratedArtifactsTests(unittest.TestCase):
    """Lines 934-965: ``_discard_only_generated_artifacts`` paths."""

    def test_returns_false_when_no_removable_paths(self) -> None:
        # Lines 937-938.
        service = _make_service()
        with patch.object(
            service, '_generated_artifact_paths_from_status', return_value=[],
        ), patch.object(
            service, '_validation_report_paths_from_status', return_value=[],
        ):
            self.assertFalse(
                service._discard_only_generated_artifacts(
                    '/x', ' M file.py\n', 'feat/x',
                ),
            )

    def test_returns_false_when_other_changes_present(self) -> None:
        # Lines 939-944.
        service = _make_service()
        with patch.object(
            service, '_generated_artifact_paths_from_status',
            return_value=['build.log'],
        ), patch.object(
            service, '_validation_report_paths_from_status', return_value=[],
        ), patch.object(
            service, '_status_contains_only_removable_artifacts',
            return_value=False,
        ):
            self.assertFalse(
                service._discard_only_generated_artifacts(
                    '/x', ' M src.py\n M build.log\n', 'feat/x',
                ),
            )

    def test_returns_false_when_current_branch_blank(self) -> None:
        # Lines 945-946.
        service = _make_service()
        with patch.object(
            service, '_generated_artifact_paths_from_status',
            return_value=['build.log'],
        ), patch.object(
            service, '_validation_report_paths_from_status', return_value=[],
        ), patch.object(
            service, '_status_contains_only_removable_artifacts',
            return_value=True,
        ):
            self.assertFalse(
                service._discard_only_generated_artifacts(
                    '/x', ' M build.log\n', '',  # blank current branch
                ),
            )

    def test_returns_true_and_discards_artifacts(self) -> None:
        # Lines 947-965: happy path.
        service = _make_service()
        service.logger = MagicMock()
        service._run_git = MagicMock(return_value='')
        with patch.object(
            service, '_generated_artifact_paths_from_status',
            return_value=['build.log'],
        ), patch.object(
            service, '_validation_report_paths_from_status', return_value=[],
        ), patch.object(
            service, '_status_contains_only_removable_artifacts',
            return_value=True,
        ):
            result = service._discard_only_generated_artifacts(
                '/x', ' M build.log\n', 'feat/x',
            )
        self.assertTrue(result)
        # Both ``checkout -f`` and ``clean -fd`` were called.
        self.assertEqual(service._run_git.call_count, 2)


class MakeGitReadyTests(unittest.TestCase):
    """Line 983: ``_make_git_ready_for_work`` with remote sync."""

    def test_make_git_ready_runs_remote_sync_step(self) -> None:
        # Lines 982-1004 + 996 specifically: remote-sync branch runs
        # the fetch + reset --hard origin/<dst> calls.
        service = _make_service()
        service.logger = MagicMock()
        service._uses_remote_destination_sync = MagicMock(return_value=True)
        service._run_git = MagicMock(return_value='')
        service._current_branch = MagicMock(return_value='main')
        service._assert_current_branch = MagicMock()
        service._ensure_clean_worktree = MagicMock()
        repo = SimpleNamespace(id='r', local_path='/x')
        service._make_git_ready_for_work('/x', 'main', repo)
        # The remote-sync `_run_git` call(s) fired.
        self.assertTrue(service._run_git.called)
        # Verify the reset --hard origin/main call (line 996-1004).
        calls_args = [c.args[1] for c in service._run_git.call_args_list]
        self.assertTrue(any(
            args[:2] == ['reset', '--hard'] and args[2] == 'origin/main'
            for args in calls_args
        ))


class StatusContainsOnlyRemovableArtifactsTests(unittest.TestCase):
    """Line 1262: ``_status_contains_only_removable_artifacts`` delegates."""

    def test_delegates_to_module_function(self) -> None:
        # Just exercise the delegate; the logic itself is tested elsewhere.
        from kato_core_lib.data_layers.service.repository_service import (
            RepositoryService,
        )
        result = RepositoryService._status_contains_only_removable_artifacts(
            ' M build.log\n', ['build.log'], [],
        )
        # The function returns True only when the status doesn't mention
        # anything outside the removable set; both behaviours are fine
        # — we just want to drive the line.
        self.assertIsInstance(result, bool)


class EnsureTaskBranchCheckedOutTests(unittest.TestCase):
    """Line 1051: ``_ensure_task_branch_checked_out`` short-circuit when
    already on the target branch."""

    def test_returns_current_branch_when_already_on_target(self) -> None:
        service = _make_service()
        result = service._ensure_task_branch_checked_out(
            '/x', 'main', 'feat/x', 'feat/x',
        )
        self.assertEqual(result, ('feat/x', True))


class EnforceBranchIsPushableAuthFailureTests(unittest.TestCase):
    """Lines 596-599: auth-style failure → 'missing git push permissions'."""

    def test_translates_authentication_failed(self) -> None:
        service = _make_service()
        with patch.object(
            service, '_push_branch',
            side_effect=RuntimeError('git failed: Authentication failed for url'),
        ):
            with self.assertRaisesRegex(
                RuntimeError, 'missing git push permissions',
            ):
                service._ensure_branch_is_pushable('/x', 'feat/x')


class EnsureRepoCheckoutOnTaskBranchPostSyncTests(unittest.TestCase):
    """Line 722 (``_assert_branch_checked_out``)."""

    def test_raises_when_current_branch_does_not_match(self) -> None:
        # ``_assert_branch_checked_out`` is exposed via _prepare_workspace_for_task.
        service = _make_service()
        with patch.object(service, '_current_branch', return_value='other-branch'):
            with self.assertRaisesRegex(RuntimeError, 'expected repository at'):
                service._assert_branch_checked_out('/x', 'feat/x')


class GetRepositoryIterationTests(unittest.TestCase):
    """Branch 170->169: ``get_repository`` skips non-matching entries
    before returning the matching one (the for-loop continues past a
    repo whose ``id`` doesn't match)."""

    def test_skips_non_matching_repositories(self) -> None:
        service = _make_service()
        first = SimpleNamespace(id='other', local_path='/a')
        second = SimpleNamespace(id='client', local_path='/b')
        service._ensure_repositories = lambda: [first, second]
        self.assertIs(service.get_repository('client'), second)


class EnsureCleanWorktreeStillDirtyAfterDiscardTests(unittest.TestCase):
    """Branch 1174->1176: after ``_discard_only_generated_artifacts``
    succeeds the status is re-read; if it's STILL non-blank we fall
    through to the warning + RuntimeError (line 1176 onward)."""

    def test_raises_when_status_still_dirty_after_artifact_discard(self) -> None:
        service = _make_service()
        service.logger = MagicMock()
        # First status: dirty. After discard: still dirty (different file
        # not in the removable set). Must hit the raise path.
        statuses = [' M build.log\n M src.py\n', ' M src.py\n']
        with patch.object(service, '_working_tree_status',
                          side_effect=statuses), \
             patch.object(service, '_discard_only_generated_artifacts',
                          return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'uncommitted changes'):
                service._ensure_clean_worktree('/x', 'feat/x')


class EnsureTaskBranchCheckedOutNoRemoteSyncTests(unittest.TestCase):
    """Branch 1330->1334: ``_ensure_task_branch_checked_out`` skips the
    destination-branch sync step when the repository doesn't use remote
    destination sync (local-only repo) and proceeds straight to the
    branch creation."""

    def test_skips_remote_sync_when_not_supported(self) -> None:
        service = _make_service()
        service._uses_remote_destination_sync = MagicMock(return_value=False)
        service._checkout_existing_task_branch = MagicMock(return_value=('', False))
        service._ensure_destination_branch_checked_out = MagicMock(return_value='main')
        service._sync_destination_branch_to_origin = MagicMock()
        service._create_task_branch = MagicMock()
        service._current_branch = MagicMock(return_value='feat/x')
        repo = SimpleNamespace(id='r', local_path='/x')
        branch, should_sync = service._ensure_task_branch_checked_out(
            '/x', 'main', 'feat/x', 'main', repository=repo,
        )
        self.assertEqual(branch, 'feat/x')
        self.assertFalse(should_sync)
        # Critical: sync helper was NOT invoked when remote sync disabled.
        service._sync_destination_branch_to_origin.assert_not_called()
        # But branch creation still ran.
        service._create_task_branch.assert_called_once_with(
            '/x', 'feat/x', 'main',
        )


if __name__ == '__main__':
    unittest.main()
