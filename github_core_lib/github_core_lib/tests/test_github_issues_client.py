import unittest
from unittest.mock import patch

from github_core_lib.github_core_lib.client.github_issues_client import GitHubIssuesClient
from github_core_lib.github_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    GitHubCommentFields,
    GitHubIssueFields,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord
from tests.utils import mock_response


def _make_client(**kwargs) -> GitHubIssuesClient:
    return GitHubIssuesClient(
        'https://api.github.com',
        'gh-token',
        'workspace',
        'repo',
        **kwargs,
    )


def _issue(
    number: int = 17,
    title: str = 'fix it already',
    body: str = 'Details',
    state: str = 'open',
    labels=None,
    pull_request=None,
) -> dict:
    issue: dict = {
        GitHubIssueFields.NUMBER: number,
        GitHubIssueFields.TITLE: title,
        GitHubIssueFields.BODY: body,
        GitHubIssueFields.STATE: state,
        GitHubIssueFields.LABELS: labels if labels is not None else [],
    }
    if pull_request is not None:
        issue[GitHubIssueFields.PULL_REQUEST] = pull_request
    return issue


class GitHubIssuesClientInitTests(unittest.TestCase):
    def test_stores_owner_and_repo(self) -> None:
        client = _make_client()
        self.assertEqual(client._owner, 'workspace')
        self.assertEqual(client._repo, 'repo')

    def test_strips_whitespace_from_owner_and_repo(self) -> None:
        client = GitHubIssuesClient(
            'https://api.github.com', 'tok', '  myorg  ', '  myrepo  '
        )
        self.assertEqual(client._owner, 'myorg')
        self.assertEqual(client._repo, 'myrepo')

    def test_sets_bearer_token_and_accept_headers(self) -> None:
        client = _make_client()
        self.assertEqual(
            client.headers,
            {
                'Authorization': 'Bearer gh-token',
                'Accept': 'application/vnd.github+json',
            },
        )

    def test_sets_timeout_to_30(self) -> None:
        client = _make_client()
        self.assertEqual(client.timeout, 30)

    def test_default_is_operational_comment_always_false(self) -> None:
        client = _make_client()
        self.assertFalse(client._is_operational_comment('any text'))

    def test_custom_is_operational_comment_used(self) -> None:
        client = _make_client(is_operational_comment=lambda body: 'SKIP' in body)
        self.assertTrue(client._is_operational_comment('SKIP this'))
        self.assertFalse(client._is_operational_comment('keep this'))


