import unittest
from unittest.mock import patch


from kato.client.bitbucket.client import BITBUCKET_PAGE_LENGTH, BitbucketClient
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields
from utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    assert_client_basic_auth_and_timeout,
    build_review_comment,
    create_pull_request_with_defaults,
    mock_response,
)


class BitbucketClientTests(unittest.TestCase):
    def test_uses_configured_retry_count(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token', max_retries=5)
        self.assertEqual(client.max_retries, 5)

    def test_uses_minimum_retry_count_of_one(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token', max_retries=0)
        self.assertEqual(client.max_retries, 1)

    def test_uses_basic_auth_when_username_is_configured(self) -> None:
        client = BitbucketClient(
            'https://bitbucket.example',
            'bb-token',
            username='bb-user',
        )

        assert_client_basic_auth_and_timeout(self, client, 'bb-user', 'bb-token', 30)

    def test_validate_connection_checks_configured_repository(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response()

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('workspace', 'repo')

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/repositories/workspace/repo')

    def test_create_pull_request_normalizes_response(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': {'html': {'href': 'https://bitbucket/pr/7'}},
        })

        with patch.object(client, '_post', return_value=response) as mock_post:
            pr = create_pull_request_with_defaults(client, description='Ready for review')

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            pr,
            {
                PullRequestFields.ID: '7',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/7',
            },
        )
        assert_client_headers_and_timeout(self, client, 'bb-token', 30)
        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests',
            json={
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.DESCRIPTION: 'Ready for review',
                'source': {'branch': {'name': 'feature/proj-1'}},
                'destination': {'branch': {'name': 'main'}},
            },
        )

    def test_create_pull_request_retries_on_timeout(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': {'html': {'href': 'https://bitbucket/pr/7'}},
        })

        with patch.object(
            client,
            '_post',
            side_effect=[ClientTimeout('connection reset'), response],
        ) as mock_post:
            pr = create_pull_request_with_defaults(client)

        self.assertEqual(pr[PullRequestFields.ID], '7')
        self.assertEqual(mock_post.call_count, 2)

    def test_create_pull_request_retries_on_transient_status_code(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        retry_response = mock_response(status_code=503)
        success_response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': {'html': {'href': 'https://bitbucket/pr/7'}},
        })

        with patch.object(
            client,
            '_post',
            side_effect=[retry_response, success_response],
        ) as mock_post:
            pr = create_pull_request_with_defaults(client)

        self.assertEqual(pr[PullRequestFields.URL], 'https://bitbucket/pr/7')
        self.assertEqual(mock_post.call_count, 2)

    def test_create_pull_request_raises_for_invalid_payload(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={PullRequestFields.TITLE: 'missing id'})

        with patch.object(client, '_post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
                create_pull_request_with_defaults(client)

    def test_create_pull_request_defaults_missing_url_to_empty(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': {},
        })

        with patch.object(client, '_post', return_value=response):
            pr = create_pull_request_with_defaults(client)

        self.assertEqual(pr[PullRequestFields.URL], '')

    def test_create_pull_request_defaults_missing_url_for_malformed_links(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': ['unexpected'],
        })

        with patch.object(client, '_post', return_value=response):
            pr = create_pull_request_with_defaults(client)

        self.assertEqual(pr[PullRequestFields.URL], '')

    def test_create_pull_request_stringifies_title_and_url(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 123,
            'links': {'html': {'href': 456}},
        })

        with patch.object(client, '_post', return_value=response):
            pr = create_pull_request_with_defaults(client)

        self.assertEqual(pr[PullRequestFields.TITLE], '123')
        self.assertEqual(pr[PullRequestFields.URL], '456')

    def test_create_pull_request_does_not_retry_non_transient_exception(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')

        with patch.object(
            client,
            '_post',
            side_effect=ValueError('invalid request'),
        ) as mock_post:
            with self.assertRaisesRegex(ValueError, 'invalid request'):
                create_pull_request_with_defaults(client)

        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests',
            json={
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.DESCRIPTION: '',
                'source': {'branch': {'name': 'feature/proj-1'}},
                'destination': {'branch': {'name': 'main'}},
            },
        )

    def test_create_pull_request_omits_destination_when_branch_is_missing(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(json_data={
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': {'html': {'href': 'https://bitbucket/pr/7'}},
        })

        with patch.object(client, '_post', return_value=response) as mock_post:
            create_pull_request_with_defaults(client, destination_branch=None)

        self.assertNotIn('destination', mock_post.call_args.kwargs['json'])

    def test_list_pull_request_comments_normalizes_response(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(
            json_data={
                'values': [
                    {
                        'id': 99,
                        'content': {'raw': 'Please rename this variable.'},
                        'user': {'display_name': 'reviewer'},
                    }
                ]
            }
        )

        with patch.object(client, '_get', return_value=response) as mock_get:
            comments = client.list_pull_request_comments('workspace', 'repo', '17')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].pull_request_id, '17')
        self.assertEqual(comments[0].comment_id, '99')
        self.assertEqual(comments[0].author, 'reviewer')
        self.assertEqual(comments[0].body, 'Please rename this variable.')
        self.assertEqual(
            getattr(comments[0], ReviewCommentFields.RESOLUTION_TARGET_ID),
            '99',
        )
        mock_get.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests/17/comments',
            params={'pagelen': BITBUCKET_PAGE_LENGTH, 'sort': 'created_on'},
        )

    def test_list_pull_request_comments_uses_root_comment_as_resolution_target(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(
            json_data={
                'values': [
                    {
                        'id': 101,
                        'parent': {'id': 99},
                        'content': {'raw': 'Follow-up reply'},
                        'user': {'display_name': 'reviewer'},
                    }
                ]
            }
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('workspace', 'repo', '17')

        self.assertEqual(
            getattr(comments[0], ReviewCommentFields.RESOLUTION_TARGET_ID),
            '99',
        )

    def test_list_pull_request_comments_skips_resolved_threads(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(
            json_data={
                'values': [
                    {
                        'id': 99,
                        'resolution': {'type': 'resolved'},
                        'content': {'raw': 'Already handled'},
                        'user': {'display_name': 'reviewer'},
                    }
                ]
            }
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('workspace', 'repo', '17')

        self.assertEqual(comments, [])

    def test_find_pull_requests_filters_open_pull_requests_by_branch_and_title_prefix(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response(
            json_data={
                'values': [
                    {
                        'id': 17,
                        PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                        'links': {'html': {'href': 'https://bitbucket/pr/17'}},
                        'source': {'branch': {'name': 'PROJ-1'}},
                    },
                    {
                        'id': 18,
                        PullRequestFields.TITLE: 'OTHER-1 Fix bug',
                        'links': {'html': {'href': 'https://bitbucket/pr/18'}},
                        'source': {'branch': {'name': 'OTHER-1'}},
                    },
                ]
            }
        )

        with patch.object(client, '_get', return_value=response) as mock_get:
            pull_requests = client.find_pull_requests(
                'workspace',
                'repo',
                source_branch='PROJ-1',
                title_prefix='PROJ-1 ',
            )

        self.assertEqual(
            pull_requests,
            [
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                    PullRequestFields.URL: 'https://bitbucket/pr/17',
                }
            ],
        )
        mock_get.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests',
            params={'pagelen': BITBUCKET_PAGE_LENGTH},
        )

    def test_resolve_review_comment_posts_to_resolution_endpoint(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response()
        comment = build_review_comment(
            resolution_target_id='99',
            resolution_target_type='comment',
            resolvable=True,
        )

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.resolve_review_comment('workspace', 'repo', comment)

        response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests/17/comments/99/resolve',
        )

    def test_reply_to_review_comment_posts_thread_reply(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response()
        response.ok = True
        comment = build_review_comment(
            resolution_target_id='99',
            resolution_target_type='comment',
            resolvable=True,
        )

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.reply_to_review_comment(
                'workspace',
                'repo',
                comment,
                'Done. Added support for creating new options.',
            )

        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests/17/comments',
            json={
                'content': {'raw': 'Done. Added support for creating new options.'},
                'parent': {'id': 99},
            },
        )

    def test_reply_to_review_comment_raises_with_response_body_on_non_ok(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = mock_response()
        response.ok = False
        response.status_code = 400
        response.text = '{"type":"error","error":{"message":"parent comment is a reply"}}'
        comment = build_review_comment(
            resolution_target_id='99',
            resolution_target_type='comment',
            resolvable=True,
        )

        with patch.object(client, '_post', return_value=response):
            with self.assertRaisesRegex(RuntimeError,
                                        'bitbucket rejected reply.*HTTP 400'):
                client.reply_to_review_comment(
                    'workspace', 'repo', comment, 'fixed it',
                )
