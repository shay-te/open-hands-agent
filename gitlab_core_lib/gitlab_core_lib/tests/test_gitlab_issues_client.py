import unittest
from unittest.mock import patch

from gitlab_core_lib.gitlab_core_lib.client.gitlab_issues_client import GitLabIssuesClient
from gitlab_core_lib.gitlab_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    GitLabCommentFields,
    GitLabIssueFields,
)
from gitlab_core_lib.gitlab_core_lib.data.issue_record import IssueRecord
from tests.utils import mock_response


def _make_client(**kwargs) -> GitLabIssuesClient:
    return GitLabIssuesClient(
        'https://gitlab.example/api/v4',
        'gl-token',
        'group/repo',
        **kwargs,
    )


def _issue(
    iid: int = 17,
    title: str = 'fix it already',
    description: str = 'Details',
    state: str = 'opened',
    labels=None,
) -> dict:
    return {
        GitLabIssueFields.IID: iid,
        GitLabIssueFields.TITLE: title,
        GitLabIssueFields.DESCRIPTION: description,
        GitLabIssueFields.STATE: state,
        GitLabIssueFields.LABELS: labels if labels is not None else [],
    }


def _note(body: str, author_name: str = 'Reviewer', system: bool = False) -> dict:
    note: dict = {
        GitLabCommentFields.BODY: body,
        GitLabCommentFields.AUTHOR: {GitLabCommentFields.NAME: author_name},
    }
    if system:
        note[GitLabCommentFields.SYSTEM] = True
    return note


class GitLabIssuesClientInitTests(unittest.TestCase):
    def test_url_encodes_project(self) -> None:
        client = _make_client()
        self.assertEqual(client._project, 'group%2Frepo')

    def test_strips_whitespace_from_project(self) -> None:
        client = GitLabIssuesClient(
            'https://gitlab.example/api/v4', 'tok', '  my group/my repo  '
        )
        self.assertIn('%2F', client._project)

    def test_sets_private_token_header(self) -> None:
        client = _make_client()
        self.assertEqual(client.headers['PRIVATE-TOKEN'], 'gl-token')

    def test_sets_timeout_to_30(self) -> None:
        client = _make_client()
        self.assertEqual(client.timeout, 30)

    def test_default_is_operational_comment_always_false(self) -> None:
        client = _make_client()
        self.assertFalse(client._is_operational_comment('anything'))

    def test_custom_is_operational_comment_used(self) -> None:
        client = _make_client(is_operational_comment=lambda body: body.startswith('[bot]'))
        self.assertTrue(client._is_operational_comment('[bot] scanning'))
        self.assertFalse(client._is_operational_comment('human comment'))


