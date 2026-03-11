import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.fields import ImplementationFields
from utils import (
    assert_client_headers_and_timeout,
    build_review_comment,
    build_task,
)


class OpenHandsClientTests(unittest.TestCase):
    def test_implement_task_posts_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = Mock()
        response.json.return_value = {
            'summary': 'Implemented task',
            ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
            ImplementationFields.SUCCESS: True,
        }
        task = build_task()

        with patch.object(client, '_post', return_value=response) as mock_post:
            result = client.implement_task(task)

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            result,
            {
                'branch_name': 'feature/proj-1',
                'summary': 'Implemented task',
                ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
                ImplementationFields.SUCCESS: True,
            },
        )
        assert_client_headers_and_timeout(self, client, 'oh-token', 300)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args, ('/api/sessions',))
        self.assertNotIn('headers', kwargs)
        self.assertNotIn('timeout', kwargs)
        self.assertIn('Implement task PROJ-1: Fix bug', kwargs['json']['prompt'])

    def test_fix_review_comment_posts_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = Mock()
        response.json.return_value = {
            'summary': 'Updated branch',
            ImplementationFields.COMMIT_MESSAGE: 'Address review comments',
            ImplementationFields.SUCCESS: True,
        }
        comment = build_review_comment()

        with patch.object(client, '_post', return_value=response) as mock_post:
            result = client.fix_review_comment(comment, 'feature/proj-1')

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result['branch_name'], 'feature/proj-1')
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertIn(
            'Comment by reviewer: Please rename this variable.',
            mock_post.call_args.kwargs['json']['prompt'],
        )
