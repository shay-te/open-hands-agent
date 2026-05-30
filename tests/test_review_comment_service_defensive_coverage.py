"""Defensive coverage for ``ReviewCommentService``.

Locks every uncovered defensive branch identified by ``coverage``.
Hermetic — no network. Mocks task/repository/state services.
"""

from __future__ import annotations

import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from requests import HTTPError

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    TaskCommentFields,
)
from kato_core_lib.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from kato_core_lib.helpers.review_comment_utils import ReviewFixContext


def _make_service(**overrides):
    defaults = dict(
        task_service=MagicMock(),
        implementation_service=MagicMock(),
        repository_service=MagicMock(),
        state_registry=MagicMock(),
    )
    defaults.update(overrides)
    return ReviewCommentService(**defaults)


def _comment(comment_id='c1', pull_request_id='pr-1', body='please fix',
             repository_id='', resolution_target_id='', author='reviewer'):
    return SimpleNamespace(
        comment_id=comment_id,
        pull_request_id=pull_request_id,
        body=body,
        author=author,
        file_path='',
        line_number='',
        line_type='',
        commit_sha='',
        all_comments=[],
        repository_id=repository_id,
        resolution_target_id=resolution_target_id,
        resolution_target_type='comment',
    )


class ProcessReviewCommentTests(unittest.TestCase):
    def test_returns_empty_dict_when_batch_returns_no_results(self) -> None:
        # Empty batch result = graceful terminal (no changes made).
        # The singular wrapper returns {} instead of raising.
        service = _make_service()
        with patch.object(
            service, 'process_review_comment_batch', return_value=[],
        ):
            result = service.process_review_comment(_comment('c1'))
            self.assertEqual(result, {})

    def test_empty_batch_returns_empty(self) -> None:
        # Line 120: ``if not comments: return []``.
        service = _make_service()
        self.assertEqual(service.process_review_comment_batch([]), [])


class ReviewPullRequestContextsExceptionTests(unittest.TestCase):
    def test_get_new_pr_comments_swallows_exception_from_review_contexts(
        self,
    ) -> None:
        # Lines 239-241: ``except Exception: log + return []``.
        service = _make_service()
        with patch.object(
            service, '_review_pull_request_contexts',
            side_effect=RuntimeError('platform down'),
        ):
            service.logger = MagicMock()
            self.assertEqual(service.get_new_pull_request_comments(), [])
            service.logger.exception.assert_called_once()


class ReviewTaskPullRequestContextsTests(unittest.TestCase):
    def test_exception_per_task_logged_and_skipped(self) -> None:
        # Lines 269-274.
        service = _make_service()
        task_a = SimpleNamespace(id='T1')
        task_b = SimpleNamespace(id='T2')
        service._task_service.get_review_tasks.return_value = [task_a, task_b]
        # First task crashes, second returns nothing.
        with patch.object(
            service, '_review_task_pull_request_contexts',
            side_effect=[RuntimeError('fail-T1'), []],
        ):
            service.logger = MagicMock()
            result = service._review_pull_request_contexts()
        self.assertEqual(result, [])
        service.logger.exception.assert_called_once()

    def test_dedup_skips_already_seen_keys(self) -> None:
        # Line 281: ``if not all(key) or key in seen: continue``.
        service = _make_service()
        task = SimpleNamespace(id='T1')
        service._task_service.get_review_tasks.return_value = [task]
        contexts = [
            {PullRequestFields.ID: 'pr-1',
             PullRequestFields.REPOSITORY_ID: 'repo-a'},
            {PullRequestFields.ID: 'pr-1',
             PullRequestFields.REPOSITORY_ID: 'repo-a'},  # duplicate
            # Blank id → filtered.
            {PullRequestFields.ID: '', PullRequestFields.REPOSITORY_ID: 'r'},
        ]
        with patch.object(
            service, '_review_task_pull_request_contexts',
            return_value=contexts,
        ):
            result = service._review_pull_request_contexts()
        self.assertEqual(len(result), 1)


class TaskPullRequestTextsTests(unittest.TestCase):
    def test_skips_non_dict_comment_entries(self) -> None:
        # Line 369: ``if not isinstance(comment_entry, dict): continue``.
        task = SimpleNamespace(
            description='task body',
        )
        setattr(task, TaskCommentFields.ALL_COMMENTS, [
            'not a dict',  # skipped
            42,            # skipped
            {TaskCommentFields.BODY: 'real comment'},
        ])
        result = ReviewCommentService._task_pull_request_texts(task)
        self.assertIn('task body', result)
        self.assertIn('real comment', result)