class GitLabIssuesClientValidateConnectionTests(unittest.TestCase):
    def test_checks_project_issues_endpoint(self) -> None:
        client = _make_client()
        response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('group/repo', 'developer', ['opened'])

        self.assertEqual(client.headers['PRIVATE-TOKEN'], 'gl-token')
        mock_get.assert_called_once_with(
            '/projects/group%2Frepo/issues',
            params={'assignee_username': 'developer', 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status.assert_called_once_with()


class GitLabIssuesClientGetAssignedTasksTests(unittest.TestCase):
    def test_returns_issue_records(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[_issue()])
        notes_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], IssueRecord)
        self.assertEqual(records[0].id, '17')
        self.assertEqual(records[0].summary, 'fix it already')

    def test_filters_by_allowed_states(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[
                _issue(iid=1, state='opened'),
                _issue(iid=2, state='closed'),
            ]
        )
        notes_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, '1')

    def test_returns_all_states_when_states_empty(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[
                _issue(iid=1, state='opened'),
                _issue(iid=2, state='closed'),
            ]
        )
        notes_r1 = mock_response(json_data=[])
        notes_r2 = mock_response(json_data=[])

        with patch.object(
            client, '_get', side_effect=[issues_response, notes_r1, notes_r2]
        ):
            records = client.get_assigned_tasks('group/repo', 'developer', [])

        self.assertEqual(len(records), 2)

    def test_loads_notes_and_appends_to_description(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[_issue()])
        notes_response = mock_response(
            json_data=[_note('Please add tests.', 'Reviewer')]
        )

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertIn('Reviewer: Please add tests.', records[0].description)

    def test_skips_system_notes_entirely(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[_issue()])
        notes_response = mock_response(
            json_data=[
                _note('assigned to @alice', system=True),
                _note('Real comment', 'alice'),
            ]
        )

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        all_comments = getattr(records[0], ISSUE_ALL_COMMENTS)
        # System note should not appear even in ALL_COMMENTS
        bodies = [c[ISSUE_COMMENT_BODY] for c in all_comments]
        self.assertNotIn('assigned to @alice', bodies)
        self.assertIn('Real comment', bodies)

    def test_uses_labels_as_tags(self) -> None:
        client = _make_client()
        issues_response = mock_response(
            json_data=[_issue(labels=['repo:client', 'priority:high'])]
        )
        notes_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(records[0].tags, ['repo:client', 'priority:high'])

    def test_operational_comments_excluded_from_description_but_in_all_comments(self) -> None:
        client = _make_client(
            is_operational_comment=lambda body: 'agent could not safely process' in body
        )
        issues_response = mock_response(json_data=[_issue()])
        notes_response = mock_response(
            json_data=[
                _note('agent could not safely process this task: timeout', 'shay'),
                _note('Please add tests.', 'Reviewer'),
            ]
        )

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertIn('Reviewer: Please add tests.', records[0].description)
        self.assertNotIn('could not safely process', records[0].description)
        all_comments = getattr(records[0], ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'shay')

    def test_sends_correct_params_to_issues_endpoint(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=issues_response) as mock_get:
            client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        mock_get.assert_called_once_with(
            '/projects/group%2Frepo/issues',
            params={
                'assignee_username': 'developer',
                'state': 'all',
                'order_by': 'updated_at',
                'sort': 'desc',
                'per_page': 100,
            },
        )

    def test_returns_empty_list_when_no_issues(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=issues_response):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(records, [])

    def test_handles_non_dict_items_gracefully(self) -> None:
        client = _make_client()
        issues_response = mock_response(json_data=['not-a-dict', None, _issue()])
        notes_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(len(records), 1)

    def test_skips_malformed_issues_that_raise(self) -> None:
        malformed = {GitLabIssueFields.STATE: 'opened'}
        good = _issue(iid=42)
        issues_response = mock_response(json_data=[malformed, good])
        notes_response = mock_response(json_data=[])
        client = _make_client()

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, '42')


class GitLabIssuesClientAddCommentTests(unittest.TestCase):
    def test_posts_note_body(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('17', 'Starting work.')

        mock_post.assert_called_once_with(
            '/projects/group%2Frepo/issues/17/notes',
            json={GitLabCommentFields.BODY: 'Starting work.'},
        )
        response.raise_for_status.assert_called_once_with()


class GitLabIssuesClientAddTagTests(unittest.TestCase):
    def test_sends_add_labels_payload(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.add_tag('17', 'in-progress')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'add_labels': 'in-progress'},
        )
        response.raise_for_status.assert_called_once_with()


class GitLabIssuesClientRemoveTagTests(unittest.TestCase):
    def test_sends_remove_labels_payload(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.remove_tag('17', 'in-progress')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'remove_labels': 'in-progress'},
        )
        response.raise_for_status.assert_called_once_with()


class GitLabIssuesClientMoveIssueToStateTests(unittest.TestCase):
    def test_adds_label_when_field_is_labels(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('17', 'labels', 'In Review')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'add_labels': 'In Review'},
        )

    def test_adds_label_when_field_is_label_singular(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('17', 'label', 'In Progress')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'add_labels': 'In Progress'},
        )

    def test_sends_reopen_state_event_for_open_states(self) -> None:
        client = _make_client()
        response = mock_response()

        for state_name in ('open', 'opened', 'reopen'):
            with patch.object(client, '_put', return_value=response) as mock_put:
                client.move_issue_to_state('17', 'state', state_name)

            mock_put.assert_called_once_with(
                '/projects/group%2Frepo/issues/17',
                json={'state_event': 'reopen'},
            )

    def test_sends_close_state_event_for_other_states(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('17', 'state', 'closed')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'state_event': 'close'},
        )

    def test_defaults_to_close_for_unknown_state(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('17', 'state', 'some-custom-state')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'state_event': 'close'},
        )


