import unittest
from unittest.mock import patch


from openhands_agent.client.github_client import GitHubClient
from openhands_agent.fields import PullRequestFields, ReviewCommentFields
from utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    build_review_comment,
    create_pull_request_with_defaults,
    mock_response,
)


class GitHubClientTests(unittest.TestCase):
    def test_validate_connection_checks_configured_repository(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response()

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('owner', 'repo')

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/repos/owner/repo')

    def test_create_pull_request_normalizes_response(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(
            json_data={
                'number': 17,
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                'html_url': 'https://github.com/owner/repo/pull/17',
            }
        )

        with patch.object(client, '_post', return_value=response) as mock_post:
            pr = create_pull_request_with_defaults(
                client,
                repo_owner='owner',
                description='Ready for review',
            )

        self.assertEqual(
            pr,
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://github.com/owner/repo/pull/17',
            },
        )
        assert_client_headers_and_timeout(self, client, 'gh-token', 30)
        mock_post.assert_called_once_with(
            '/repos/owner/repo/pulls',
            json={
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                'head': 'feature/proj-1',
                'base': 'main',
                'body': 'Ready for review',
            },
        )

    def test_create_pull_request_retries_on_timeout(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(
            json_data={
                'number': 17,
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                'html_url': 'https://github.com/owner/repo/pull/17',
            }
        )

        with patch.object(client, '_post', side_effect=[ClientTimeout('reset'), response]) as mock_post:
            pr = create_pull_request_with_defaults(client, repo_owner='owner')

        self.assertEqual(pr[PullRequestFields.ID], '17')
        self.assertEqual(mock_post.call_count, 2)

    def test_create_pull_request_raises_for_invalid_payload(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(json_data={PullRequestFields.TITLE: 'missing id'})

        with patch.object(client, '_post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
                create_pull_request_with_defaults(client, repo_owner='owner')

    def test_list_pull_request_comments_normalizes_response(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {
                            'nodes': [
                                {
                                    'id': 'thread-1',
                                    'isResolved': False,
                                    'comments': {
                                        'nodes': [
                                            {
                                                'databaseId': 99,
                                                'body': 'Please rename this variable.',
                                                'author': {'login': 'reviewer'},
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        with patch.object(client, '_graphql_with_retry', return_value=payload) as mock_graphql:
            comments = client.list_pull_request_comments('owner', 'repo', '17')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].pull_request_id, '17')
        self.assertEqual(comments[0].comment_id, '99')
        self.assertEqual(comments[0].author, 'reviewer')
        self.assertEqual(comments[0].body, 'Please rename this variable.')
        self.assertEqual(
            getattr(comments[0], ReviewCommentFields.RESOLUTION_TARGET_ID),
            'thread-1',
        )
        mock_graphql.assert_called_once()

    def test_list_pull_request_comments_skips_resolved_threads(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {
                            'nodes': [
                                {
                                    'id': 'thread-1',
                                    'isResolved': True,
                                    'comments': {
                                        'nodes': [
                                            {
                                                'databaseId': 99,
                                                'body': 'Already handled',
                                                'author': {'login': 'reviewer'},
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        with patch.object(client, '_graphql_with_retry', return_value=payload):
            comments = client.list_pull_request_comments('owner', 'repo', '17')

        self.assertEqual(comments, [])

    def test_resolve_review_comment_uses_graphql_thread_resolution(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        comment = build_review_comment(
            resolution_target_id='thread-1',
            resolution_target_type='thread',
            resolvable=True,
        )

        with patch.object(client, '_graphql_with_retry', return_value={'data': {}}) as mock_graphql:
            client.resolve_review_comment('owner', 'repo', comment)

        self.assertEqual(
            mock_graphql.call_args.args[1],
            {'threadId': 'thread-1'},
        )

    def test_graphql_url_uses_enterprise_endpoint_when_rest_base_uses_api_v3(self) -> None:
        client = GitHubClient('https://github.example/api/v3', 'gh-token')

        self.assertEqual(
            client._graphql_url(),
            'https://github.example/api/graphql',
        )

    def test_graphql_request_raises_for_graphql_errors(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(
            json_data={
                'errors': [
                    {'message': 'review thread not found'},
                ]
            }
        )

        with patch.object(client.session, 'post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'review thread not found'):
                client._graphql_with_retry('query { viewer { login } }', {})
