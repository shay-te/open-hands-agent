import types
import unittest
from unittest.mock import Mock

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from utils import build_task


class ImplementationServiceTests(unittest.TestCase):
    def test_passes_openhands_client_calls(self) -> None:
        client = types.SimpleNamespace(
            implement_task=Mock(),
            fix_review_comment=Mock(),
        )
        service = ImplementationService(client)
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