class PullRequestIdFromUrlTests(unittest.TestCase):
    def test_returns_empty_when_path_parts_too_short(self) -> None:
        # Line 381: ``if len(path_parts) < 3: return ''``.
        service = _make_service()
        repo = SimpleNamespace(owner='w', repo_slug='r', provider_base_url='')
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://example.com/short', repo,
            ),
            '',
        )

    def test_returns_empty_when_repository_path_blank(self) -> None:
        # Line 389.
        service = _make_service()
        repo = SimpleNamespace(owner='', repo_slug='', provider_base_url='')
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://x.example/foo/bar/baz', repo,
            ),
            '',
        )

    def test_bitbucket_url_matches(self) -> None:
        # Lines 392-396.
        service = _make_service()
        repo = SimpleNamespace(
            owner='w', repo_slug='r', provider_base_url='https://bitbucket.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://bitbucket.example/w/r/pull-requests/17', repo,
            ),
            '17',
        )

    def test_bitbucket_url_returns_empty_when_path_does_not_match(self) -> None:
        # Lines 393-394: not pull-requests suffix.
        service = _make_service()
        repo = SimpleNamespace(
            owner='w', repo_slug='r', provider_base_url='https://bitbucket.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://bitbucket.example/w/r/wiki/17', repo,
            ),
            '',
        )

    def test_github_url_matches(self) -> None:
        # Lines 397-401.
        service = _make_service()
        repo = SimpleNamespace(
            owner='w', repo_slug='r', provider_base_url='https://github.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://github.example/w/r/pull/42', repo,
            ),
            '42',
        )

    def test_github_url_returns_empty_for_non_pull_path(self) -> None:
        # Line 399: ``path_parts[-2] != 'pull'`` → ''.
        service = _make_service()
        repo = SimpleNamespace(
            owner='w', repo_slug='r', provider_base_url='https://github.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://github.example/w/r/issues/42', repo,
            ),
            '',
        )

    def test_gitlab_url_returns_empty_when_no_second_slash(self) -> None:
        # Line 407: ``merge_request_part.count('/') < 1`` → ''. A URL
        # like ``/group/proj/-/foo`` (no second slash after the dash)
        # is rejected.
        service = _make_service()
        repo = SimpleNamespace(
            owner='group', repo_slug='proj', provider_base_url='https://gitlab.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://gitlab.example/group/proj/-/foo', repo,
            ),
            '',
        )

    def test_github_url_returns_empty_for_wrong_repo_path(self) -> None:
        # Line 400-401: candidate_repository_path mismatch.
        service = _make_service()
        repo = SimpleNamespace(
            owner='w', repo_slug='r', provider_base_url='https://github.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://github.example/other-org/other-repo/pull/42', repo,
            ),
            '',
        )

    def test_gitlab_url_matches(self) -> None:
        # Lines 402-417.
        service = _make_service()
        repo = SimpleNamespace(
            owner='group', repo_slug='proj', provider_base_url='https://gitlab.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://gitlab.example/group/proj/-/merge_requests/3', repo,
            ),
            '3',
        )

    def test_gitlab_url_returns_empty_when_no_dash_marker(self) -> None:
        service = _make_service()
        repo = SimpleNamespace(
            owner='group', repo_slug='proj', provider_base_url='https://gitlab.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://gitlab.example/group/proj/merge_requests/3', repo,
            ),
            '',
        )

    def test_gitlab_url_returns_empty_when_not_merge_request(self) -> None:
        # Line 409.
        service = _make_service()
        repo = SimpleNamespace(
            owner='group', repo_slug='proj', provider_base_url='https://gitlab.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://gitlab.example/group/proj/-/issues/3', repo,
            ),
            '',
        )

    def test_unknown_provider_returns_empty(self) -> None:
        # Line 417: provider didn't match → return ''.
        service = _make_service()
        repo = SimpleNamespace(
            owner='w', repo_slug='r', provider_base_url='https://other.example',
        )
        self.assertEqual(
            service._repository_pull_request_id_from_url(
                'https://other.example/w/r/something/42', repo,
            ),
            '',
        )


