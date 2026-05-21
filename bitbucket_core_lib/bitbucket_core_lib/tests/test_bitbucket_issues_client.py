import unittest
from unittest.mock import patch

from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_issues_client import (
    BitbucketIssuesClient,
)
from bitbucket_core_lib.bitbucket_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    BitbucketIssueFields,
)
from bitbucket_core_lib.bitbucket_core_lib.data.issue_record import IssueRecord
from tests.utils import assert_client_basic_auth_and_timeout, mock_response


class BitbucketIssuesClientInitTests(unittest.TestCase):
    def test_stores_workspace_and_repo_slug(self) -> None:
        client = BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0', 'tok', ' MyWorkspace ', ' my-repo '
        )
        self.assertEqual(client._workspace, 'MyWorkspace')
        self.assertEqual(client._repo_slug, 'my-repo')

    def test_uses_basic_auth_when_username_is_configured(self) -> None:
        client = BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0',
            'bb-token',
            'workspace',
            'repo',
            username='bb-user',
        )
        assert_client_basic_auth_and_timeout(self, client, 'bb-user', 'bb-token', 30)

    def test_default_is_operational_comment_never_filters(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'tok', 'ws', 'repo')
        # Default: nothing is an operational comment
        self.assertFalse(client._is_operational_comment('Kato agent started working'))
        self.assertFalse(client._is_operational_comment('any text'))

    def test_custom_is_operational_comment_is_stored(self) -> None:
        checker = lambda text: text.startswith('Bot:')
        client = BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0', 'tok', 'ws', 'repo',
            is_operational_comment=checker,
        )
        self.assertTrue(client._is_operational_comment('Bot: running'))
        self.assertFalse(client._is_operational_comment('Human: nice work'))


class BitbucketIssuesClientValidateTests(unittest.TestCase):
    def test_validate_connection_checks_repository_issues(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        response = mock_response(json_data={'values': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('repo', 'reviewer', ['new'])

        mock_get.assert_called_once_with(
            '/repositories/workspace/repo/issues',
            params={'pagelen': 1},
        )
        response.raise_for_status.assert_called_once()


class BitbucketIssuesClientGetTasksTests(unittest.TestCase):
    def _make_client(self, **kwargs) -> BitbucketIssuesClient:
        return BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo', **kwargs
        )

    def test_returns_issue_record_instances(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], IssueRecord)

    def test_filters_by_assignee_nickname(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [
                {'id': 17, 'title': 'A', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'nickname': 'reviewer'}},
                {'id': 18, 'title': 'B', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'nickname': 'someone-else'}},
            ]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, '17')

    def test_filters_by_assignee_display_name(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [
                {'id': 5, 'title': 'T', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'display_name': 'Alice Smith'}},
            ]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'alice smith', ['new'])

        self.assertEqual(len(tasks), 1)

    def test_returns_all_issues_when_assignee_is_blank(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [
                {'id': 1, 'title': 'A', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'nickname': 'alice'}},
                {'id': 2, 'title': 'B', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'nickname': 'bob'}},
            ]
        })
        comments1 = mock_response(json_data={'values': []})
        comments2 = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments1, comments2]):
            tasks = client.get_assigned_tasks('repo', '', ['new'])

        self.assertEqual(len(tasks), 2)

    def test_filters_by_state(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [
                {'id': 1, 'title': 'Open', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'nickname': 'reviewer'}},
                {'id': 2, 'title': 'Closed', 'content': {'raw': ''}, 'state': 'resolved',
                 'assignee': {'nickname': 'reviewer'}},
            ]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, '1')

    def test_state_filter_is_case_insensitive(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [
                {'id': 1, 'title': 'T', 'content': {'raw': ''}, 'state': 'NEW',
                 'assignee': {'nickname': 'reviewer'}},
            ]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)

    def test_empty_state_list_allows_all_states(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [
                {'id': 1, 'title': 'A', 'content': {'raw': ''}, 'state': 'new',
                 'assignee': {'nickname': 'reviewer'}},
                {'id': 2, 'title': 'B', 'content': {'raw': ''}, 'state': 'resolved',
                 'assignee': {'nickname': 'reviewer'}},
            ]
        })
        c1 = mock_response(json_data={'values': []})
        c2 = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, c1, c2]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', [])

        self.assertEqual(len(tasks), 2)

    def test_loads_issue_labels_as_tags(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
                BitbucketIssueFields.LABELS: ['repo:client', 'priority:high'],
            }]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])

    def test_includes_comments_in_description(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [{
                'content': {'raw': 'Please add tests.'},
                'user': {'display_name': 'Reviewer'},
            }]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertIn('Reviewer: Please add tests.', tasks[0].description)

    def test_stores_all_comments_on_record(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [{
                'content': {'raw': 'Please add tests.'},
                'user': {'display_name': 'Reviewer'},
            }]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        all_comments = getattr(tasks[0], ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 1)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'Reviewer')
        self.assertEqual(all_comments[0][ISSUE_COMMENT_BODY], 'Please add tests.')

    def test_filters_operational_comments_from_description_when_configured(self) -> None:
        client = self._make_client(
            is_operational_comment=lambda text: text.startswith('Kato agent'),
        )
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': 'Kato agent could not safely process this task: timeout'},
                 'user': {'display_name': 'shay'}},
                {'content': {'raw': 'Please add tests.'},
                 'user': {'display_name': 'Reviewer'}},
            ]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertIn('Reviewer: Please add tests.', tasks[0].description)
        self.assertNotIn('could not safely process', tasks[0].description)

    def test_operational_comments_still_appear_in_all_comments(self) -> None:
        client = self._make_client(
            is_operational_comment=lambda text: text.startswith('Kato agent'),
        )
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': 'Kato agent started working'},
                 'user': {'display_name': 'bot'}},
                {'content': {'raw': 'Please add tests.'},
                 'user': {'display_name': 'Reviewer'}},
            ]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        all_comments = getattr(tasks[0], ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)

    def test_comments_fetch_failure_does_not_fail_the_task(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 17, 'title': 'fix it already', 'content': {'raw': 'Details'},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })

        def side_effect(path, **_kw):
            if 'comments' in path:
                raise RuntimeError('network error')
            return issues_resp

        with patch.object(client, '_get', side_effect=side_effect):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(getattr(tasks[0], ISSUE_ALL_COMMENTS), [])

    def test_non_dict_issue_items_are_skipped(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={'values': ['string', None, 42]})

        with patch.object(client, '_get', return_value=issues_resp):
            tasks = client.get_assigned_tasks('repo', '', [])

        self.assertEqual(tasks, [])

    def test_branch_name_derived_from_issue_id(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 99, 'title': 'Task', 'content': {'raw': ''},
                'state': 'new', 'assignee': {'nickname': 'reviewer'},
            }]
        })
        comments_resp = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(tasks[0].branch_name, 'feature/99')

    def test_no_assignee_on_issue_is_excluded_when_assignee_filter_set(self) -> None:
        client = self._make_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 1, 'title': 'T', 'content': {'raw': ''}, 'state': 'new',
                # no assignee key
            }]
        })

        with patch.object(client, '_get', return_value=issues_resp):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(tasks, [])


