import unittest
from unittest.mock import patch

from provider_client_base.provider_client_base.data.fields import PullRequestFields, ReviewCommentFields

from github_core_lib.github_core_lib.client.github_client import GitHubClient
from tests.utils import (
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
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
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
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://github.com/owner/repo/pull/17',
            },
        )
        assert_client_headers_and_timeout(self, client, 'gh-token', 30)
        mock_post.assert_called_once_with(
            '/repos/owner/repo/pulls',
            json={
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
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
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
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

    def test_list_pull_request_comments_captures_file_path_and_line(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {
                            'nodes': [
                                {
                                    'id': 'thread-2',
                                    'isResolved': False,
                                    'path': 'src/main.py',
                                    'line': 42,
                                    'originalLine': 40,
                                    'comments': {
                                        'nodes': [
                                            {
                                                'databaseId': 55,
                                                'body': 'Style issue',
                                                'author': {'login': 'bot'},
                                                'commit': {'oid': 'abc123'},
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
            comments = client.list_pull_request_comments('owner', 'repo', '42')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].file_path, 'src/main.py')
        self.assertEqual(comments[0].line_number, 42)
        self.assertEqual(comments[0].line_type, 'added')
        self.assertEqual(comments[0].commit_sha, 'abc123')

    def test_list_pull_request_comments_falls_back_to_original_line(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {
                            'nodes': [
                                {
                                    'id': 'thread-3',
                                    'isResolved': False,
                                    'path': 'src/utils.py',
                                    'line': None,
                                    'originalLine': 10,
                                    'comments': {
                                        'nodes': [
                                            {
                                                'databaseId': 66,
                                                'body': 'Old line comment',
                                                'author': {'login': 'reviewer'},
                                                'commit': None,
                                                'originalCommit': {'oid': 'def456'},
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
            comments = client.list_pull_request_comments('owner', 'repo', '5')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].line_number, 10)
        self.assertEqual(comments[0].line_type, 'removed')
        self.assertEqual(comments[0].commit_sha, 'def456')

    def test_list_pull_request_comments_excludes_items_without_database_id(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {
                            'nodes': [
                                {
                                    'id': 'thread-4',
                                    'isResolved': False,
                                    'comments': {
                                        'nodes': [
                                            {
                                                'databaseId': None,
                                                'body': 'No id',
                                                'author': {'login': 'bot'},
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
            comments = client.list_pull_request_comments('owner', 'repo', '1')

        self.assertEqual(comments, [])

    def test_find_pull_requests_filters_open_pull_requests_by_branch_and_title_prefix(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(
            json_data=[
                {
                    'number': 17,
                    PullRequestFields.TITLE: 'PROJ-1 fix it already',
                    'html_url': 'https://github.com/owner/repo/pull/17',
                    'head': {'ref': 'PROJ-1'},
                },
                {
                    'number': 18,
                    PullRequestFields.TITLE: 'OTHER-1 Fix bug',
                    'html_url': 'https://github.com/owner/repo/pull/18',
                    'head': {'ref': 'OTHER-1'},
                },
            ]
        )

        with patch.object(client, '_get', return_value=response) as mock_get:
            pull_requests = client.find_pull_requests(
                'owner',
                'repo',
                source_branch='PROJ-1',
                title_prefix='PROJ-1 ',
            )

        self.assertEqual(
            pull_requests,
            [
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.TITLE: 'PROJ-1 fix it already',
                    PullRequestFields.URL: 'https://github.com/owner/repo/pull/17',
                }
            ],
        )
        mock_get.assert_called_once_with(
            '/repos/owner/repo/pulls',
            params={'state': 'open', 'per_page': 100, 'head': 'owner:PROJ-1'},
        )

    def test_find_pull_requests_returns_all_open_when_no_filters(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(
            json_data=[
                {
                    'number': 1,
                    PullRequestFields.TITLE: 'PR 1',
                    'html_url': 'https://github.com/owner/repo/pull/1',
                    'head': {'ref': 'branch-1'},
                },
                {
                    'number': 2,
                    PullRequestFields.TITLE: 'PR 2',
                    'html_url': 'https://github.com/owner/repo/pull/2',
                    'head': {'ref': 'branch-2'},
                },
            ]
        )

        with patch.object(client, '_get', return_value=response):
            pull_requests = client.find_pull_requests('owner', 'repo')

        self.assertEqual(len(pull_requests), 2)

    def test_find_pull_requests_returns_empty_for_non_list_payload(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(json_data={'error': 'unexpected'})

        with patch.object(client, '_get', return_value=response):
            result = client.find_pull_requests('owner', 'repo')

        self.assertEqual(result, [])

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

    def test_resolve_review_comment_looks_up_thread_when_id_missing(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        comment = build_review_comment(comment_id='99')
        thread_nodes = [
            {
                'id': 'thread-found',
                'isResolved': False,
                'comments': {
                    'nodes': [{'databaseId': 99, 'body': 'x', 'author': {'login': 'a'}}]
                },
            }
        ]
        graphql_payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {'nodes': thread_nodes}
                    }
                }
            }
        }

        with patch.object(client, '_graphql_with_retry', side_effect=[graphql_payload, {'data': {}}]) as mock_gql:
            client.resolve_review_comment('owner', 'repo', comment)

        self.assertEqual(mock_gql.call_count, 2)
        self.assertEqual(mock_gql.call_args.args[1], {'threadId': 'thread-found'})

    def test_resolve_review_comment_raises_when_thread_cannot_be_found(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        comment = build_review_comment(comment_id='999')
        graphql_payload = {
            'data': {
                'repository': {
                    'pullRequest': {
                        'reviewThreads': {'nodes': []}
                    }
                }
            }
        }

        with patch.object(client, '_graphql_with_retry', return_value=graphql_payload):
            with self.assertRaisesRegex(ValueError, 'unable to determine GitHub review thread'):
                client.resolve_review_comment('owner', 'repo', comment)

    def test_reply_to_review_comment_posts_rest_reply(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response()
        comment = build_review_comment(comment_id='99')

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.reply_to_review_comment(
                'owner',
                'repo',
                comment,
                'Done. Adjusted the resize line handling for RTL.',
            )

        response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            '/repos/owner/repo/pulls/17/comments/99/replies',
            json={'body': 'Done. Adjusted the resize line handling for RTL.'},
        )

    def test_graphql_url_uses_enterprise_endpoint_when_rest_base_uses_api_v3(self) -> None:
        client = GitHubClient('https://github.example/api/v3', 'gh-token')

        self.assertEqual(
            client._graphql_url(),
            'https://github.example/api/graphql',
        )

    def test_graphql_url_uses_cloud_graphql_for_standard_base(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')

        self.assertEqual(client._graphql_url(), 'https://api.github.com/graphql')

    def test_graphql_url_handles_api_suffix(self) -> None:
        client = GitHubClient('https://github.example/api', 'gh-token')

        self.assertEqual(client._graphql_url(), 'https://github.example/api/graphql')

    def test_graphql_url_preserves_existing_graphql_path(self) -> None:
        client = GitHubClient('https://github.example/api/graphql', 'gh-token')

        self.assertEqual(client._graphql_url(), 'https://github.example/api/graphql')

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

    def test_graphql_request_raises_for_non_dict_payload(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(json_data=['unexpected', 'list'])

        with patch.object(client.session, 'post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'invalid GitHub GraphQL response payload'):
                client._graphql_with_retry('query { viewer { login } }', {})

    def test_review_thread_nodes_raises_for_non_numeric_pr_id(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')

        with self.assertRaisesRegex(ValueError, 'invalid GitHub pull request id'):
            client._review_thread_nodes('owner', 'repo', 'not-a-number')


class GitHubClientDefensiveBranchTests(unittest.TestCase):
    def test_find_pull_requests_skips_non_dict_entries(self) -> None:
        # Line 124: ``if not isinstance(item, dict): continue``.
        client = GitHubClient('https://api.github.com', 'gh-token')
        valid_pr = {
            'number': 17, 'title': 'feat: foo',
            'head': {'ref': 'feat/foo'}, 'base': {'ref': 'main'},
            'body': '', 'html_url': 'https://github/pr/17',
            'state': 'open', 'merged': False,
        }
        get_response = mock_response(json_data=['not a dict', valid_pr])
        with patch.object(client, '_get_with_retry', return_value=get_response):
            results = client.find_pull_requests('octo', 'repo')
        self.assertEqual(len(results), 1)

    def test_find_pull_requests_skips_when_title_prefix_mismatch(self) -> None:
        # Line 131: title doesn't start with prefix → skip.
        client = GitHubClient('https://api.github.com', 'gh-token')
        pr_keep = {
            'number': 1, 'title': 'PROJ-1: keep',
            'head': {'ref': 'feat/1'}, 'base': {'ref': 'main'},
            'body': '', 'html_url': 'u',
            'state': 'open', 'merged': False,
        }
        pr_drop = {
            'number': 2, 'title': 'other: drop',
            'head': {'ref': 'feat/2'}, 'base': {'ref': 'main'},
            'body': '', 'html_url': 'u',
            'state': 'open', 'merged': False,
        }
        get_response = mock_response(json_data=[pr_keep, pr_drop])
        with patch.object(client, '_get_with_retry', return_value=get_response):
            results = client.find_pull_requests(
                'octo', 'repo', title_prefix='PROJ-1:',
            )
        self.assertEqual(len(results), 1)

    def test_normalize_comments_returns_empty_for_non_list(self) -> None:
        # Line 182: non-list payload short-circuits.
        result = GitHubClient._normalize_comments({'not': 'a list'}, '17')
        self.assertEqual(result, [])

    def test_normalize_comments_skips_non_dict_nodes(self) -> None:
        # Line 203: a node in the thread's comments list isn't a dict → skip.
        thread_with_junk_node = {
            'id': 'thread-1',
            'isResolved': False,
            'path': 'src/a.py',
            'line': 10,
            'comments': {
                'nodes': [
                    'not a dict',
                    {
                        'id': 'note-1', 'databaseId': 1,
                        'body': 'ok', 'author': {'login': 'alice'},
                        'commit': {'oid': 'abc'},
                    },
                ],
            },
        }
        result = GitHubClient._normalize_comments([thread_with_junk_node], '17')
        self.assertEqual(len(result), 1)

    def test_discussion_id_skips_non_dict_threads(self) -> None:
        # Line 257: skips non-dict entries in the thread search.
        client = GitHubClient('https://api.github.com', 'gh-token')
        valid_thread = {
            'id': 'thread-1',
            'comments': {'nodes': [{'databaseId': 99}]},
        }
        with patch.object(
            client, '_review_thread_nodes',
            return_value=['junk-not-a-dict', valid_thread],
        ):
            result = client._thread_id_for_comment('owner', 'repo', '17', '99')
        self.assertEqual(result, 'thread-1')

    def test_thread_id_continues_loop_when_first_thread_has_no_match(self) -> None:
        # Branch 303->298: the ``any(...)`` check at line 303 returns
        # False for the first thread, so the loop continues to the
        # next iteration (back-edge to line 298).
        client = GitHubClient('https://api.github.com', 'gh-token')
        non_matching_thread = {
            'id': 'thread-other',
            'comments': {'nodes': [{'databaseId': 42}]},
        }
        matching_thread = {
            'id': 'thread-target',
            'comments': {'nodes': [{'databaseId': 99}]},
        }
        with patch.object(
            client, '_review_thread_nodes',
            return_value=[non_matching_thread, matching_thread],
        ):
            result = client._thread_id_for_comment('owner', 'repo', '17', '99')
        self.assertEqual(result, 'thread-target')

    def test_graphql_returns_payload_when_no_errors(self) -> None:
        # Line 292: the happy path — no ``errors`` key → return the raw payload.
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {'data': {'viewer': {'login': 'octocat'}}}
        response = mock_response(json_data=payload)
        with patch.object(client.session, 'post', return_value=response):
            result = client._graphql_with_retry('query { viewer { login } }', {})
        self.assertEqual(result, payload)

    def test_graphql_raises_on_errors_array(self) -> None:
        # Line 292: ``payload`` contains ``errors`` → wrap as RuntimeError.
        client = GitHubClient('https://api.github.com', 'gh-token')
        response = mock_response(json_data={
            'errors': [{'message': 'syntax error in query'}],
        })
        with patch.object(client.session, 'post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'syntax error'):
                client._graphql_with_retry('query { viewer { login } }', {})

    def test_graphql_url_appends_graphql_to_bare_host(self) -> None:
        # Line 306: base_url ends with neither ``/graphql`` nor ``/api/v3`` →
        # append ``/graphql`` to the existing path.
        client = GitHubClient('https://ghe.example.com/custom/path', 'gh-token')
        url = client._graphql_url()
        self.assertTrue(url.endswith('/graphql'))
        self.assertIn('/custom/path', url)