class NewPullRequestCommentsForContextTests(unittest.TestCase):
    def test_returns_empty_when_pr_not_in_review_set(self) -> None:
        # Line 427: ``if (pull_request_id, repository_id) not in ...: return []``.
        service = _make_service()
        context = {
            PullRequestFields.ID: 'pr-1',
            PullRequestFields.REPOSITORY_ID: 'repo-a',
        }
        # Empty review set — context doesn't match.
        self.assertEqual(
            service._new_pull_request_comments_for_context(context, set()),
            [],
        )


class PullRequestCommentsExceptionTests(unittest.TestCase):
    def test_returns_empty_on_exception(self) -> None:
        # Lines 450-456.
        service = _make_service()
        service._repository_service.get_repository.side_effect = RuntimeError(
            'no such repo',
        )
        service.logger = MagicMock()
        self.assertEqual(service._pull_request_comments('repo-a', 'pr-1'), [])
        service.logger.exception.assert_called_once()


class TaskIdForCommentTests(unittest.TestCase):
    def test_returns_none_when_context_missing(self) -> None:
        # Lines 544-545.
        service = _make_service()
        service._state_registry.pull_request_context.return_value = None
        self.assertIsNone(service.task_id_for_comment(_comment()))

    def test_returns_task_id_from_dict_context(self) -> None:
        # Lines 546-550.
        service = _make_service()
        service._state_registry.pull_request_context.return_value = {
            'task_id': 'PROJ-1',
        }
        self.assertEqual(service.task_id_for_comment(_comment()), 'PROJ-1')

    def test_returns_task_id_from_object_context(self) -> None:
        # Lines 546-550: ``getattr`` branch for non-dict context.
        service = _make_service()
        service._state_registry.pull_request_context.return_value = SimpleNamespace(
            task_id='PROJ-2',
        )
        self.assertEqual(service.task_id_for_comment(_comment()), 'PROJ-2')

    def test_returns_none_when_task_id_is_blank(self) -> None:
        service = _make_service()
        service._state_registry.pull_request_context.return_value = {
            'task_id': '   ',
        }
        self.assertIsNone(service.task_id_for_comment(_comment()))


class CallFixReviewCommentsBackendTypeErrorTests(unittest.TestCase):
    def test_falls_back_on_typeerror_for_legacy_backend(self) -> None:
        # Lines 693-700.
        service = _make_service()
        backend = MagicMock()
        # First call (with mode kwarg) raises TypeError; legacy fallback succeeds.
        backend.fix_review_comments.side_effect = [
            TypeError('unexpected kwarg "mode"'),
            {'success': True},
        ]
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='s',
        )
        result = service._call_fix_review_comments_or_fanout(
            backend, [_comment()], context, streaming=False, mode='fix',
        )
        # Backend was called twice: once with mode (failed), once without.
        self.assertEqual(backend.fix_review_comments.call_count, 2)
        self.assertEqual(result, {'success': True})

    def test_fanout_short_circuits_on_failure(self) -> None:
        # Line 711: ``if not last_execution.get(SUCCESS): return last_execution``.
        service = _make_service()

        class _NoBatch:
            def fix_review_comment(self, *args, **kwargs):
                return {ImplementationFields.SUCCESS: False, 'error': 'fail'}

        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='s',
        )
        result = service._call_fix_review_comments_or_fanout(
            _NoBatch(), [_comment('c1'), _comment('c2')], context,
            streaming=False, mode='fix',
        )
        self.assertFalse(result[ImplementationFields.SUCCESS])


