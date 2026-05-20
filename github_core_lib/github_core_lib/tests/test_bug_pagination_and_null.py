"""Regression: GitHub GraphQL reviewThreads paginates + null data raises.

Before the fix:
  - `reviewThreads(first: 100)` capped at 100, silently truncating
    PRs with more threads.
  - A null ``data.repository`` or null ``pullRequest`` (permission
    denied, deleted) was treated as "no threads," masking the failure.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from github_core_lib.github_core_lib.client.github_client import GitHubClient


def _thread(idx: int):
    return {
        'id': f'thread-{idx}',
        'isResolved': False,
        'path': f'src/file{idx}.py',
        'line': idx,
        'originalLine': idx,
        'comments': {
            'nodes': [{
                'databaseId': 1000 + idx,
                'body': f'comment {idx}',
                'author': {'login': 'reviewer'},
                'commit': {'oid': 'abc'},
                'originalCommit': {'oid': 'abc'},
            }],
        },
    }


def _payload_page(threads, has_next=False, end_cursor=None):
    return {
        'data': {
            'repository': {
                'pullRequest': {
                    'reviewThreads': {
                        'pageInfo': {
                            'hasNextPage': has_next,
                            'endCursor': end_cursor,
                        },
                        'nodes': threads,
                    },
                },
            },
        },
    }


class GitHubReviewThreadsPaginationTests(unittest.TestCase):

    def test_single_page_stops_when_has_next_page_false(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        page = _payload_page([_thread(i) for i in range(1, 6)], has_next=False)

        with patch.object(client, '_graphql_with_retry', return_value=page) as mock_q:
            comments = client.list_pull_request_comments('owner', 'repo', '17')

        self.assertEqual(mock_q.call_count, 1)
        # First call has no cursor.
        self.assertIsNone(mock_q.call_args_list[0].args[1]['cursor'])
        # 5 comments mapped from 5 threads.
        self.assertEqual(len(comments), 5)

    def test_has_next_page_but_missing_end_cursor_breaks_loop(self) -> None:
        # Adversarial GraphQL response: ``hasNextPage`` says yes but
        # ``endCursor`` is missing/empty. Without the ``if not cursor:
        # break`` guard the loop would re-issue the same cursorless
        # query and spin forever (or until GitHub rate-limited).
        client = GitHubClient('https://api.github.com', 'gh-token')
        page = _payload_page(
            [_thread(1)], has_next=True, end_cursor=None,
        )

        with patch.object(client, '_graphql_with_retry', return_value=page) as mock_q:
            comments = client.list_pull_request_comments('owner', 'repo', '17')

        # Single call — the missing cursor must short-circuit the loop.
        self.assertEqual(mock_q.call_count, 1)
        self.assertEqual(len(comments), 1)

    def test_multi_page_follows_endCursor_until_done(self) -> None:
        client = GitHubClient('https://api.github.com', 'gh-token')
        page1 = _payload_page(
            [_thread(i) for i in range(1, 101)],
            has_next=True, end_cursor='cursor-A',
        )
        page2 = _payload_page(
            [_thread(i) for i in range(101, 106)],
            has_next=False,
        )

        with patch.object(
            client, '_graphql_with_retry', side_effect=[page1, page2],
        ) as mock_q:
            comments = client.list_pull_request_comments('owner', 'repo', '17')

        self.assertEqual(len(comments), 105)
        self.assertEqual(mock_q.call_count, 2)
        # Second call passes the endCursor from page 1.
        self.assertEqual(mock_q.call_args_list[1].args[1]['cursor'], 'cursor-A')


class GitHubNullDataRaiseTests(unittest.TestCase):

    def test_null_data_raises_RuntimeError(self) -> None:
        # GraphQL error: data is null at the top level.
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {'data': None, 'errors': [{'message': 'auth required'}]}

        with patch.object(client, '_graphql_with_retry', return_value=payload):
            with self.assertRaisesRegex(RuntimeError, 'no data'):
                client.list_pull_request_comments('owner', 'repo', '17')

    def test_null_repository_raises(self) -> None:
        # The token is valid but the repo doesn't exist or no access.
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {'data': {'repository': None}}

        with patch.object(client, '_graphql_with_retry', return_value=payload):
            with self.assertRaisesRegex(RuntimeError, 'null repository'):
                client.list_pull_request_comments('owner', 'repo', '17')

    def test_null_pull_request_raises(self) -> None:
        # Repo exists but the specific PR doesn't.
        client = GitHubClient('https://api.github.com', 'gh-token')
        payload = {'data': {'repository': {'pullRequest': None}}}

        with patch.object(client, '_graphql_with_retry', return_value=payload):
            with self.assertRaisesRegex(RuntimeError, 'null pullRequest'):
                client.list_pull_request_comments('owner', 'repo', '17')


if __name__ == '__main__':
    unittest.main()