class GitHubIssuesClientValidateConnectionTests(unittest.TestCase):
    def test_validate_connection_checks_repository_issues(self) -> None:
        client = _make_client()
        response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('repo', 'octocat', ['open'])

        mock_get.assert_called_once_with(
            '/repos/workspace/repo/issues',
            params={'assignee': 'octocat', 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status.assert_called_once_with()


class GitHubIssuesClientGetAssignedTasksTests(unittest.TestCase):
    def test_returns_issue_records(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[_issue()])
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], IssueRecord)
        self.assertEqual(records[0].id, '17')
        self.assertEqual(records[0].summary, 'fix it already')

    def test_filters_out_pull_requests(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[
                _issue(number=17, state='open'),
                _issue(number=18, state='open', pull_request={'url': 'https://api.github.com/pr/18'}),
            ]
        )
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, '17')

    def test_filters_by_allowed_states(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[
                _issue(number=1, state='open'),
                _issue(number=2, state='closed'),
            ]
        )
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, '1')

    def test_returns_all_states_when_states_empty(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[
                _issue(number=1, state='open'),
                _issue(number=2, state='closed'),
            ]
        )
        comments_response_1 = mock_response(json_data=[])
        comments_response_2 = mock_response(json_data=[])

        with patch.object(
            client, '_get', side_effect=[issues_response, comments_response_1, comments_response_2]
        ):
            records = client.get_assigned_tasks('repo', 'octocat', [])

        self.assertEqual(len(records), 2)

    def test_loads_comments_and_appends_to_description(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[_issue()])
        comments_response = mock_response(
            json_data=[
                {
                    GitHubCommentFields.BODY: 'Please add tests.',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'reviewer'},
                }
            ]
        )

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertIn('reviewer: Please add tests.', records[0].description)

    def test_uses_labels_as_tags(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[
                _issue(
                    labels=[
                        {GitHubIssueFields.NAME: 'repo:client'},
                        {GitHubIssueFields.NAME: 'priority:high'},
                    ]
                )
            ]
        )
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(records[0].tags, ['repo:client', 'priority:high'])

    def test_operational_comments_excluded_from_description_but_in_all_comments(self) -> None:
        client = _make_client(
            is_operational_comment=lambda body: 'agent could not safely process' in body
        )
        issues_response = mock_response(json_data=[_issue()])
        comments_response = mock_response(
            json_data=[
                {
                    GitHubCommentFields.BODY: 'agent could not safely process this task: timeout',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'shay'},
                },
                {
                    GitHubCommentFields.BODY: 'Please add tests.',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'reviewer'},
                },
            ]
        )

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertIn('reviewer: Please add tests.', records[0].description)
        self.assertNotIn('could not safely process', records[0].description)
        all_comments = getattr(records[0], ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'shay')

    def test_handles_non_dict_items_gracefully(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=['not-a-dict', None, _issue()])
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(records), 1)

    def test_sends_correct_params_to_issues_endpoint(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=issues_response) as mock_get:
            client.get_assigned_tasks('repo', 'octocat', ['open'])

        mock_get.assert_called_once_with(
            '/repos/workspace/repo/issues',
            params={
                'assignee': 'octocat',
                'state': 'all',
                'sort': 'updated',
                'direction': 'desc',
                'per_page': 100,
            },
        )

    def test_returns_empty_list_when_no_issues(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=issues_response):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(records, [])

    def test_skips_malformed_issues_that_raise(self) -> None:
        client = _make_client()
        # Issue missing the required NUMBER key will raise KeyError in _to_record.
        malformed = {GitHubIssueFields.STATE: 'open'}
        good = _issue(number=42, state='open')
        issues_response = mock_response(json_data=[malformed, good])
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, '42')