class ProvisionWorkspaceCloneExceptionTests(unittest.TestCase):
    def test_returns_repository_when_workspace_manager_missing(self) -> None:
        # Lines 737-738: ``if self._workspace_manager is None: return repository``.
        service = _make_service(workspace_manager=None)
        repo = SimpleNamespace(id='r', local_path='/x')
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        self.assertIs(service._provision_workspace_clone(repo, context), repo)

    def test_resolve_failure_falls_back_to_single_repo(self) -> None:
        # Lines 747-759: resolve_task_repositories raises → fallback to
        # cloning only the comment repo.
        service = _make_service(workspace_manager=MagicMock())
        service.logger = MagicMock()
        service._repository_service.resolve_task_repositories.side_effect = (
            RuntimeError('cannot resolve')
        )
        repo = SimpleNamespace(id='r', local_path='/x')
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service.'
            'provision_task_workspace_clones',
            return_value=[repo],
        ), patch.object(
            service, '_task_for_workspace_clone',
            return_value=SimpleNamespace(id='T', summary='', description='', tags=[]),
        ):
            result = service._provision_workspace_clone(repo, context)
        self.assertIs(result, repo)

    def test_appends_comment_repo_when_not_in_resolved_set(self) -> None:
        # Line 770: clone ALSO the comment repo when resolve omits it.
        service = _make_service(workspace_manager=MagicMock())
        comment_repo = SimpleNamespace(id='comment-repo', local_path='/x')
        other_repo = SimpleNamespace(id='other-repo', local_path='/y')
        service._repository_service.resolve_task_repositories.return_value = [
            other_repo,
        ]
        context = ReviewFixContext(
            repository_id='comment-repo',
            pull_request_title='', branch_name='b',
            task_id='T', task_summary='', agent_session_id='',
        )
        captured = {}

        def fake_provision(_wm, _rs, _task, repositories):
            captured['repos'] = repositories
            # Return a clone of comment-repo to satisfy the find-by-id loop.
            return [comment_repo]

        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service.'
            'provision_task_workspace_clones',
            side_effect=fake_provision,
        ), patch.object(
            service, '_task_for_workspace_clone',
            return_value=SimpleNamespace(id='T', summary='', description='', tags=[]),
        ):
            service._provision_workspace_clone(comment_repo, context)
        ids = [getattr(r, 'id', '') for r in captured['repos']]
        self.assertIn('comment-repo', ids)

    def test_returns_original_when_provisioned_set_lacks_comment_repo(
        self,
    ) -> None:
        # Line 784: provisioned list does NOT include a clone whose id
        # matches review_context.repository_id → return the original
        # repository unchanged (the caller's checkout).
        service = _make_service(workspace_manager=MagicMock())
        comment_repo = SimpleNamespace(id='comment-repo', local_path='/x')
        service._repository_service.resolve_task_repositories.return_value = [
            comment_repo,
        ]
        context = ReviewFixContext(
            repository_id='comment-repo',
            pull_request_title='', branch_name='b',
            task_id='T', task_summary='', agent_session_id='',
        )
        # Provisioner returns clones with DIFFERENT ids — the loop on
        # line 781-783 finds no match and we fall through to line 784.
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service.'
            'provision_task_workspace_clones',
            return_value=[SimpleNamespace(id='unrelated', local_path='/y')],
        ), patch.object(
            service, '_task_for_workspace_clone',
            return_value=SimpleNamespace(id='T', summary='', description='', tags=[]),
        ):
            result = service._provision_workspace_clone(comment_repo, context)
        self.assertIs(result, comment_repo)

    def test_failure_in_provisioning_falls_back_to_repository(self) -> None:
        # Lines 784-791: outer try/except.
        service = _make_service(workspace_manager=MagicMock())
        service.logger = MagicMock()
        service._repository_service.resolve_task_repositories.return_value = []
        repo = SimpleNamespace(id='r', local_path='/x')
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service.'
            'provision_task_workspace_clones',
            side_effect=RuntimeError('provision crashed'),
        ), patch.object(
            service, '_task_for_workspace_clone',
            return_value=SimpleNamespace(id='T', summary='', description='', tags=[]),
        ):
            result = service._provision_workspace_clone(repo, context)
        self.assertIs(result, repo)
        service.logger.exception.assert_called()


class TaskForWorkspaceCloneFallbackTests(unittest.TestCase):
    def test_returns_task_from_assigned_queue(self) -> None:
        # Line 829-831: task found in first queue.
        service = _make_service()
        task = SimpleNamespace(id='T1', summary='x', description='', tags=[])
        service._task_service.get_assigned_tasks.return_value = [task]
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T1', task_summary='', agent_session_id='',
        )
        result = service._task_for_workspace_clone(context, SimpleNamespace(id='r'))
        self.assertIs(result, task)

    def test_swallows_assigned_queue_exception_and_falls_back_to_stub(self) -> None:
        # Lines 822-828: exception → log + continue.
        service = _make_service()
        service.logger = MagicMock()
        service._task_service.get_assigned_tasks.side_effect = RuntimeError('fail')
        service._task_service.get_review_tasks.return_value = []
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T1', task_summary='summary', agent_session_id='',
        )
        result = service._task_for_workspace_clone(context, SimpleNamespace(id='r'))
        # Falls back to a SimpleNamespace stub.
        self.assertEqual(result.id, 'T1')
        self.assertEqual(result.summary, 'summary')

    def test_returns_stub_when_no_task_found(self) -> None:
        # Lines 832-838: stub fallback.
        service = _make_service()
        service._task_service.get_assigned_tasks.return_value = []
        service._task_service.get_review_tasks.return_value = []
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T-missing', task_summary='', agent_session_id='',
        )
        result = service._task_for_workspace_clone(context, SimpleNamespace(id='r'))
        self.assertEqual(result.id, 'T-missing')


