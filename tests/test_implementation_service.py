import types
import unittest
from unittest.mock import Mock

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.fields import ReviewCommentFields
from utils import build_task


class ImplementationServiceTests(unittest.TestCase):
    def test_passes_openhands_client_calls(self) -> None:
        client = types.SimpleNamespace(
            implement_task=Mock(),
            fix_review_comment=Mock(),
        )
        service = ImplementationService(client)
        service.logger = Mock()
        task = build_task()
        comment = service.review_comment_from_payload(
            {
                "pull_request_id": "17",
                "comment_id": "99",
                "author": "reviewer",
                "body": "Please rename this variable.",
            }
        )

        service.implement_task(task)
        service.fix_review_comment(comment, 'feature/proj-1')

        service.logger.info.assert_any_call('delegating implementation for task %s', 'PROJ-1')
        service.logger.info.assert_any_call(
            'delegating review fix for pull request %s comment %s',
            '17',
            '99',
        )
        client.implement_task.assert_called_once_with(task)
        client.fix_review_comment.assert_called_once_with(
            comment,
            'feature/proj-1',
        )

    def test_review_comment_from_payload_builds_entity(self) -> None:
        service = ImplementationService(types.SimpleNamespace())

        comment = service.review_comment_from_payload(
            {
                "pull_request_id": "17",
                "comment_id": "99",
                "author": "reviewer",
                "body": "Please rename this variable.",
            }
        )

        self.assertIsInstance(comment, ReviewComment)
        self.assertEqual(comment.pull_request_id, "17")
        self.assertEqual(comment.comment_id, "99")
        self.assertEqual(comment.author, "reviewer")
        self.assertEqual(comment.body, "Please rename this variable.")

    def test_review_comment_from_payload_raises_value_error_for_invalid_payload(self) -> None:
        service = ImplementationService(types.SimpleNamespace())

        with self.assertRaisesRegex(ValueError, "invalid review comment payload"):
            service.review_comment_from_payload({"pull_request_id": "17"})

    def test_review_comment_from_payload_stringifies_non_string_values(self) -> None:
        service = ImplementationService(types.SimpleNamespace())

        comment = service.review_comment_from_payload(
            {
                "pull_request_id": 17,
                "comment_id": 99,
                "author": None,
                "body": 12345,
            }
        )

        self.assertEqual(comment.pull_request_id, "17")
        self.assertEqual(comment.comment_id, "99")
        self.assertEqual(comment.author, "None")
        self.assertEqual(comment.body, "12345")

    def test_review_comment_from_payload_normalizes_comment_context(self) -> None:
        service = ImplementationService(types.SimpleNamespace())

        comment = service.review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'reviewer',
                ReviewCommentFields.BODY: 'Please rename this variable.',
                ReviewCommentFields.ALL_COMMENTS: [
                    {
                        ReviewCommentFields.COMMENT_ID: 98,
                        ReviewCommentFields.AUTHOR: 'reviewer',
                        ReviewCommentFields.BODY: 'Please add a test.',
                    }
                ],
            }
        )

        self.assertEqual(
            getattr(comment, ReviewCommentFields.ALL_COMMENTS),
            [
                {
                    ReviewCommentFields.COMMENT_ID: '98',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please add a test.',
                }
            ],
        )