class GitLabIssuesClientBuildRecordTests(unittest.TestCase):
    def test_generates_branch_name_from_id(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='42',
            summary='My Issue',
            description='desc',
            comment_entries=[],
        )

        self.assertEqual(record.branch_name, 'feature/42')

    def test_uses_explicit_branch_name(self) -> None:
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

    def test_normalizes_none_fields(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='5',
            summary=None,
            description=None,
            comment_entries=[],
        )

        self.assertEqual(record.summary, '')
        self.assertEqual(record.description, '')


class GitLabIssuesClientDescriptionTests(unittest.TestCase):
    def test_appends_non_operational_comments(self) -> None:
        client = _make_client()
        entries = [{ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'LGTM'}]
        result = client._build_description_with_comments('Original.', entries)

        self.assertIn('Original.', result)
        self.assertIn('alice: LGTM', result)

    def test_no_comment_section_when_all_filtered(self) -> None:
        client = _make_client(is_operational_comment=lambda _: True)
        entries = [{ISSUE_COMMENT_AUTHOR: 'bot', ISSUE_COMMENT_BODY: 'operational'}]
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


class GitLabIssuesClientIssueNotesTests(unittest.TestCase):
    def test_returns_note_list(self) -> None:
        client = _make_client()
        response = mock_response(json_data=[_note('Great work')])

        with patch.object(client, '_get', return_value=response):
            result = client._issue_comments('17')

        self.assertEqual(len(result), 1)

    def test_returns_empty_on_failure(self) -> None:
        client = _make_client()

        with patch.object(client, '_get', side_effect=RuntimeError('network error')):
            result = client._issue_comments('17')

        self.assertEqual(result, [])


class GitLabIssuesClientCommentEntriesTests(unittest.TestCase):
    def test_extracts_body_and_author_name(self) -> None:
        client = _make_client()
        comments = [_note('Looks good', 'Reviewer')]
        entries = client._task_comment_entries(comments)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][ISSUE_COMMENT_AUTHOR], 'Reviewer')
        self.assertEqual(entries[0][ISSUE_COMMENT_BODY], 'Looks good')

    def test_falls_back_to_username(self) -> None:
        client = _make_client()
        comments = [
            {
                GitLabCommentFields.BODY: 'Comment',
                GitLabCommentFields.AUTHOR: {GitLabCommentFields.USERNAME: 'alice_user'},
            }
        ]
        entries = client._task_comment_entries(comments)

        self.assertEqual(entries[0][ISSUE_COMMENT_AUTHOR], 'alice_user')

    def test_skips_system_notes(self) -> None:
        client = _make_client()
        comments = [
            _note('assigned to @alice', system=True),
            _note('Real comment', 'alice'),
        ]
        entries = client._task_comment_entries(comments)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][ISSUE_COMMENT_BODY], 'Real comment')

    def test_skips_blank_body_notes(self) -> None:
        client = _make_client()
        comments = [
            _note('  ', 'bob'),
            _note('Non-blank', 'bob'),
        ]
        entries = client._task_comment_entries(comments)

        self.assertEqual(len(entries), 1)

    def test_uses_unknown_when_author_missing(self) -> None:
        client = _make_client()
        comments = [{GitLabCommentFields.BODY: 'A comment'}]
        entries = client._task_comment_entries(comments)

        self.assertEqual(entries[0][ISSUE_COMMENT_AUTHOR], 'unknown')

    def test_skips_non_dict_items(self) -> None:
        client = _make_client()
        entries = client._task_comment_entries(['not-a-dict', None])

        self.assertEqual(entries, [])


