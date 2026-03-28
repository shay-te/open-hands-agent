import unittest
from unittest.mock import Mock, call, patch

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

    def test_validate_connection_checks_openhands_v1_api(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=1)

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection()

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/api/v1/app-conversations/count')

    def test_implement_task_prompt_does_not_embed_testing_commands(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        prompt = client._build_implementation_prompt(build_task())

        self.assertNotIn('Act as a separate testing agent.', prompt)
        self.assertIn('return only JSON', prompt)
        self.assertIn('commit_message: the commit message to use.', prompt)
        self.assertIn('Files changed:', prompt)

    def test_testing_prompt_describes_separate_testing_agent(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        prompt = client._build_testing_prompt(build_task())

        self.assertIn('Act as a separate testing agent.', prompt)
        self.assertIn('Write additional tests when needed', prompt)
        self.assertIn('Do not create a pull request.', prompt)
        self.assertIn('return only JSON', prompt)

    def test_implement_task_uses_v1_conversation_flow(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        task = build_task()

        with patch.object(client, 'logger', client.logger), patch.object(
            client,
            '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [
                                        {
                                            'text': (
                                                '{"success": true, "summary": "Implemented task", '
                                                '"commit_message": "Implement PROJ-1"}'
                                            )
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                ),
            ],
        ) as mock_get:
            result = implement_task_with_defaults(client, task)

        self.assertEqual(
            result,
            {
                'branch_name': 'feature/proj-1',
                'summary': 'Implemented task',
                ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-1',
            },
        )
        assert_client_headers_and_timeout(self, client, 'oh-token', 300)
        self.assertEqual(mock_post.call_args.args, ('/api/v1/app-conversations',))
        request_body = mock_post.call_args.kwargs['json']
        self.assertEqual(request_body['title'], 'PROJ-1: Fix bug')
        self.assertEqual(request_body['initial_message']['role'], 'user')
        self.assertIn(
            'Implement task PROJ-1: Fix bug',
            request_body['initial_message']['content'][0]['text'],
        )
        self.assertIn('Files changed:', request_body['initial_message']['content'][0]['text'])
        self.assertEqual(
            [call_item.args[0] for call_item in mock_get.call_args_list],
            [
                '/api/v1/app-conversations/start-tasks',
                '/api/v1/app-conversations',
                '/api/v1/conversation/conversation-1/events/search',
            ],
        )
        self.assertEqual(
            mock_get.call_args_list[2].kwargs['params'],
            {'limit': 100, 'sort_order': 'TIMESTAMP_DESC'},
        )
        client.logger.info.assert_any_call('requesting implementation for task %s', 'PROJ-1')
        client.logger.info.assert_any_call(
            'implementation finished for task %s with success=%s',
            'PROJ-1',
            True,
        )

    def test_implement_task_uses_parent_conversation_id_for_uuid_session(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        valid_session_id = '570ac918-7d72-42b1-b8fa-c4d06ca6f5f0'

        with patch.object(
            client,
            '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [
                                        {'text': '{"success": true, "summary": "ok"}'}
                                    ],
                                },
                            }
                        ]
                    }
                ),
            ],
        ):
            result = implement_task_with_defaults(client, session_id=valid_session_id)

        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conversation-1')
        self.assertEqual(
            mock_post.call_args.kwargs['json']['parent_conversation_id'],
            '570ac9187d7242b1b8fac4d06ca6f5f0',
        )

    def test_implement_task_ignores_non_uuid_session_id(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client,
            '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [
                                        {'text': '{"success": true, "summary": "ok"}'}
                                    ],
                                },
                            }
                        ]
                    }
                ),
            ],
        ):
            implement_task_with_defaults(client, session_id='conversation-1')

        self.assertNotIn('parent_conversation_id', mock_post.call_args.kwargs['json'])

    def test_test_task_posts_testing_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()

        with patch.object(client, 'logger', client.logger), patch.object(
            client,
            '_run_prompt',
            return_value={
                'summary': 'Added tests and validated the implementation',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-2',
            },
        ) as mock_run_prompt:
            result = test_task_with_defaults(client)

        self.assertEqual(
            result,
            {
                'summary': 'Added tests and validated the implementation',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-2',
            },
        )
        self.assertIn('Act as a separate testing agent.', mock_run_prompt.call_args.kwargs['prompt'])
        self.assertIn('Do not create a pull request.', mock_run_prompt.call_args.kwargs['prompt'])
        client.logger.info.assert_any_call('requesting testing validation for task %s', 'PROJ-1')
        client.logger.info.assert_any_call(
            'testing validation finished for task %s with success=%s',
            'PROJ-1',
            True,
        )

    def test_fix_review_comment_posts_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        comment = build_review_comment()

        with patch.object(client, 'logger', client.logger), patch.object(
            client,
            '_run_prompt',
            return_value={
                'summary': 'Updated branch',
                ImplementationFields.COMMIT_MESSAGE: 'Address review comments',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-3',
            },
        ) as mock_run_prompt:
            result = fix_review_comment_with_defaults(client, comment)

        self.assertEqual(result['branch_name'], 'feature/proj-1')
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conversation-3')
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertIn(
            'Comment by reviewer: Please rename this variable.',
            mock_run_prompt.call_args.kwargs['prompt'],
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
        self.assertIn('return only JSON', prompt)

    def test_implement_task_retries_on_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client,
            '_post',
            side_effect=[
                ClientTimeout('gateway timeout'),
                mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
            ],
        ) as mock_post, patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [
                                        {'text': '{"success": true, "summary": "ok"}'}
                                    ],
                                },
                            }
                        ]
                    }
                ),
            ],
        ):
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_fix_review_comment_retries_on_transient_response(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        retry_response = mock_response(status_code=503)
        success_response = mock_response(json_data={'id': 'start-1', 'status': 'WORKING'})

        with patch.object(
            client,
            '_post',
            side_effect=[retry_response, success_response],
        ) as mock_post, patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [
                                        {'text': '{"success": true, "summary": "ok"}'}
                                    ],
                                },
                            }
                        ]
                    }
                ),
            ],
        ):
            result = fix_review_comment_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_test_task_retries_on_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client,
            '_post',
            side_effect=[
                ClientTimeout('gateway timeout'),
                mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
            ],
        ) as mock_post, patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [
                                        {'text': '{"success": true, "summary": "ok"}'}
                                    ],
                                },
                            }
                        ]
                    }
                ),
            ],
        ):
            result = test_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_parse_result_json_reads_fenced_json(self) -> None:
        payload = OpenHandsClient._parse_result_json(
            '```json\n{"success": true, "summary": "ok"}\n```'
        )

        self.assertEqual(
            payload,
            {
                'success': True,
                'summary': 'ok',
            },
        )

    def test_run_prompt_raises_when_events_do_not_include_parseable_result(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client,
            '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client,
            '_get',
            side_effect=[
                mock_response(
                    json_data=[
                        {
                            'id': 'start-1',
                            'status': 'READY',
                            'app_conversation_id': 'conversation-1',
                        }
                    ]
                ),
                mock_response(
                    json_data=[
                        {
                            'id': 'conversation-1',
                            'execution_status': 'finished',
                        }
                    ]
                ),
                mock_response(
                    json_data={
                        'items': [
                            {
                                'kind': 'MessageEvent',
                                'source': 'agent',
                                'llm_message': {
                                    'role': 'assistant',
                                    'content': [{'text': 'not json'}],
                                },
                            }
                        ]
                    }
                ),
            ],
        ):
            with self.assertRaisesRegex(
                ValueError,
                'did not return a parseable result',
            ):
                implement_task_with_defaults(client)

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
