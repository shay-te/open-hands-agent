import types
import unittest
from unittest.mock import Mock

from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.review_comment_service import ReviewCommentService
from openhands_agent.helpers.review_comment_utils import review_comment_fixed_comment
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
        self.repository = types.SimpleNamespace(id='client')
        self.repository_service = types.SimpleNamespace(
            get_repository=Mock(return_value=self.repository),
            prepare_task_branches=Mock(),
            publish_review_fix=Mock(),
            resolve_review_comment=Mock(),
            restore_task_repositories=Mock(),
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
        self.repository_service.resolve_review_comment.assert_called_once_with(
            self.repository,
            comment,
        )
        self.task_service.add_comment.assert_called_once_with(
            'PROJ-1',
            review_comment_fixed_comment(comment),
        )
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
        self.repository_service.resolve_review_comment.assert_not_called()
        self.assertFalse(self.state_registry.is_review_comment_processed('client', '17', '99'))

    def test_get_new_pull_request_comments_filters_processed_comments_and_duplicate_targets(self) -> None:
        self.task_service.get_review_tasks.return_value = [build_task(task_id='PROJ-1')]
        self.state_registry.mark_task_processed(
            'PROJ-1',
            [
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'client',
                }
            ],
        )
        self.state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
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
        self.repository_service.get_repository.assert_called_once_with('client')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '17',
        )