class GitHubIssuesClientAddCommentTests(unittest.TestCase):
    def test_posts_comment_body(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('17', 'Ready for review')

        mock_post.assert_called_once_with(
            '/repos/workspace/repo/issues/17/comments',
            json={GitHubCommentFields.BODY: 'Ready for review'},
        )
        response.raise_for_status.assert_called_once_with()


class GitHubIssuesClientAddTagTests(unittest.TestCase):
    def test_posts_label_to_issue(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_tag('17', 'in-progress')

        mock_post.assert_called_once_with(
            '/repos/workspace/repo/issues/17/labels',
            json={'labels': ['in-progress']},
        )
        response.raise_for_status.assert_called_once_with()

    def test_raises_on_non_success(self) -> None:
        client = _make_client()
        response = mock_response(status_code=422)

        with patch.object(client, '_post', return_value=response):
            client.add_tag('17', 'bad-label')

        response.raise_for_status.assert_called_once_with()


class GitHubIssuesClientRemoveTagTests(unittest.TestCase):
    def test_returns_on_200(self) -> None:
        client = _make_client()
        response = mock_response(status_code=200)

        with patch.object(client, '_delete', return_value=response):
            client.remove_tag('17', 'in-progress')

        response.raise_for_status.assert_not_called()

    def test_returns_on_204(self) -> None:
        client = _make_client()
        response = mock_response(status_code=204)

        with patch.object(client, '_delete', return_value=response):
            client.remove_tag('17', 'in-progress')

        response.raise_for_status.assert_not_called()

    def test_returns_on_404_label_already_absent(self) -> None:
        client = _make_client()
        response = mock_response(status_code=404)

        with patch.object(client, '_delete', return_value=response):
            client.remove_tag('17', 'missing-label')

        response.raise_for_status.assert_not_called()

    def test_raises_on_other_error_codes(self) -> None:
        client = _make_client()
        response = mock_response(status_code=500)

        with patch.object(client, '_delete', return_value=response):
            client.remove_tag('17', 'label')

        response.raise_for_status.assert_called_once_with()

    def test_encodes_special_characters_in_label(self) -> None:
        client = _make_client()
        response = mock_response(status_code=200)

        with patch.object(client, '_delete', return_value=response) as mock_delete:
            client.remove_tag('17', 'priority: high')

        url = mock_delete.call_args.args[0]
        self.assertIn('priority%3A%20high', url)


class GitHubIssuesClientMoveIssueToStateTests(unittest.TestCase):
    def test_adds_label_when_field_is_labels(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.move_issue_to_state('17', 'labels', 'In Review')

        mock_post.assert_called_once_with(
            '/repos/workspace/repo/issues/17/labels',
            json={GitHubIssueFields.LABELS: ['In Review']},
        )

    def test_adds_label_when_field_is_label_singular(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.move_issue_to_state('17', 'label', 'In Review')

        mock_post.assert_called_once_with(
            '/repos/workspace/repo/issues/17/labels',
            json={GitHubIssueFields.LABELS: ['In Review']},
        )

    def test_patches_state_field(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_patch', return_value=response) as mock_patch:
            client.move_issue_to_state('17', 'state', 'closed')

        mock_patch.assert_called_once_with(
            '/repos/workspace/repo/issues/17',
            json={'state': 'closed'},
        )

    def test_defaults_to_state_field_when_field_name_empty(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_patch', return_value=response) as mock_patch:
            client.move_issue_to_state('17', '', 'open')

        mock_patch.assert_called_once_with(
            '/repos/workspace/repo/issues/17',
            json={GitHubIssueFields.STATE: 'open'},
        )


class GitHubIssuesClientBuildRecordTests(unittest.TestCase):
    def test_generates_branch_name_from_id_when_not_provided(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='42',
            summary='My Issue',
            description='desc',
            comment_entries=[],
        )

        self.assertEqual(record.branch_name, 'feature/42')

    def test_uses_explicit_branch_name_when_provided(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='42',
            summary='My Issue',
            description='desc',
            comment_entries=[],
            branch_name='custom/branch',
        )

        self.assertEqual(record.branch_name, 'custom/branch')

    def test_sets_all_comments_attribute(self) -> None:
        client = _make_client()
        entries = [{'author': 'bob', 'body': 'nice'}]
        record = client._build_record(
            issue_id='1',
            summary='s',
            description='d',
            comment_entries=entries,
        )

        self.assertEqual(getattr(record, ISSUE_ALL_COMMENTS), entries)

    def test_normalizes_none_summary_and_description(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='5',
            summary=None,
            description=None,
            comment_entries=[],
        )

        self.assertEqual(record.summary, '')
        self.assertEqual(record.description, '')


class GitHubIssuesClientDescriptionTests(unittest.TestCase):
    def test_appends_non_operational_comments(self) -> None:
        client = _make_client()
        entries = [
            {ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'LGTM'},
        ]
        result = client._build_description_with_comments('Original description.', entries)

        self.assertIn('Original description.', result)
        self.assertIn('alice: LGTM', result)

    def test_no_comments_section_when_all_filtered(self) -> None:
        client = _make_client(is_operational_comment=lambda _: True)
        entries = [
            {ISSUE_COMMENT_AUTHOR: 'bot', ISSUE_COMMENT_BODY: 'operational text'},
        ]
        result = client._build_description_with_comments('Desc.', entries)

        self.assertNotIn('Issue comments for context', result)

    def test_provides_fallback_when_description_empty(self) -> None:
        client = _make_client()
        result = client._build_description_with_comments('', [])

        self.assertIn('No description provided.', result)

    def test_skips_non_dict_comment_entries(self) -> None:
        client = _make_client()
        result = client._build_description_with_comments('Desc', ['not-a-dict', None])

        self.assertNotIn('Issue comments for context', result)

    def test_unknown_author_when_author_missing(self) -> None:
        client = _make_client()
        entries = [{ISSUE_COMMENT_BODY: 'hello'}]
        result = client._build_description_with_comments('Desc', entries)

        self.assertIn('unknown: hello', result)


class GitHubIssuesClientIssueCommentsTests(unittest.TestCase):
    def test_returns_comment_list(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data=[
                {
                    GitHubCommentFields.BODY: 'Great work',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'alice'},
                }
            ]
        )

        with patch.object(client, '_get', return_value=response):
            result = client._issue_comments('17')

        self.assertEqual(len(result), 1)

    def test_returns_empty_on_failure(self) -> None:
        client = _make_client()

        with patch.object(client, '_get', side_effect=RuntimeError('network error')):
            result = client._issue_comments('17')

        self.assertEqual(result, [])


class GitHubIssuesClientCommentEntriesTests(unittest.TestCase):
    def test_extracts_body_and_author(self) -> None:
        client = _make_client()
        comments = [
            {
                GitHubCommentFields.BODY: 'Looks good',
                GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'reviewer'},
            }
        ]
        entries = client._task_comment_entries(comments)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][ISSUE_COMMENT_AUTHOR], 'reviewer')
        self.assertEqual(entries[0][ISSUE_COMMENT_BODY], 'Looks good')

    def test_uses_unknown_when_user_missing(self) -> None:
        client = _make_client()
        comments = [{GitHubCommentFields.BODY: 'A comment'}]
        entries = client._task_comment_entries(comments)

        self.assertEqual(entries[0][ISSUE_COMMENT_AUTHOR], 'unknown')

    def test_skips_blank_body_comments(self) -> None:
        client = _make_client()
        comments = [
            {GitHubCommentFields.BODY: '  ', GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'bob'}},
            {GitHubCommentFields.BODY: 'Real comment', GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'bob'}},
        ]
        entries = client._task_comment_entries(comments)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][ISSUE_COMMENT_BODY], 'Real comment')

    def test_skips_non_dict_items(self) -> None:
        client = _make_client()
        entries = client._task_comment_entries(['not-a-dict', None])

        self.assertEqual(entries, [])

    def test_default_bot_login_disables_filter(self) -> None:
        # Backward-compat: hosts that don't pass ``bot_login`` keep
        # the pre-filter behavior.
        client = _make_client()
        self.assertEqual(client._bot_login, '')
        comments = [
            {GitHubCommentFields.BODY: '@alice please look',
             GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'op'}},
        ]
        self.assertEqual(len(client._task_comment_entries(comments)), 1)

    def test_mention_filter_drops_addressed_to_other_humans(self) -> None:
        # The reported bug, GitHub edition.
        client = _make_client(bot_login='kato_bot')
        comments = [
            {GitHubCommentFields.BODY: '@alice can you handle this',
             GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'op'}},
            {GitHubCommentFields.BODY: 'this also needs a unit test',
             GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'op'}},
            {GitHubCommentFields.BODY: '@kato_bot fix the typo',
             GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'op'}},
        ]
        bodies = [e[ISSUE_COMMENT_BODY] for e in client._task_comment_entries(comments)]
        self.assertIn('this also needs a unit test', bodies)
        self.assertIn('@kato_bot fix the typo', bodies)
        self.assertNotIn('@alice can you handle this', bodies)