class BitbucketIssuesClientAddCommentTests(unittest.TestCase):
    def test_add_comment_posts_raw_content_payload(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('17', 'Ready for review')

        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/issues/17/comments',
            json={'content': {'raw': 'Ready for review'}},
        )
        response.raise_for_status.assert_called_once()

    def test_add_comment_uses_correct_workspace_and_repo(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'tok', 'myws', 'myrepo')
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('5', 'hello')

        self.assertIn('/repositories/myws/myrepo/issues/5/comments', mock_post.call_args.args[0])


class BitbucketIssuesClientMoveStateTests(unittest.TestCase):
    def test_move_issue_to_state_puts_correct_payload(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('17', 'status', 'resolved')

        mock_put.assert_called_once_with(
            '/repositories/workspace/repo/issues/17',
            json={'status': 'resolved'},
        )

    def test_move_issue_defaults_to_state_field_when_field_name_empty(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'tok', 'ws', 'repo')
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('1', '', 'closed')

        _, kwargs = mock_put.call_args
        self.assertIn(BitbucketIssueFields.STATE, kwargs['json'])


class BitbucketIssuesClientAddRemoveTagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo'
        )

    # ----- add_tag -----

    def test_add_tag_puts_component_name(self) -> None:
        response = mock_response()
        with patch.object(self.client, '_put', return_value=response) as mock_put:
            self.client.add_tag('42', 'kato:triage:investigate')

        mock_put.assert_called_once_with(
            '/repositories/workspace/repo/issues/42',
            json={'component': {'name': 'kato:triage:investigate'}},
        )

    def test_add_tag_strips_whitespace_from_label(self) -> None:
        response = mock_response()
        with patch.object(self.client, '_put', return_value=response) as mock_put:
            self.client.add_tag('1', '  my-tag  ')

        _, kwargs = mock_put.call_args
        self.assertEqual(kwargs['json']['component']['name'], 'my-tag')

    def test_add_tag_skips_empty_label(self) -> None:
        with patch.object(self.client, '_put') as mock_put:
            self.client.add_tag('1', '')
        mock_put.assert_not_called()

    def test_add_tag_skips_whitespace_only_label(self) -> None:
        with patch.object(self.client, '_put') as mock_put:
            self.client.add_tag('1', '   ')
        mock_put.assert_not_called()

    def test_add_tag_propagates_http_error(self) -> None:
        from tests.utils import mock_response as _mr
        bad_response = _mr(status_code=400)
        bad_response.raise_for_status.side_effect = Exception('bad request')
        with patch.object(self.client, '_put', return_value=bad_response):
            with self.assertRaises(Exception):
                self.client.add_tag('1', 'some-tag')

    # ----- remove_tag -----

    def test_remove_tag_clears_component_when_name_matches(self) -> None:
        get_response = mock_response(
            json_data={'component': {'name': 'kato:triage:investigate'}}
        )
        put_response = mock_response()
        with patch.object(self.client, '_get', return_value=get_response), \
             patch.object(self.client, '_put', return_value=put_response) as mock_put:
            self.client.remove_tag('42', 'kato:triage:investigate')

        mock_put.assert_called_once_with(
            '/repositories/workspace/repo/issues/42',
            json={'component': None},
        )

    def test_remove_tag_is_no_op_when_component_does_not_match(self) -> None:
        get_response = mock_response(json_data={'component': {'name': 'other-tag'}})
        with patch.object(self.client, '_get', return_value=get_response), \
             patch.object(self.client, '_put') as mock_put:
            self.client.remove_tag('42', 'kato:triage:investigate')

        mock_put.assert_not_called()

    def test_remove_tag_is_no_op_when_component_is_null(self) -> None:
        get_response = mock_response(json_data={'component': None})
        with patch.object(self.client, '_get', return_value=get_response), \
             patch.object(self.client, '_put') as mock_put:
            self.client.remove_tag('42', 'some-tag')

        mock_put.assert_not_called()

    def test_remove_tag_comparison_is_case_insensitive(self) -> None:
        get_response = mock_response(json_data={'component': {'name': 'MyTag'}})
        put_response = mock_response()
        with patch.object(self.client, '_get', return_value=get_response), \
             patch.object(self.client, '_put', return_value=put_response) as mock_put:
            self.client.remove_tag('42', 'mytag')

        mock_put.assert_called_once()

    def test_remove_tag_is_no_op_on_get_exception(self) -> None:
        with patch.object(self.client, '_get', side_effect=Exception('network error')), \
             patch.object(self.client, '_put') as mock_put:
            # Should not raise
            self.client.remove_tag('42', 'any-tag')

        mock_put.assert_not_called()


