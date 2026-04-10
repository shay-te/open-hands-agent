import types
import unittest
from unittest.mock import Mock

from requests import HTTPError

from kato.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.service.agent_state_registry import AgentStateRegistry
from kato.data_layers.service.review_comment_service import ReviewCommentService
from kato.helpers.review_comment_utils import review_comment_fixed_comment
from utils import build_review_comment, build_task


class ReviewCommentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = types.SimpleNamespace(
            get_review_tasks=Mock(return_value=[]),
            add_comment=Mock(),
        )
        self.implementation_service = types.SimpleNamespace(
            fix_review_comment=Mock(
                return_value={
                    ImplementationFields.SUCCESS: True,
                }
            ),
        )
        self.repository = types.SimpleNamespace(
            id='client',
            owner='workspace',
            repo_slug='repo',
            provider_base_url='https://api.bitbucket.org/2.0',
        )
        self.repository_service = types.SimpleNamespace(
            get_repository=Mock(return_value=self.repository),
            resolve_task_repositories=Mock(return_value=[self.repository]),
            prepare_task_branches=Mock(),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            restore_task_repositories=Mock(),
            build_branch_name=Mock(return_value='PROJ-1'),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
        )
        self.state_registry = AgentStateRegistry()
        self.service = ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            self.state_registry,
        )

    def test_process_review_comment_raises_for_unknown_pull_request(self) -> None:
        comment = build_review_comment(pull_request_id='17')

        with self.assertRaisesRegex(ValueError, 'unknown pull request id: 17'):
            self.service.process_review_comment(comment)

    def test_process_review_comment_processes_fix_and_marks_comment_processed(self) -> None:
        call_order: list[str] = []
        self.service.logger = Mock()
        self.repository_service.reply_to_review_comment.side_effect = (
            lambda *args, **kwargs: call_order.append('reply')
        )
        self.repository_service.resolve_review_comment.side_effect = (
            lambda *args, **kwargs: call_order.append('resolve')
        )
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
            },
            'feature/proj-1/client',
            session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='Fix bug',
        )

        result = self.service.process_review_comment(comment)

        self.assertEqual(
            result,
            {
                'status': 'updated',
                'pull_request_id': '17',
                'branch_name': 'feature/proj-1/client',
                PullRequestFields.REPOSITORY_ID: 'client',
            },
        )
        self.repository_service.prepare_task_branches.assert_called_once_with(
            [self.repository],
            {'client': 'feature/proj-1/client'},
        )
        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository,
            'feature/proj-1/client',
            'Address review comments',
        )
        self.repository_service.reply_to_review_comment.assert_called_once()
        self.repository_service.resolve_review_comment.assert_called_once_with(
            self.repository,
            comment,
        )
        self.assertEqual(call_order, ['reply', 'resolve'])
        reply_body = self.repository_service.reply_to_review_comment.call_args.args[2]
        self.assertIn('Kato addressed this review comment', reply_body)
        self.task_service.add_comment.assert_called_once_with(
            'PROJ-1',
            review_comment_fixed_comment(comment),
        )
        self.assertEqual(
            self.service.logger.info.call_args_list[0].args,
            (
                'Working on pull request comments: %s',
                'PROJ-1 Fix bug',
            ),
        )
        self.assertEqual(
            self.service.logger.info.call_args_list[1].args,
            (
                'processing review comment %s for pull request %s',
                '99',
                '17',
            ),
        )
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', '99')
        )
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', 'comment:99')
        )

    def test_process_review_comment_treats_resolution_conflict_as_non_fatal(self) -> None:
        self.service.logger = Mock()
        self.repository_service.resolve_review_comment.side_effect = HTTPError(
            '409 Client Error: Conflict',
            response=types.SimpleNamespace(status_code=409),
        )
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
            },
            'feature/proj-1/client',
            task_id='PROJ-1',
            task_summary='Fix bug',
        )

        result = self.service.process_review_comment(comment)

        self.assertEqual(result['status'], 'updated')
        self.repository_service.publish_review_fix.assert_called_once()
        self.repository_service.reply_to_review_comment.assert_called_once()
        self.repository_service.resolve_review_comment.assert_called_once_with(
            self.repository,
            comment,
        )
        self.repository_service.restore_task_repositories.assert_not_called()
        self.service.logger.warning.assert_called_once()
        self.assertIn(
            'skipped resolving review comment %s on pull request %s',
            [call.args[0] for call in self.service.logger.info.call_args_list],
        )
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', '99')
        )

    def test_process_review_comment_treats_already_resolved_runtime_error_as_non_fatal(self) -> None:
        self.service.logger = Mock()
        self.repository_service.resolve_review_comment.side_effect = RuntimeError(
            'review thread is already resolved'
        )
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
            },
            'feature/proj-1/client',
            task_id='PROJ-1',
            task_summary='Fix bug',
        )

        result = self.service.process_review_comment(comment)

        self.assertEqual(result['status'], 'updated')
        self.repository_service.restore_task_repositories.assert_not_called()
        self.service.logger.warning.assert_called_once()
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', '99')
        )

    def test_process_review_comment_restores_repository_when_publish_fails(self) -> None:
        self.repository_service.publish_review_fix.side_effect = RuntimeError('push failed')
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
            },
            'feature/proj-1/client',
            task_id='PROJ-1',
            task_summary='Fix bug',
        )

        with self.assertRaisesRegex(RuntimeError, 'push failed'):
            self.service.process_review_comment(comment)

        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.repository],
            force=True,
        )
        self.repository_service.reply_to_review_comment.assert_not_called()
        self.repository_service.resolve_review_comment.assert_not_called()
        self.assertFalse(self.state_registry.is_review_comment_processed('client', '17', '99'))

    def test_get_new_pull_request_comments_discovers_pull_request_context_for_review_task(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        processed = ReviewComment(
            pull_request_id='17',
            comment_id='98',
            author='reviewer',
            body='Already handled.',
        )
        duplicate_a = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        duplicate_b = ReviewComment(
            pull_request_id='17',
            comment_id='100',
            author='reviewer',
            body='Please rename this variable too.',
        )
        setattr(duplicate_a, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(duplicate_a, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        setattr(duplicate_b, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(duplicate_b, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [
            processed,
            duplicate_a,
            duplicate_b,
        ]
        self.state_registry.mark_review_comment_processed('client', '17', '98')

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['100'])
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.repository_service.find_pull_requests.assert_called_once_with(
            self.repository,
            source_branch='PROJ-1',
            title_prefix='PROJ-1 ',
        )
        self.repository_service.get_repository.assert_called_once_with('client')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '17',
        )
        self.assertEqual(
            self.state_registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                'branch_name': 'PROJ-1',
                'task_id': 'PROJ-1',
                'task_summary': 'Fix bug',
            },
        )

    def test_get_new_pull_request_comments_skips_thread_with_prior_kato_reply(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        reviewer_comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        kato_reply = ReviewComment(
            pull_request_id='17',
            comment_id='100',
            author='kato',
            body='Kato addressed this review comment and pushed a follow-up update.',
        )
        for comment in (reviewer_comment, kato_reply):
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [
            reviewer_comment,
            kato_reply,
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(comments, [])

    def test_get_new_pull_request_comments_skips_processed_resolution_target(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        follow_up_comment = ReviewComment(
            pull_request_id='17',
            comment_id='100',
            author='reviewer',
            body='Please rename this variable too.',
        )
        setattr(follow_up_comment, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(follow_up_comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [
            follow_up_comment,
        ]
        self.state_registry.mark_review_comment_processed('client', '17', 'thread:thread-1')

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(comments, [])

    def test_get_new_pull_request_comments_adds_repository_id_before_remembering_api_discovered_pull_request(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(comments, [])
        self.assertEqual(
            self.state_registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                'branch_name': 'PROJ-1',
                'task_id': 'PROJ-1',
                'task_summary': 'Fix bug',
            },
        )

    def test_get_new_pull_request_comments_uses_task_pull_request_url_before_api_lookup(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(
                task_id='PROJ-1',
                description=(
                    'Requested change.\n\n'
                    'Pull request created: '
                    'https://bitbucket.org/workspace/repo/pull-requests/17'
                ),
                tags=['repo:client'],
            )
        ]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='17',
                comment_id='99',
                author='reviewer',
                body='Please rename this variable.',
            )
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['99'])
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.repository_service.find_pull_requests.assert_not_called()
        self.repository_service.get_repository.assert_called_once_with('client')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '17',
        )
        self.assertEqual(
            self.state_registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'PROJ-1',
                'task_id': 'PROJ-1',
                'task_summary': 'Fix bug',
            },
        )

    def test_get_new_pull_request_comments_uses_task_comment_pull_request_url_before_api_lookup(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(
                task_id='PROJ-1',
                description='Requested change.',
                comments=[
                    {
                        'author': 'OpenHands',
                        'body': (
                            'Pull request created: '
                            'https://bitbucket.org/workspace/repo/pull-requests/18'
                        ),
                    }
                ],
                tags=['repo:client'],
            )
        ]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='18',
                comment_id='100',
                author='reviewer',
                body='Please rename this variable.',
            )
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['100'])
        self.repository_service.find_pull_requests.assert_not_called()
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '18',
        )