class ReviewRepositoryLocalPathTests(unittest.TestCase):
    def test_returns_empty_on_repository_lookup_failure(self) -> None:
        # Lines 844-850.
        service = _make_service()
        service._repository_service.get_repository.side_effect = RuntimeError(
            'unknown id',
        )
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        self.assertEqual(service._review_repository_local_path(context), '')

    def test_returns_local_path_when_repository_found(self) -> None:
        service = _make_service()
        service._repository_service.get_repository.return_value = SimpleNamespace(
            local_path='/wks/repo-a',
        )
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        self.assertEqual(
            service._review_repository_local_path(context),
            '/wks/repo-a',
        )


class PublishReviewCommentsBatchExceptionTests(unittest.TestCase):
    def test_reply_failure_is_swallowed_and_does_not_block_resolve(self) -> None:
        # Lines 905-906: reply_to_review_comment raises → log + continue.
        service = _make_service()
        service.logger = MagicMock()
        service._repository_service.publish_review_fix = MagicMock()
        service._repository_service.reply_to_review_comment.side_effect = (
            RuntimeError('reply failed')
        )
        service._repository_service.resolve_review_comment = MagicMock()
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        service._publish_review_comments_batch_fix(
            [_comment()], SimpleNamespace(id='r'), context, {'success': True},
        )
        service.logger.exception.assert_called_once()


class ReviewFixProducedChangesTests(unittest.TestCase):
    def test_returns_true_when_service_missing_helpers(self) -> None:
        # Lines 948-951: fallback when helpers aren't wired.
        service = _make_service()
        # MagicMock has every attribute by default; explicitly set to None.
        service._repository_service.current_head_sha = None
        service._repository_service.has_dirty_working_tree = None
        self.assertTrue(
            service._review_fix_produced_changes(SimpleNamespace(id='r'), ''),
        )

    def test_returns_true_on_head_sha_exception(self) -> None:
        # Lines 954-955.
        service = _make_service()
        service._repository_service.current_head_sha.side_effect = RuntimeError(
            'git fail',
        )
        self.assertTrue(
            service._review_fix_produced_changes(SimpleNamespace(id='r'), 'abc'),
        )

    def test_returns_true_when_head_moved(self) -> None:
        # Line 958: head changed.
        service = _make_service()
        service._repository_service.current_head_sha.return_value = 'new-sha'
        self.assertTrue(
            service._review_fix_produced_changes(
                SimpleNamespace(id='r'), 'old-sha',
            ),
        )

    def test_returns_true_on_dirty_exception(self) -> None:
        # Lines 961-962.
        service = _make_service()
        service._repository_service.current_head_sha.return_value = 'abc'
        service._repository_service.has_dirty_working_tree.side_effect = (
            RuntimeError('git fail')
        )
        self.assertTrue(
            service._review_fix_produced_changes(SimpleNamespace(id='r'), 'abc'),
        )


class PublishReviewNoChangesTests(unittest.TestCase):
    def test_swallows_reply_failure(self) -> None:
        # Lines 1002-1010.
        service = _make_service()
        service.logger = MagicMock()
        service._repository_service.reply_to_review_comment.side_effect = (
            RuntimeError('reply failed')
        )
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        service._publish_review_no_changes(
            [_comment()], SimpleNamespace(id='r'), context,
        )
        service.logger.exception.assert_called_once()


class PublishReviewCommentAnswersTests(unittest.TestCase):
    def test_swallows_reply_failure(self) -> None:
        # Lines 1051-1052.
        service = _make_service()
        service.logger = MagicMock()
        service._repository_service.reply_to_review_comment.side_effect = (
            RuntimeError('reply failed')
        )
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T', task_summary='', agent_session_id='',
        )
        service._publish_review_comment_answers(
            [_comment()], SimpleNamespace(id='r'), context,
            {'message': 'an answer'},
        )
        service.logger.exception.assert_called_once()


