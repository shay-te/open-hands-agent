import unittest
from unittest.mock import patch

from provider_client_base.provider_client_base.data.fields import PullRequestFields, ReviewCommentFields

from gitlab_core_lib.gitlab_core_lib.client.gitlab_client import GitLabClient
from tests.utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    build_review_comment,
    create_pull_request_with_defaults,
    mock_response,
)


class GitLabClientValidateConnectionTests(unittest.TestCase):
    def test_checks_project_endpoint(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response()

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('group/subgroup', 'repo')

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/projects/group%2Fsubgroup%2Frepo')

    def test_url_encodes_nested_group_paths(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response()

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('a/b/c', 'repo')

        mock_get.assert_called_once_with('/projects/a%2Fb%2Fc%2Frepo')


class GitLabClientCreatePullRequestTests(unittest.TestCase):
    def test_normalizes_response(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data={
                'iid': 9,
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
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
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://gitlab.example/group/repo/-/merge_requests/9',
            },
        )
        assert_client_headers_and_timeout(self, client, 'gl-token', 30)
        mock_post.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests',
            json={
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                'source_branch': 'feature/proj-1',
                'target_branch': 'main',
                PullRequestFields.DESCRIPTION: 'Ready for review',
            },
        )

    def test_retries_on_timeout(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data={
                'iid': 9,
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                'web_url': 'https://gitlab.example/group/repo/-/merge_requests/9',
            }
        )

        with patch.object(client, '_post', side_effect=[ClientTimeout('reset'), response]) as mock_post:
            pr = create_pull_request_with_defaults(client, repo_owner='group/subgroup')

        self.assertEqual(pr[PullRequestFields.ID], '9')
        self.assertEqual(mock_post.call_count, 2)

    def test_raises_for_invalid_payload(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(json_data={PullRequestFields.TITLE: 'missing id'})

        with patch.object(client, '_post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
                create_pull_request_with_defaults(client, repo_owner='group/subgroup')


class GitLabClientListPullRequestCommentsTests(unittest.TestCase):
    def test_normalizes_discussion_notes(self) -> None:
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
            params={'per_page': 100, 'page': 1},
        )

    def test_skips_resolved_discussions(self) -> None:
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

    def test_skips_system_notes(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'id': 'discussion-1',
                    'resolved': False,
                    'notes': [
                        {
                            'id': 77,
                            'body': 'assigned to @alice',
                            'author': {'username': 'gitlab'},
                            'system': True,
                        },
                        {
                            'id': 78,
                            'body': 'Real comment',
                            'author': {'username': 'alice'},
                        },
                    ],
                }
            ]
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '17')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].comment_id, '78')

    def test_captures_file_path_and_new_line(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'id': 'discussion-2',
                    'resolved': False,
                    'notes': [
                        {
                            'id': 55,
                            'body': 'Style issue',
                            'author': {'username': 'bot'},
                            'position': {
                                'new_path': 'src/main.py',
                                'new_line': 42,
                                'head_sha': 'abc123',
                            },
                        }
                    ],
                }
            ]
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '5')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].file_path, 'src/main.py')
        self.assertEqual(comments[0].line_number, 42)
        self.assertEqual(comments[0].line_type, 'added')
        self.assertEqual(comments[0].commit_sha, 'abc123')

    def test_falls_back_to_old_line_and_old_path(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'id': 'discussion-3',
                    'resolved': False,
                    'notes': [
                        {
                            'id': 66,
                            'body': 'Deleted line comment',
                            'author': {'username': 'reviewer'},
                            'position': {
                                'old_path': 'src/old.py',
                                'old_line': 10,
                                'start_sha': 'def456',
                            },
                        }
                    ],
                }
            ]
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '5')

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].file_path, 'src/old.py')
        self.assertEqual(comments[0].line_number, 10)
        self.assertEqual(comments[0].line_type, 'removed')
        self.assertEqual(comments[0].commit_sha, 'def456')

    def test_excludes_notes_without_id(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'id': 'discussion-4',
                    'resolved': False,
                    'notes': [
                        {
                            'id': None,
                            'body': 'No id note',
                            'author': {'username': 'bot'},
                        }
                    ],
                }
            ]
        )

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '1')

        self.assertEqual(comments, [])

    def test_returns_empty_for_non_list_payload(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(json_data={'error': 'not a list'})

        with patch.object(client, '_get', return_value=response):
            comments = client.list_pull_request_comments('group/subgroup', 'repo', '1')

        self.assertEqual(comments, [])


class GitLabClientFindPullRequestsTests(unittest.TestCase):
    def test_filters_by_branch_and_title_prefix(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {
                    'iid': 9,
                    PullRequestFields.TITLE: 'PROJ-1 fix it already',
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
                    PullRequestFields.TITLE: 'PROJ-1 fix it already',
                    PullRequestFields.URL: 'https://gitlab.example/group/repo/-/merge_requests/9',
                }
            ],
        )
        mock_get.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests',
            params={'state': 'opened', 'per_page': 100, 'source_branch': 'PROJ-1'},
        )

    def test_returns_all_open_when_no_filters(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(
            json_data=[
                {'iid': 1, PullRequestFields.TITLE: 'MR 1', 'web_url': 'https://u/1', 'source_branch': 'b1'},
                {'iid': 2, PullRequestFields.TITLE: 'MR 2', 'web_url': 'https://u/2', 'source_branch': 'b2'},
            ]
        )

        with patch.object(client, '_get', return_value=response):
            prs = client.find_pull_requests('owner', 'repo')

        self.assertEqual(len(prs), 2)

    def test_returns_empty_for_non_list_payload(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(json_data={'unexpected': True})

        with patch.object(client, '_get', return_value=response):
            result = client.find_pull_requests('owner', 'repo')

        self.assertEqual(result, [])

    def test_sends_no_branch_param_when_source_branch_empty(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.find_pull_requests('owner', 'repo')

        params = mock_get.call_args.kwargs['params']
        self.assertNotIn('source_branch', params)


class GitLabClientResolveCommentTests(unittest.TestCase):
    def test_marks_discussion_resolved_using_stored_id(self) -> None:
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

    def test_looks_up_discussion_when_id_missing(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        comment = build_review_comment(comment_id='99')
        discussions_payload = [
            {
                'id': 'disc-found',
                'resolved': False,
                'notes': [{'id': 99, 'body': 'x', 'author': {'username': 'a'}}],
            }
        ]

        resolve_response = mock_response()
        get_response = mock_response(json_data=discussions_payload)

        with patch.object(client, '_get', return_value=get_response):
            with patch.object(client, '_put', return_value=resolve_response) as mock_put:
                client.resolve_review_comment('group/subgroup', 'repo', comment)

        mock_put.assert_called_once_with(
            '/projects/group%2Fsubgroup%2Frepo/merge_requests/17/discussions/disc-found',
            json={'resolved': True},
        )

    def test_raises_when_discussion_cannot_be_found(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        comment = build_review_comment(comment_id='999')
        get_response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=get_response):
            with self.assertRaisesRegex(ValueError, 'unable to determine GitLab discussion'):
                client.resolve_review_comment('group/subgroup', 'repo', comment)


class GitLabClientReplyToCommentTests(unittest.TestCase):
    def test_posts_note_to_discussion(self) -> None:
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


class GitLabClientDefensiveBranchTests(unittest.TestCase):
    def test_find_pull_requests_skips_non_dict_entries_in_payload(self) -> None:
        # Line 78: ``if not isinstance(item, dict): continue``.
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        valid_pr = {
            'iid': 17, 'title': 'feat: foo', 'source_branch': 'feat/foo',
            'target_branch': 'main', 'description': '',
            'web_url': 'https://gitlab/pr/17',
            'state': 'opened', 'merged': False,
        }
        get_response = mock_response(json_data=['junk-not-a-dict', valid_pr])
        with patch.object(client, '_get_with_retry', return_value=get_response):
            results = client.find_pull_requests('grp', 'repo')
        self.assertEqual(len(results), 1)

    def test_find_pull_requests_skips_when_title_does_not_match_prefix(self) -> None:
        # Line 83: title doesn't start with the requested prefix → skip.
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        pr_keep = {
            'iid': 1, 'title': 'PROJ-1: keep me',
            'source_branch': 'feat/1', 'target_branch': 'main',
            'description': '', 'web_url': 'u', 'state': 'opened', 'merged': False,
        }
        pr_drop = {
            'iid': 2, 'title': 'random: drop me',
            'source_branch': 'feat/2', 'target_branch': 'main',
            'description': '', 'web_url': 'u', 'state': 'opened', 'merged': False,
        }
        get_response = mock_response(json_data=[pr_keep, pr_drop])
        with patch.object(client, '_get_with_retry', return_value=get_response):
            results = client.find_pull_requests(
                'grp', 'repo', title_prefix='PROJ-1:',
            )
        self.assertEqual(len(results), 1)

    def test_normalize_comments_returns_empty_for_non_list_payload(self) -> None:
        # Line 149: defensive ``if not isinstance(payload, list): return []``.
        result = GitLabClient._normalize_comments({'not': 'a list'}, '17')
        self.assertEqual(result, [])

    def test_discussion_id_for_comment_skips_non_dict_entries(self) -> None:
        # Line 226: skips garbage entries in the discussion payload list.
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        valid_discussion = {
            'id': 'disc-1',
            'notes': [{'id': 'note-id', 'body': 'hi'}],
        }
        with patch.object(
            client, '_discussion_payload',
            return_value=['not a dict', valid_discussion],
        ):
            result = client._discussion_id_for_comment('grp', 'repo', '17', 'note-id')
        self.assertEqual(result, 'disc-1')


class GitLabIssuesClientJsonItemsTests(unittest.TestCase):
    def test_returns_empty_when_items_key_set_but_payload_not_dict(self) -> None:
        # Line 260: items_key requested but payload isn't a dict → [].
        from gitlab_core_lib.gitlab_core_lib.client.gitlab_issues_client import (
            GitLabIssuesClient,
        )
        response = mock_response(json_data=['junk'])
        result = GitLabIssuesClient._json_items(response, items_key='items')
        self.assertEqual(result, [])
