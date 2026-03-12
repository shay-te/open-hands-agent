import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.fields import ImplementationFields, ReviewCommentFields
from utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    build_review_comment,
    build_task,
    fix_review_comment_with_defaults,
    implement_task_with_defaults,
    mock_response,
    test_task_with_defaults,
)


class OpenHandsClientTests(unittest.TestCase):
    def test_uses_configured_retry_count(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token', max_retries=5)
        self.assertEqual(client.max_retries, 5)

    def test_uses_minimum_retry_count_of_one(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token', max_retries=0)
        self.assertEqual(client.max_retries, 1)

    def test_validate_connection_checks_openhands_api(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection()

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/api/sessions')

    def test_implement_task_prompt_does_not_embed_testing_commands(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        prompt = client._build_implementation_prompt(build_task())

        self.assertNotIn('Before creating the pull request:', prompt)
        self.assertNotIn('Act as a separate testing agent.', prompt)

    def test_testing_prompt_describes_separate_testing_agent(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        prompt = client._build_testing_prompt(build_task())

        self.assertIn('Act as a separate testing agent.', prompt)
        self.assertIn('Write additional tests when needed', prompt)
        self.assertIn('Do not create a pull request.', prompt)

    def test_implement_task_posts_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        response = mock_response(json_data={
            'summary': 'Implemented task',
            ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
            ImplementationFields.SUCCESS: True,
        })
        task = build_task()

        with patch.object(client, 'logger', client.logger), patch.object(
            client,
            '_post',
            return_value=response,
        ) as mock_post:
            result = implement_task_with_defaults(client, task)

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
        self.assertNotIn('Before creating the pull request:', kwargs['json']['prompt'])
        client.logger.info.assert_any_call('requesting implementation for task %s', 'PROJ-1')
        client.logger.info.assert_any_call(
            'implementation finished for task %s with success=%s',
            'PROJ-1',
            True,
        )

    def test_test_task_posts_testing_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        response = mock_response(json_data={
            'summary': 'Added tests and validated the implementation',
            ImplementationFields.SUCCESS: True,
        })

        with patch.object(client, 'logger', client.logger), patch.object(
            client,
            '_post',
            return_value=response,
        ) as mock_post:
            result = test_task_with_defaults(client)

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            result,
            {
                'summary': 'Added tests and validated the implementation',
                ImplementationFields.SUCCESS: True,
            },
        )
        self.assertIn('Act as a separate testing agent.', mock_post.call_args.kwargs['json']['prompt'])
        self.assertIn('Do not create a pull request.', mock_post.call_args.kwargs['json']['prompt'])
        client.logger.info.assert_any_call('requesting testing validation for task %s', 'PROJ-1')
        client.logger.info.assert_any_call(
            'testing validation finished for task %s with success=%s',
            'PROJ-1',
            True,
        )

    def test_fix_review_comment_posts_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        response = mock_response(json_data={
            'summary': 'Updated branch',
            ImplementationFields.COMMIT_MESSAGE: 'Address review comments',
            ImplementationFields.SUCCESS: True,
        })
        comment = build_review_comment()

        with patch.object(client, 'logger', client.logger), patch.object(
            client,
            '_post',
            return_value=response,
        ) as mock_post:
            result = fix_review_comment_with_defaults(client, comment)

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result['branch_name'], 'feature/proj-1')
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertIn(
            'Comment by reviewer: Please rename this variable.',
            mock_post.call_args.kwargs['json']['prompt'],
        )
        client.logger.info.assert_any_call(
            'requesting review fix for pull request %s comment %s',
            '17',
            '99',
        )
        client.logger.info.assert_any_call(
            'review fix finished for pull request %s comment %s with success=%s',
            '17',
            '99',
            True,
        )

    def test_fix_review_comment_prompt_includes_prior_comment_context(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        comment = build_review_comment()
        setattr(
            comment,
            ReviewCommentFields.ALL_COMMENTS,
            [
                {
                    ReviewCommentFields.COMMENT_ID: '98',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please add a test.',
                },
                {
                    ReviewCommentFields.COMMENT_ID: '99',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please rename this variable.',
                },
            ],
        )

        prompt = client._build_review_prompt(comment, 'feature/proj-1')

        self.assertIn('Review comment context:', prompt)
        self.assertIn('- reviewer: Please add a test.', prompt)

    def test_implement_task_retries_on_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data={ImplementationFields.SUCCESS: True})

        with patch.object(
            client,
            '_post',
            side_effect=[ClientTimeout('gateway timeout'), response],
        ) as mock_post:
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_fix_review_comment_retries_on_transient_response(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        retry_response = mock_response(status_code=503)
        success_response = mock_response(json_data={ImplementationFields.SUCCESS: True})

        with patch.object(
            client,
            '_post',
            side_effect=[retry_response, success_response],
        ) as mock_post:
            result = fix_review_comment_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_test_task_retries_on_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data={ImplementationFields.SUCCESS: True})

        with patch.object(
            client,
            '_post',
            side_effect=[ClientTimeout('gateway timeout'), response],
        ) as mock_post:
            result = test_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_implement_task_uses_defaults_for_null_payload(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=None)

        with patch.object(client, '_post', return_value=response):
            result = implement_task_with_defaults(client)

        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'Implement PROJ-1')
        self.assertFalse(result[ImplementationFields.SUCCESS])

    def test_implement_task_uses_defaults_for_non_dict_payload(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=['unexpected'])

        with patch.object(client, '_post', return_value=response):
            result = implement_task_with_defaults(client)

        self.assertEqual(result['summary'], '')
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'Implement PROJ-1')
        self.assertFalse(result[ImplementationFields.SUCCESS])

    def test_fix_review_comment_raises_after_retry_exhaustion(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client,
            '_post',
            side_effect=[
                ClientTimeout('timeout'),
                ClientTimeout('timeout'),
                ClientTimeout('timeout'),
            ],
        ):
            with self.assertRaises(ClientTimeout):
                fix_review_comment_with_defaults(client)

    def test_fix_review_comment_uses_defaults_for_non_dict_payload(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=['unexpected'])

        with patch.object(client, '_post', return_value=response):
            result = fix_review_comment_with_defaults(client)

        self.assertEqual(result['summary'], '')
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'Address review comments')
        self.assertFalse(result[ImplementationFields.SUCCESS])

    def test_test_task_uses_defaults_for_non_dict_payload(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=['unexpected'])

        with patch.object(client, '_post', return_value=response):
            result = test_task_with_defaults(client)

        self.assertEqual(result['summary'], '')
        self.assertFalse(result[ImplementationFields.SUCCESS])

    def test_success_flag_treats_false_string_as_false(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(
            json_data={
                'summary': 'Implemented task',
                ImplementationFields.SUCCESS: 'false',
            }
        )

        with patch.object(client, '_post', return_value=response):
            result = implement_task_with_defaults(client)

        self.assertFalse(result[ImplementationFields.SUCCESS])

    def test_implement_task_does_not_retry_non_transient_exception(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client,
            '_post',
            side_effect=ValueError('invalid request'),
        ) as mock_post:
            with self.assertRaisesRegex(ValueError, 'invalid request'):
                implement_task_with_defaults(client)

        self.assertEqual(mock_post.call_count, 1)