class GitHubIssuesClientStaticHelpersTests(unittest.TestCase):
    def test_normalized_allowed_states_lowercases(self) -> None:
        result = GitHubIssuesClient._normalized_allowed_states(['Open', 'In PROGRESS'])

        self.assertIn('open', result)
        self.assertIn('in progress', result)

    def test_normalized_allowed_states_excludes_blank(self) -> None:
        result = GitHubIssuesClient._normalized_allowed_states(['', '  ', 'open'])

        self.assertEqual(result, {'open'})

    def test_matches_allowed_state_returns_true_for_empty_set(self) -> None:
        self.assertTrue(GitHubIssuesClient._matches_allowed_state('anything', set()))

    def test_matches_allowed_state_returns_true_when_in_set(self) -> None:
        self.assertTrue(GitHubIssuesClient._matches_allowed_state('open', {'open', 'closed'}))

    def test_matches_allowed_state_returns_false_when_not_in_set(self) -> None:
        self.assertFalse(GitHubIssuesClient._matches_allowed_state('draft', {'open', 'closed'}))

    def test_task_tags_extracts_name_from_dict_labels(self) -> None:
        result = GitHubIssuesClient._task_tags([{'name': 'bug'}, {'name': 'enhancement'}])

        self.assertEqual(result, ['bug', 'enhancement'])

    def test_task_tags_falls_back_to_label_then_text(self) -> None:
        result = GitHubIssuesClient._task_tags([
            {'label': 'a'},
            {'text': 'b'},
            {'name': 'c'},
        ])

        self.assertEqual(result, ['a', 'b', 'c'])

    def test_task_tags_handles_plain_strings(self) -> None:
        result = GitHubIssuesClient._task_tags(['bug', 'wontfix'])

        self.assertEqual(result, ['bug', 'wontfix'])

    def test_task_tags_returns_empty_for_non_list(self) -> None:
        self.assertEqual(GitHubIssuesClient._task_tags(None), [])
        self.assertEqual(GitHubIssuesClient._task_tags('string'), [])
        self.assertEqual(GitHubIssuesClient._task_tags({}), [])

    def test_task_tags_skips_blank_values(self) -> None:
        result = GitHubIssuesClient._task_tags([{'name': ''}, {'name': 'valid'}])

        self.assertEqual(result, ['valid'])

    def test_json_items_returns_list_from_response(self) -> None:
        response = mock_response(json_data=[{'a': 1}, {'b': 2}])

        result = GitHubIssuesClient._json_items(response)

        self.assertEqual(result, [{'a': 1}, {'b': 2}])

    def test_json_items_returns_empty_for_non_list(self) -> None:
        response = mock_response(json_data={'key': 'value'})

        result = GitHubIssuesClient._json_items(response)

        self.assertEqual(result, [])

    def test_json_items_uses_items_key(self) -> None:
        response = mock_response(json_data={'values': [{'id': 1}]})

        result = GitHubIssuesClient._json_items(response, items_key='values')

        self.assertEqual(result, [{'id': 1}])

    def test_json_items_returns_empty_when_items_key_missing(self) -> None:
        response = mock_response(json_data={'other': []})

        result = GitHubIssuesClient._json_items(response, items_key='values')

        self.assertEqual(result, [])

    def test_json_items_returns_empty_when_payload_not_dict_and_items_key_set(self) -> None:
        response = mock_response(json_data=['item1', 'item2'])

        result = GitHubIssuesClient._json_items(response, items_key='values')

        self.assertEqual(result, [])

    def test_safe_dict_returns_dict_value(self) -> None:
        result = GitHubIssuesClient._safe_dict({'user': {'login': 'alice'}}, 'user')

        self.assertEqual(result, {'login': 'alice'})

    def test_safe_dict_returns_empty_for_non_dict_value(self) -> None:
        result = GitHubIssuesClient._safe_dict({'user': 'alice'}, 'user')

        self.assertEqual(result, {})

    def test_safe_dict_returns_empty_for_missing_key(self) -> None:
        result = GitHubIssuesClient._safe_dict({}, 'user')

        self.assertEqual(result, {})