class GitLabIssuesClientStaticHelpersTests(unittest.TestCase):
    def test_normalized_allowed_states_lowercases(self) -> None:
        result = GitLabIssuesClient._normalized_allowed_states(['Opened', 'CLOSED'])

        self.assertIn('opened', result)
        self.assertIn('closed', result)

    def test_normalized_allowed_states_excludes_blank(self) -> None:
        result = GitLabIssuesClient._normalized_allowed_states(['', '  ', 'opened'])

        self.assertEqual(result, {'opened'})

    def test_matches_allowed_state_returns_true_for_empty_set(self) -> None:
        self.assertTrue(GitLabIssuesClient._matches_allowed_state('anything', set()))

    def test_matches_allowed_state_returns_true_when_in_set(self) -> None:
        self.assertTrue(GitLabIssuesClient._matches_allowed_state('opened', {'opened', 'closed'}))

    def test_matches_allowed_state_returns_false_when_not_in_set(self) -> None:
        self.assertFalse(GitLabIssuesClient._matches_allowed_state('locked', {'opened', 'closed'}))

    def test_task_tags_extracts_plain_strings(self) -> None:
        result = GitLabIssuesClient._task_tags(['bug', 'enhancement'])

        self.assertEqual(result, ['bug', 'enhancement'])

    def test_task_tags_extracts_from_dicts(self) -> None:
        result = GitLabIssuesClient._task_tags([{'name': 'backend'}, {'label': 'perf'}])

        self.assertEqual(result, ['backend', 'perf'])

    def test_task_tags_returns_empty_for_non_list(self) -> None:
        self.assertEqual(GitLabIssuesClient._task_tags(None), [])
        self.assertEqual(GitLabIssuesClient._task_tags('string'), [])

    def test_task_tags_skips_blank_values(self) -> None:
        result = GitLabIssuesClient._task_tags([{'name': ''}, 'valid'])

        self.assertEqual(result, ['valid'])

    def test_json_items_returns_list_from_response(self) -> None:
        response = mock_response(json_data=[{'a': 1}])

        result = GitLabIssuesClient._json_items(response)

        self.assertEqual(result, [{'a': 1}])

    def test_json_items_returns_empty_for_non_list(self) -> None:
        response = mock_response(json_data={'key': 'value'})

        result = GitLabIssuesClient._json_items(response)

        self.assertEqual(result, [])

    def test_json_items_uses_items_key(self) -> None:
        response = mock_response(json_data={'items': [{'id': 1}]})

        result = GitLabIssuesClient._json_items(response, items_key='items')

        self.assertEqual(result, [{'id': 1}])

    def test_safe_dict_returns_dict_value(self) -> None:
        result = GitLabIssuesClient._safe_dict(
            {'author': {'name': 'alice'}}, 'author'
        )

        self.assertEqual(result, {'name': 'alice'})

    def test_safe_dict_returns_empty_for_non_dict(self) -> None:
        result = GitLabIssuesClient._safe_dict({'author': 'alice'}, 'author')

        self.assertEqual(result, {})

    def test_safe_dict_returns_empty_for_missing_key(self) -> None:
        result = GitLabIssuesClient._safe_dict({}, 'author')

        self.assertEqual(result, {})


class GitLabIssuesClientBestEffortTests(unittest.TestCase):
    def test_returns_items_on_success(self) -> None:
        client = _make_client()
        response = mock_response(json_data=[{'id': 1}])

        with patch.object(client, '_get', return_value=response):
            result = client._best_effort_response_items(
                '17',
                item_label='comments',
                path='/projects/group%2Frepo/issues/17/notes',
            )

        self.assertEqual(result, [{'id': 1}])

    def test_returns_empty_list_on_exception(self) -> None:
        client = _make_client()

        with patch.object(client, '_get', side_effect=Exception('boom')):
            result = client._best_effort_response_items(
                '17',
                item_label='comments',
                path='/projects/group%2Frepo/issues/17/notes',
            )

        self.assertEqual(result, [])


