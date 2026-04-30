import unittest
from unittest.mock import patch


from kato.client.gitlab.client import GitLabClient
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields
from utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    build_review_comment,
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
                    'id': 'discussion-1',
                    'resolved': False,
                    'notes': [
                        {
                            'id': 99,
                            'body': 'Please rename this variable.',
                            'author': {'username': 'reviewer'},
                        }
                    ],
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
        self.assertEqual(
            getattr(comments[0], ReviewCommentFields.RESOLUTION_TARGET_ID),
            'discussion-1',
        )
        mock_get.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests/17/discussions',
            params={'per_page': 100},
        )

    def test_list_pull_request_comments_skips_resolved_discussions(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'id': 'discussion-1',
                    'resolved': True,
                    'notes': [
                        {
                            'id': 99,
                            'body': 'Already handled',
                            'author': {'username': 'reviewer'},
                        }
                    ],
                }
            ]
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '17')

        self.assertEqual(comments, [])

    def test_find_pull_requests_filters_open_pull_requests_by_branch_and_title_prefix(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'iid': 9,
                    PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                    'web_url': 'https://gitlab.example/group/repo/-/merge_requests/9',
                    'source_branch': 'PROJ-1',
                },
                {
                    'iid': 10,
                    PullRequestFields.TITLE: 'OTHER-1 Fix bug',
                    'web_url': 'https://gitlab.example/group/repo/-/merge_requests/10',
                    'source_branch': 'OTHER-1',
                },
            ]
        )

        with patch.object(client, '_get', return_value=response) as mock_get:
            pull_requests = client.find_pull_requests(
                'group/subgroup',
                'repo',
                source_branch='PROJ-1',
                title_prefix='PROJ-1 ',
            )

        self.assertEqual(
            pull_requests,
            [
                {
                    PullRequestFields.ID: '9',
                    PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                    PullRequestFields.URL: 'https://gitlab.example/group/repo/-/merge_requests/9',
                }
            ],
        )
        mock_get.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests',
            params={'state': 'opened', 'per_page': 100, 'source_branch': 'PROJ-1'},
        )

    def test_resolve_review_comment_marks_discussion_resolved(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response()
        comment = build_review_comment(
            resolution_target_id='discussion-1',
            resolution_target_type='discussion',
            resolvable=True,
        )

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.resolve_review_comment('group/subgroup', 'repo', comment)

        response.raise_for_status.assert_called_once_with()
        mock_put.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests/17/discussions/discussion-1',
            json={'resolved': True},
        )

    def test_reply_to_review_comment_posts_discussion_note(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response()
        comment = build_review_comment(
            resolution_target_id='discussion-1',
            resolution_target_type='discussion',
            resolvable=True,
        )

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.reply_to_review_comment(
                'group/subgroup',
                'repo',
                comment,
                'Done. The custom field column now resizes correctly.',
            )

        response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests/17/discussions/discussion-1/notes',
            json={'body': 'Done. The custom field column now resizes correctly.'},
        )