class BitbucketIssuesClientTagHelpersTests(unittest.TestCase):
    def test_task_tags_extracts_string_values(self) -> None:
        tags = BitbucketIssuesClient._task_tags(['repo:client', 'priority:high'])
        self.assertEqual(tags, ['repo:client', 'priority:high'])

    def test_task_tags_extracts_dict_name_values(self) -> None:
        tags = BitbucketIssuesClient._task_tags([{'name': 'backend'}, {'name': 'urgent'}])
        self.assertEqual(tags, ['backend', 'urgent'])

    def test_task_tags_extracts_label_and_text_fallbacks(self) -> None:
        tags = BitbucketIssuesClient._task_tags([{'label': 'a'}, {'text': 'b'}])
        self.assertEqual(tags, ['a', 'b'])

    def test_task_tags_skips_empty_and_non_list(self) -> None:
        self.assertEqual(BitbucketIssuesClient._task_tags(None), [])
        self.assertEqual(BitbucketIssuesClient._task_tags('string'), [])
        self.assertEqual(BitbucketIssuesClient._task_tags(['', '  ']), [])

    def test_matches_assignee_by_display_name(self) -> None:
        self.assertTrue(
            BitbucketIssuesClient._matches_assignee({'display_name': 'Alice'}, 'alice')
        )

    def test_matches_assignee_by_nickname(self) -> None:
        self.assertTrue(
            BitbucketIssuesClient._matches_assignee({'nickname': 'jdoe'}, 'jdoe')
        )

    def test_matches_assignee_returns_false_for_non_dict(self) -> None:
        self.assertFalse(BitbucketIssuesClient._matches_assignee('string', 'string'))
        self.assertFalse(BitbucketIssuesClient._matches_assignee(None, 'none'))

    def test_normalized_allowed_states_lowercases(self) -> None:
        states = BitbucketIssuesClient._normalized_allowed_states(['New', 'OPEN'])
        self.assertIn('new', states)
        self.assertIn('open', states)

    def test_matches_allowed_state_empty_set_allows_all(self) -> None:
        self.assertTrue(BitbucketIssuesClient._matches_allowed_state('anything', set()))

    def test_matches_allowed_state_rejects_unknown(self) -> None:
        self.assertFalse(BitbucketIssuesClient._matches_allowed_state('closed', {'new', 'open'}))