class ResolveReviewCommentTests(unittest.TestCase):
    def test_resolves_silently_on_404(self) -> None:
        # Line 1099: HTTPError 404 is non-fatal → log + return False.
        service = _make_service()
        service.logger = MagicMock()
        response = MagicMock(status_code=404)
        http_error = HTTPError(response=response)
        service._repository_service.resolve_review_comment.side_effect = http_error
        result = service._resolve_review_comment(
            SimpleNamespace(id='r'), _comment(),
        )
        self.assertFalse(result)
        service.logger.warning.assert_called()

    def test_re_raises_unexpected_http_error(self) -> None:
        service = _make_service()
        response = MagicMock(status_code=500)  # not in non-fatal set
        http_error = HTTPError(response=response)
        service._repository_service.resolve_review_comment.side_effect = http_error
        with self.assertRaises(HTTPError):
            service._resolve_review_comment(SimpleNamespace(id='r'), _comment())

    def test_resolves_silently_on_non_fatal_runtime_error(self) -> None:
        # Same path for RuntimeError.
        service = _make_service()
        service.logger = MagicMock()
        service._repository_service.resolve_review_comment.side_effect = (
            RuntimeError('comment is already resolved')
        )
        result = service._resolve_review_comment(
            SimpleNamespace(id='r'), _comment(),
        )
        self.assertFalse(result)


class RestoreReviewCommentRepositoryTests(unittest.TestCase):
    def test_swallows_restore_exception(self) -> None:
        # Lines 1142-1143.
        service = _make_service()
        service.logger = MagicMock()
        service._repository_service.restore_task_repositories.side_effect = (
            RuntimeError('restore failed')
        )
        repo = SimpleNamespace(id='r')
        service._restore_review_comment_repository(_comment(), repo)
        service.logger.exception.assert_called_once()


class TaskPullRequestTextsNonListCommentsTests(unittest.TestCase):
    """Branch 372->377: ``_task_pull_request_texts`` skips the for-loop
    body entirely when ``ALL_COMMENTS`` is not a list (defensive cast)."""

    def test_returns_description_only_when_comments_field_not_a_list(self) -> None:
        task = SimpleNamespace(description='task body')
        # Provide a non-list value (e.g. dict, str, None) — the
        # isinstance check fails and the loop is skipped.
        setattr(task, TaskCommentFields.ALL_COMMENTS, {'not': 'a list'})
        result = ReviewCommentService._task_pull_request_texts(task)
        self.assertEqual(result, ['task body'])


class CallFixReviewCommentsStreamingTests(unittest.TestCase):
    """Branch 697->703: when ``streaming`` is True, the plural_args
    reassignment (line 698-702) is skipped and the call proceeds with
    the streaming-style two-tuple plural args."""

    def test_streaming_uses_two_tuple_plural_args(self) -> None:
        service = _make_service()
        backend = MagicMock()
        backend.fix_review_comments.return_value = {'success': True}
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='feat/x', task_id='T', task_summary='s',
            agent_session_id='session-id-streaming',
        )
        repo = SimpleNamespace(id='r', local_path='/workspace/clone')
        result = service._call_fix_review_comments_or_fanout(
            backend, [_comment('c1')], context,
            streaming=True, mode='fix', repository=repo,
        )
        self.assertEqual(result, {'success': True})
        # The streaming plural_args remains the original two-tuple
        # ``(comments, branch_name)`` — agent_session_id NOT in args
        # (it's already part of the streaming session state).
        call = backend.fix_review_comments.call_args
        self.assertEqual(len(call.args), 2)
        # Streaming kwargs include ``repository_local_path``.
        self.assertEqual(call.kwargs['repository_local_path'], '/workspace/clone')


class TaskForWorkspaceCloneSkipNonMatchingTests(unittest.TestCase):
    """Branch 844->843: ``_task_for_workspace_clone`` iterates past tasks
    whose ``id`` doesn't match before returning the matching one."""

    def test_skips_tasks_with_non_matching_ids(self) -> None:
        service = _make_service()
        wrong = SimpleNamespace(id='OTHER', summary='', description='', tags=[])
        target = SimpleNamespace(id='T1', summary='right', description='', tags=[])
        # Both tasks live in the assigned queue; the loop must skip
        # ``wrong`` (line 844 False → 843) and return ``target``.
        service._task_service.get_assigned_tasks.return_value = [wrong, target]
        context = ReviewFixContext(
            repository_id='r', pull_request_title='',
            branch_name='b', task_id='T1', task_summary='', agent_session_id='',
        )
        result = service._task_for_workspace_clone(context, SimpleNamespace(id='r'))
        self.assertIs(result, target)


if __name__ == '__main__':
    unittest.main()
