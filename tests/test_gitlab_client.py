import unittest
from unittest.mock import patch

import bootstrap  # noqa: F401

from openhands_agent.client.gitlab_client import GitLabClient
from openhands_agent.fields import PullRequestFields
from utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    create_pull_request_with_defaults,
    mock_response,
)


class GitLabClientTests(unittest.TestCase):
    def test_validate_connection_checks_configured_repository(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response()

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('group/subgroup', 'repo')

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/projects/group%2Fsubgroup%2Frepo')

    def test_create_pull_request_normalizes_response(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data={
                'iid': 9,
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                'web_url': 'https://gitlab.example/group/repo/-/merge_requests/9',
            }
        )

        with patch.object(client, '_post', return_value=response) as mock_post:
            pr = create_pull_request_with_defaults(
                client,
                repo_owner='group/subgroup',
                description='Ready for review',
            )

        self.assertEqual(
            pr,
            {
                PullRequestFields.ID: '9',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://gitlab.example/group/repo/-/merge_requests/9',
            },
        )
        assert_client_headers_and_timeout(self, client, 'gl-token', 30)
        mock_post.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests',
            json={
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                'source_branch': 'feature/proj-1',
                'target_branch': 'main',
                PullRequestFields.DESCRIPTION: 'Ready for review',
            },
        )

    def test_create_pull_request_retries_on_timeout(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data={
                'iid': 9,
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                'web_url': 'https://gitlab.example/group/repo/-/merge_requests/9',
            }
        )

        with patch.object(client, '_post', side_effect=[ClientTimeout('reset'), response]) as mock_post:
            pr = create_pull_request_with_defaults(client, repo_owner='group/subgroup')

        self.assertEqual(pr[PullRequestFields.ID], '9')
        self.assertEqual(mock_post.call_count, 2)

    def test_create_pull_request_raises_for_invalid_payload(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(json_data={PullRequestFields.TITLE: 'missing id'})

        with patch.object(client, '_post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
                create_pull_request_with_defaults(client, repo_owner='group/subgroup')

    def test_list_pull_request_comments_normalizes_response(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'id': 99,
                    'body': 'Please rename this variable.',
                    'author': {'username': 'reviewer'},
                }
            ]
        )

        with patch.object(client, '_get', return_value=response) as mock_get:
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '17')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].pull_request_id, '17')
        self.assertEqual(comments[0].comment_id, '99')
        self.assertEqual(comments[0].author, 'reviewer')
        self.assertEqual(comments[0].body, 'Please rename this variable.')
        mock_get.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests/17/notes',
            params={'sort': 'asc', 'order_by': 'created_at', 'per_page': 100},
        )