class BitbucketIssuesClientFlowTests(unittest.TestCase):
    """A-Z flow: create client → validate → fetch tasks → add comment → move state."""

    def test_full_lifecycle_flow(self) -> None:
        OPERATIONAL_PREFIXES = ('Bot completed', 'Bot started')

        client = BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0',
            'bb-token',
            'acme',
            'backend',
            max_retries=2,
            is_operational_comment=lambda t: any(t.startswith(p) for p in OPERATIONAL_PREFIXES),
        )

        # Step 1: validate_connection
        validate_resp = mock_response(json_data={'values': []})

        # Step 2: get_assigned_tasks — issues + comments fetches
        issues_resp = mock_response(json_data={
            'values': [
                {
                    'id': 42,
                    'title': 'Add login page',
                    'content': {'raw': 'Users need a login form'},
                    'state': 'new',
                    'assignee': {'nickname': 'dev-alice'},
                    BitbucketIssueFields.LABELS: ['frontend'],
                },
            ]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': 'Bot started task'}, 'user': {'display_name': 'bot'}},
                {'content': {'raw': 'Please use React'}, 'user': {'display_name': 'alice'}},
            ]
        })

        # Step 3: add_comment
        add_comment_resp = mock_response()

        # Step 4: move_issue_to_state
        move_resp = mock_response()

        get_calls = [validate_resp, issues_resp, comments_resp]
        post_calls = [add_comment_resp]
        put_calls = [move_resp]

        with patch.object(client, '_get', side_effect=get_calls), \
             patch.object(client, '_post', return_value=add_comment_resp), \
             patch.object(client, '_put', return_value=move_resp):

            client.validate_connection('backend', 'dev-alice', ['new'])

            tasks = client.get_assigned_tasks('backend', 'dev-alice', ['new'])

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertIsInstance(task, IssueRecord)
        self.assertEqual(task.id, '42')
        self.assertEqual(task.summary, 'Add login page')
        self.assertIn('alice: Please use React', task.description)
        self.assertNotIn('Bot started task', task.description)
        self.assertEqual(task.tags, ['frontend'])
        self.assertEqual(task.branch_name, 'feature/42')

        all_comments = getattr(task, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)

        with patch.object(client, '_post', return_value=add_comment_resp) as mock_post:
            client.add_comment('42', 'Working on it')
        mock_post.assert_called_once()

        with patch.object(client, '_put', return_value=move_resp) as mock_put:
            client.move_issue_to_state('42', 'state', 'in progress')
        mock_put.assert_called_once()


class BitbucketIssuesClientDefensiveBranchTests(unittest.TestCase):
    def _client(self):
        return BitbucketIssuesClient(
            'https://bitbucket.example', 'bb-token',
            workspace='workspace', repo_slug='repo',
        )

    def test_to_record_coerces_non_dict_content(self) -> None:
        # Line 144: ``content`` isn't a dict → fall back to {}.
        client = self._client()
        with patch.object(client, '_issue_comments', return_value=[]):
            record = client._to_record({
                'id': 42, 'title': 'Issue title',
                'content': 'oops not a dict',
            })
        self.assertEqual(record.id, '42')

    def test_comment_lines_skips_non_dict_entries(self) -> None:
        # Line 224: non-dict comment → skip.
        client = self._client()
        result = client._comment_lines([
            'not a dict',
            {ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'real comment'},
        ])
        self.assertEqual(len(result), 1)

    def test_normalize_issue_records_logs_on_to_record_failure(self) -> None:
        # Lines 249-250: ``to_record`` raises → log + continue.
        client = self._client()
        with patch.object(client, 'logger') as mock_logger:
            result = client._normalize_issue_records(
                [
                    {'key': 'bad'},
                    {'key': 'good'},
                ],
                to_record=lambda item: (
                    (_ for _ in ()).throw(KeyError('missing'))
                    if item['key'] == 'bad'
                    else IssueRecord(id=item['key'], summary='ok', description='')
                ),
            )
        self.assertEqual(len(result), 1)
        mock_logger.exception.assert_called_once()

    def test_json_items_returns_empty_when_items_key_set_but_payload_not_dict(self) -> None:
        # Line 298: items_key requested but payload isn't a dict.
        response = mock_response(json_data=['list'])
        result = BitbucketIssuesClient._json_items(response, items_key='values')
        self.assertEqual(result, [])

    def test_build_comment_entries_skips_non_dict_and_blank(self) -> None:
        # Lines 330, 333: non-dict and blank-body entries are skipped.
        result = BitbucketIssuesClient._build_comment_entries(
            [
                'not a dict',
                {'author': 'a', 'body': ''},
                {'author': 'b', 'body': 'real'},
            ],
            extract_body=lambda c: c.get('body', ''),
            extract_author=lambda c: c.get('author', ''),
        )
        self.assertEqual(len(result), 1)