class GitLabIssuesClientFlowTests(unittest.TestCase):
    """A-Z flow: create client, fetch issues, add comment, move state, add/remove tag."""

    def test_full_flow_fetch_issues_with_labels_and_comments(self) -> None:
        client = GitLabIssuesClient(
            'https://gitlab.example/api/v4',
            'gl-token',
            'acme/backend',
            is_operational_comment=lambda body: body.startswith('[bot]'),
        )

        issues_payload = [
            {
                GitLabIssueFields.IID: 101,
                GitLabIssueFields.TITLE: 'Implement caching',
                GitLabIssueFields.DESCRIPTION: 'Cache the DB calls.',
                GitLabIssueFields.STATE: 'opened',
                GitLabIssueFields.LABELS: ['performance', 'backend'],
            },
            {
                GitLabIssueFields.IID: 102,
                GitLabIssueFields.TITLE: 'Closed issue',
                GitLabIssueFields.DESCRIPTION: '',
                GitLabIssueFields.STATE: 'closed',
                GitLabIssueFields.LABELS: [],
            },
        ]
        notes_payload = [
            _note('[bot] automated scan complete', 'ci-bot'),
            _note('Looks great, ship it!', 'alice'),
            _note('assigned to @alice', system=True),
        ]

        issues_response = mock_response(json_data=issues_payload)
        notes_response = mock_response(json_data=notes_payload)

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            records = client.get_assigned_tasks('acme/backend', 'alice', ['opened'])

        # Only issue 101 passes: state is opened
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.id, '101')
        self.assertEqual(record.summary, 'Implement caching')
        self.assertIn('Cache the DB calls.', record.description)
        # Operational comment excluded from description
        self.assertNotIn('[bot] automated scan complete', record.description)
        # Human comment present
        self.assertIn('alice: Looks great, ship it!', record.description)
        # System note absent from ALL_COMMENTS
        all_comments = getattr(record, ISSUE_ALL_COMMENTS)
        bodies = [c[ISSUE_COMMENT_BODY] for c in all_comments]
        self.assertNotIn('assigned to @alice', bodies)
        self.assertEqual(record.tags, ['performance', 'backend'])
        self.assertEqual(record.branch_name, 'feature/101')

    def test_full_flow_add_comment_then_move_to_review(self) -> None:
        client = GitLabIssuesClient(
            'https://gitlab.example/api/v4',
            'gl-token',
            'acme/backend',
        )

        post_response = mock_response()
        put_response = mock_response()

        with patch.object(client, '_post', return_value=post_response) as mock_post:
            client.add_comment('101', 'Starting implementation.')

        with patch.object(client, '_put', return_value=put_response) as mock_put:
            client.move_issue_to_state('101', 'labels', 'In Progress')

        mock_post.assert_called_once()
        mock_put.assert_called_once()
        self.assertIn('notes', mock_post.call_args.args[0])
        self.assertIn('add_labels', mock_put.call_args.kwargs['json'])

    def test_full_flow_add_and_remove_tag(self) -> None:
        client = GitLabIssuesClient(
            'https://gitlab.example/api/v4',
            'gl-token',
            'acme/backend',
        )

        add_response = mock_response()
        remove_response = mock_response()

        with patch.object(client, '_put', side_effect=[add_response, remove_response]) as mock_put:
            client.add_tag('101', 'in-progress')
            client.remove_tag('101', 'in-progress')

        self.assertEqual(mock_put.call_count, 2)
        add_call, remove_call = mock_put.call_args_list
        self.assertEqual(add_call.kwargs['json'], {'add_labels': 'in-progress'})
        self.assertEqual(remove_call.kwargs['json'], {'remove_labels': 'in-progress'})

    def test_full_flow_validate_then_get_tasks(self) -> None:
        client = GitLabIssuesClient(
            'https://gitlab.example/api/v4',
            'gl-token',
            'acme/backend',
        )

        validate_response = mock_response(json_data=[])
        issues_response = mock_response(json_data=[_issue(iid=55, title='Task A')])
        notes_response = mock_response(json_data=[])

        with patch.object(
            client, '_get', side_effect=[validate_response, issues_response, notes_response]
        ):
            client.validate_connection('acme/backend', 'alice', ['opened'])
            records = client.get_assigned_tasks('acme/backend', 'alice', ['opened'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, '55')
        self.assertEqual(records[0].summary, 'Task A')