class GitHubIssuesClientBestEffortTests(unittest.TestCase):
    def test_returns_items_on_success(self) -> None:
        client = _make_client()
        response = mock_response(json_data=[{'id': 1}])

        with patch.object(client, '_get', return_value=response):
            result = client._best_effort_response_items(
                '17',
                item_label='comments',
                path='/repos/workspace/repo/issues/17/comments',
            )

        self.assertEqual(result, [{'id': 1}])

    def test_returns_empty_list_on_exception(self) -> None:
        client = _make_client()

        with patch.object(client, '_get', side_effect=Exception('boom')):
            result = client._best_effort_response_items(
                '17',
                item_label='comments',
                path='/repos/workspace/repo/issues/17/comments',
            )

        self.assertEqual(result, [])


class GitHubIssuesClientFlowTests(unittest.TestCase):
    """A-Z flow: create client, fetch issues, verify all data flows through."""

    def test_full_flow_fetch_issues_with_labels_and_comments(self) -> None:
        client = GitHubIssuesClient(
            'https://api.github.com',
            'gh-token',
            'acme',
            'backend',
            is_operational_comment=lambda body: body.startswith('[bot]'),
        )

        issues_payload = [
            {
                GitHubIssueFields.NUMBER: 101,
                GitHubIssueFields.TITLE: 'Implement caching',
                GitHubIssueFields.BODY: 'Cache the DB calls.',
                GitHubIssueFields.STATE: 'open',
                GitHubIssueFields.LABELS: [
                    {GitHubIssueFields.NAME: 'performance'},
                    {GitHubIssueFields.NAME: 'backend'},
                ],
            },
            {
                GitHubIssueFields.NUMBER: 102,
                GitHubIssueFields.TITLE: 'PR — should be excluded',
                GitHubIssueFields.BODY: '',
                GitHubIssueFields.STATE: 'open',
                GitHubIssueFields.LABELS: [],
                GitHubIssueFields.PULL_REQUEST: {'url': 'https://api.github.com/pulls/102'},
            },
            {
                GitHubIssueFields.NUMBER: 103,
                GitHubIssueFields.TITLE: 'Closed issue — filtered',
                GitHubIssueFields.BODY: '',
                GitHubIssueFields.STATE: 'closed',
                GitHubIssueFields.LABELS: [],
            },
        ]
        comments_payload = [
            {
                GitHubCommentFields.BODY: '[bot] automated scan complete',
                GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'ci-bot'},
            },
            {
                GitHubCommentFields.BODY: 'Looks great, ship it!',
                GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'alice'},
            },
        ]

        issues_response = mock_response(json_data=issues_payload)
        comments_response = mock_response(json_data=comments_payload)

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            records = client.get_assigned_tasks('backend', 'alice', ['open'])

        # Only issue 101 passes: not a PR, state is open
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.id, '101')
        self.assertEqual(record.summary, 'Implement caching')
        self.assertIn('Cache the DB calls.', record.description)
        # Operational comment excluded from description
        self.assertNotIn('[bot] automated scan complete', record.description)
        # Human comment present
        self.assertIn('alice: Looks great, ship it!', record.description)
        self.assertEqual(record.tags, ['performance', 'backend'])
        self.assertEqual(record.branch_name, 'feature/101')
        all_comments = getattr(record, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)

    def test_full_flow_add_comment_then_move_to_review(self) -> None:
        client = GitHubIssuesClient(
            'https://api.github.com',
            'gh-token',
            'acme',
            'backend',
        )

        comment_response = mock_response()
        label_response = mock_response()

        with patch.object(client, '_post', side_effect=[comment_response, label_response]) as mock_post:
            client.add_comment('101', 'Starting implementation.')
            client.move_issue_to_state('101', 'labels', 'In Progress')

        self.assertEqual(mock_post.call_count, 2)
        comment_call, label_call = mock_post.call_args_list
        self.assertEqual(comment_call.args[0], '/repos/acme/backend/issues/101/comments')
        self.assertEqual(label_call.args[0], '/repos/acme/backend/issues/101/labels')

    def test_full_flow_add_and_remove_tag(self) -> None:
        client = GitHubIssuesClient(
            'https://api.github.com',
            'gh-token',
            'acme',
            'backend',
        )

        add_response = mock_response()
        remove_response = mock_response(status_code=200)

        with patch.object(client, '_post', return_value=add_response) as mock_post:
            client.add_tag('101', 'in-progress')

        with patch.object(client, '_delete', return_value=remove_response) as mock_delete:
            client.remove_tag('101', 'in-progress')

        mock_post.assert_called_once()
        mock_delete.assert_called_once()
        self.assertIn('in-progress', mock_delete.call_args.args[0])
