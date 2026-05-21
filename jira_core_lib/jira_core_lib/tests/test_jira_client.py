import unittest
from unittest.mock import patch

from jira_core_lib.jira_core_lib.client.jira_client import (
    JiraClient,
    _COMMENT_SECTION_TITLE,
    _TEXT_ATTACHMENTS_SECTION_TITLE,
    _SCREENSHOT_SECTION_TITLE,
)
from jira_core_lib.jira_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    JiraAttachmentFields,
    JiraCommentFields,
    JiraIssueFields,
    JiraTransitionFields,
)
from jira_core_lib.jira_core_lib.data.issue_record import IssueRecord
from tests.utils import assert_client_headers_and_timeout, mock_response


def _make_client(**kwargs) -> JiraClient:
    return JiraClient('https://jira.example', 'jira-token', **kwargs)


def _adf_paragraph(text: str) -> dict:
    return {
        'type': 'doc',
        'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}
        ],
    }


def _issue_payload(
    key: str = 'PROJ-1',
    summary: str = 'fix it already',
    description=None,
    comments: list | None = None,
    attachments: list | None = None,
    labels: list | None = None,
) -> dict:
    return {
        JiraIssueFields.KEY: key,
        'fields': {
            JiraIssueFields.SUMMARY: summary,
            JiraIssueFields.DESCRIPTION: description,
            JiraIssueFields.COMMENT: {'comments': comments or []},
            JiraIssueFields.ATTACHMENT: attachments or [],
            JiraIssueFields.LABELS: labels or [],
        },
    }


def _comment_payload(text: str, author: str = 'Reviewer') -> dict:
    return {
        JiraCommentFields.BODY: _adf_paragraph(text),
        JiraCommentFields.AUTHOR: {JiraCommentFields.DISPLAY_NAME: author},
    }


class JiraClientInitTests(unittest.TestCase):
    def test_uses_bearer_auth_by_default(self) -> None:
        client = _make_client(max_retries=5)

        self.assertEqual(client.max_retries, 5)
        assert_client_headers_and_timeout(self, client, 'jira-token', 30)

    def test_uses_basic_auth_when_email_is_configured(self) -> None:
        client = JiraClient('https://jira.example', 'jira-token', 'dev@example.com')

        self.assertIsNone(client.headers)
        self.assertEqual(client.auth, ('dev@example.com', 'jira-token'))

    def test_ignores_blank_email(self) -> None:
        client = JiraClient('https://jira.example', 'jira-token', '   ')

        assert_client_headers_and_timeout(self, client, 'jira-token', 30)

    def test_default_is_operational_comment_always_false(self) -> None:
        client = _make_client()
        self.assertFalse(client._is_operational_comment('anything'))

    def test_custom_is_operational_comment_injectable(self) -> None:
        client = _make_client(is_operational_comment=lambda body: body.startswith('[bot]'))
        self.assertTrue(client._is_operational_comment('[bot] scanning'))
        self.assertFalse(client._is_operational_comment('human'))


class JiraClientValidateConnectionTests(unittest.TestCase):
    def test_queries_search_api_with_correct_jql(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('PROJ', 'developer', ['To Do', 'Open'])

        mock_get.assert_called_once_with(
            '/rest/api/3/search',
            params={
                'jql': 'project = "PROJ" AND assignee = "developer" AND status IN ("To Do", "Open") ORDER BY updated DESC',
                'fields': 'key',
                'maxResults': 1,
            },
        )
        response.raise_for_status.assert_called_once_with()

    def test_raises_when_states_empty(self) -> None:
        client = _make_client()
        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            client.validate_connection('PROJ', 'developer', [])


class JiraClientGetAssignedTasksTests(unittest.TestCase):
    def test_returns_issue_records(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': [_issue_payload(description=_adf_paragraph('Details'))]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], IssueRecord)
        self.assertEqual(records[0].id, 'PROJ-1')
        self.assertIn('Details', records[0].description)

    def test_sends_correct_params(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        mock_get.assert_called_once_with(
            '/rest/api/3/search',
            params={
                'jql': 'project = "PROJ" AND assignee = "developer" AND status IN ("To Do") ORDER BY updated DESC',
                'fields': 'summary,description,comment,attachment,labels',
                'maxResults': 100,
            },
        )

    def test_uses_labels_as_tags(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': [_issue_payload(labels=['repo:client', 'priority:high'])]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(records[0].tags, ['repo:client', 'priority:high'])

    def test_maps_comments_into_description(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={
                'issues': [
                    _issue_payload(
                        description=_adf_paragraph('Main'),
                        comments=[_comment_payload('Please add tests.')],
                    )
                ]
            }
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn(_COMMENT_SECTION_TITLE + ':', records[0].description)
        self.assertIn('Reviewer: Please add tests.', records[0].description)

    def test_maps_text_attachments_into_description(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'notes.txt',
            JiraAttachmentFields.MIME_TYPE: 'text/plain',
            JiraAttachmentFields.CONTENT: 'https://jira.example/attachment/1',
        }
        response = mock_response(
            json_data={'issues': [_issue_payload(attachments=[attachment])]}
        )
        attachment_response = mock_response(text='Attachment body')

        with patch.object(client, '_get', return_value=response), \
             patch.object(client.session, 'get', return_value=attachment_response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn(_TEXT_ATTACHMENTS_SECTION_TITLE + ':', records[0].description)
        self.assertIn('Attachment notes.txt:\nAttachment body', records[0].description)

    def test_maps_screenshot_attachments_into_description(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'screen.png',
            JiraAttachmentFields.MIME_TYPE: 'image/png',
            JiraAttachmentFields.CONTENT: 'https://jira.example/attachment/2',
            JiraAttachmentFields.SIZE: 12345,
        }
        response = mock_response(
            json_data={'issues': [_issue_payload(attachments=[attachment])]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn(_SCREENSHOT_SECTION_TITLE + ':', records[0].description)
        self.assertIn('screen.png', records[0].description)
        self.assertIn('12345 bytes', records[0].description)

    def test_operational_comments_excluded_from_description_but_in_all_comments(self) -> None:
        client = _make_client(
            is_operational_comment=lambda body: 'agent could not safely process' in body
        )
        response = mock_response(
            json_data={
                'issues': [
                    _issue_payload(
                        description=_adf_paragraph('Details'),
                        comments=[
                            _comment_payload('agent could not safely process this task: timeout', 'shay'),
                            _comment_payload('Please add tests.', 'Reviewer'),
                        ],
                    )
                ]
            }
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn('Reviewer: Please add tests.', records[0].description)
        self.assertNotIn('could not safely process', records[0].description)
        all_comments = getattr(records[0], ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'shay')

    def test_handles_non_dict_items_gracefully(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': ['not-a-dict', None, _issue_payload()]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(len(records), 1)

    def test_skips_malformed_issues_that_raise(self) -> None:
        malformed = {'fields': {}}  # missing KEY
        good = _issue_payload(key='PROJ-5')
        response = mock_response(json_data={'issues': [malformed, good]})
        client = _make_client()

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, 'PROJ-5')

    def test_raises_when_states_empty(self) -> None:
        client = _make_client()
        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            client.get_assigned_tasks('PROJ', 'developer', [])

    def test_returns_empty_list_when_no_issues(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(records, [])

    def test_falls_back_no_description_provided(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': [_issue_payload(description=None)]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn('No description provided.', records[0].description)


class JiraClientAddCommentTests(unittest.TestCase):
    def test_posts_plain_text_body(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('PROJ-1', 'Ready for review')

        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1/comment',
            json={'body': 'Ready for review'},
        )
        response.raise_for_status.assert_called_once_with()


class JiraClientAddTagTests(unittest.TestCase):
    def test_sends_add_label_update(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.add_tag('PROJ-1', 'in-progress')

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1',
            json={'update': {'labels': [{'add': 'in-progress'}]}},
        )
        response.raise_for_status.assert_called_once_with()


class JiraClientRemoveTagTests(unittest.TestCase):
    def test_sends_remove_label_update(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.remove_tag('PROJ-1', 'in-progress')

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1',
            json={'update': {'labels': [{'remove': 'in-progress'}]}},
        )
        response.raise_for_status.assert_called_once_with()


class JiraClientMoveIssueToStateTests(unittest.TestCase):
    def test_uses_transition_for_status_field(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '31', JiraTransitionFields.NAME: 'In Review',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Review'}}
                ]
            }
        )
        update_response = mock_response()

        with patch.object(client, '_get', return_value=transitions_response) as mock_get, \
             patch.object(client, '_post', return_value=update_response) as mock_post:
            client.move_issue_to_state('PROJ-1', 'status', 'In Review')

        mock_get.assert_called_once_with('/rest/api/3/issue/PROJ-1/transitions')
        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1/transitions',
            json={'transition': {JiraTransitionFields.ID: '31'}},
        )

    def test_uses_transition_for_status_field_case_insensitive(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '10', JiraTransitionFields.NAME: 'Done',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'Done'}}
                ]
            }
        )
        update_response = mock_response()

        with patch.object(client, '_get', return_value=transitions_response), \
             patch.object(client, '_post', return_value=update_response):
            client.move_issue_to_state('PROJ-1', 'STATUS', 'Done')

    def test_matches_transition_by_to_name(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '42', JiraTransitionFields.NAME: 'Transition A',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Review'}}
                ]
            }
        )
        update_response = mock_response()

        with patch.object(client, '_get', return_value=transitions_response), \
             patch.object(client, '_post', return_value=update_response) as mock_post:
            client.move_issue_to_state('PROJ-1', 'status', 'In Review')

        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1/transitions',
            json={'transition': {JiraTransitionFields.ID: '42'}},
        )

    def test_raises_when_transition_not_found(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '31', JiraTransitionFields.NAME: 'Done',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'Done'}}
                ]
            }
        )

        with patch.object(client, '_get', return_value=transitions_response):
            with self.assertRaisesRegex(ValueError, 'unknown jira transition: In Review'):
                client.move_issue_to_state('PROJ-1', 'status', 'In Review')

    def test_puts_field_update_for_non_status_fields(self) -> None:
        # After the update, move_issue_to_state re-fetches the issue
        # to verify the field actually changed. Both calls must be
        # mocked: the PUT (update) and the GET (verify).
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': 'High'}},
        )

        with patch.object(client, '_put', return_value=put_response) as mock_put, \
             patch.object(client, '_get', return_value=verify_response):
            client.move_issue_to_state('PROJ-1', 'priority', 'High')

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1',
            json={'fields': {'priority': 'High'}},
        )

    def test_raises_when_field_update_silently_ignored(self) -> None:
        # The bug this verification catches: Jira accepts the PUT
        # with 200 OK but the field isn't actually updated (read-only
        # field, wrong custom field id, workflow validation). Without
        # the verify, kato would believe the state changed when it
        # hadn't, leaving the operator's UI out of sync with Jira.
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': 'Low'}},  # unchanged
        )

        with patch.object(client, '_put', return_value=put_response), \
             patch.object(client, '_get', return_value=verify_response):
            with self.assertRaisesRegex(RuntimeError, 'still'):
                client.move_issue_to_state('PROJ-1', 'priority', 'High')

    def test_handles_empty_transitions_list(self) -> None:
        client = _make_client()
        transitions_response = mock_response(json_data={'transitions': []})

        with patch.object(client, '_get', return_value=transitions_response):
            with self.assertRaisesRegex(ValueError, 'unknown jira transition'):
                client.move_issue_to_state('PROJ-1', 'status', 'In Review')

    def test_verifies_dict_field_value_normalises_to_value_key(self) -> None:
        # When Jira returns the field as a dict (e.g. ``priority``
        # comes back as ``{"value": "High"}`` for some custom field
        # types), the verification path must unwrap it before the
        # equality check — otherwise every non-status update would
        # falsely "fail verification" because ``{"value": ...} != "..."``.
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': {'value': 'High'}}},
        )

        with patch.object(client, '_put', return_value=put_response), \
             patch.object(client, '_get', return_value=verify_response):
            # Should NOT raise — the dict-shape field unwraps to "High".
            client.move_issue_to_state('PROJ-1', 'priority', 'High')

    def test_verifies_dict_field_value_normalises_to_name_key(self) -> None:
        # Other Jira custom field types return ``{"name": "..."}``.
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': {'name': 'High'}}},
        )

        with patch.object(client, '_put', return_value=put_response), \
             patch.object(client, '_get', return_value=verify_response):
            client.move_issue_to_state('PROJ-1', 'priority', 'High')


class JiraClientBuildRecordTests(unittest.TestCase):
    def test_generates_branch_name_from_id(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='PROJ-42',
            summary='s',
            description='d',
            comment_entries=[],
        )

        self.assertEqual(record.branch_name, 'feature/proj-42')

    def test_uses_explicit_branch_name(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='PROJ-1',
            summary='s',
            description='d',
            comment_entries=[],
            branch_name='custom/branch',
        )

        self.assertEqual(record.branch_name, 'custom/branch')

    def test_sets_all_comments_attribute(self) -> None:
        client = _make_client()
        entries = [{'author': 'bob', 'body': 'nice'}]
        record = client._build_record(
            issue_id='PROJ-1',
            summary='s',
            description='d',
            comment_entries=entries,
        )

        self.assertEqual(getattr(record, ISSUE_ALL_COMMENTS), entries)

    def test_normalizes_none_fields(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='PROJ-1',
            summary=None,
            description=None,
            comment_entries=[],
        )

        self.assertEqual(record.summary, '')
        self.assertEqual(record.description, '')


class JiraClientAdfToTextTests(unittest.TestCase):
    def test_returns_empty_for_none(self) -> None:
        self.assertEqual(JiraClient._adf_to_text(None), '')

    def test_strips_plain_string(self) -> None:
        self.assertEqual(JiraClient._adf_to_text('  hello  '), 'hello')

    def test_joins_list(self) -> None:
        result = JiraClient._adf_to_text(['hello', ' ', 'world'])
        self.assertIn('hello', result)
        self.assertIn('world', result)

    def test_extracts_text_from_paragraph_node(self) -> None:
        node = _adf_paragraph('Hello paragraph')
        result = JiraClient._adf_to_text(node)
        self.assertIn('Hello paragraph', result)

    def test_handles_nested_content(self) -> None:
        node = {
            'type': 'doc',
            'content': [
                {
                    'type': 'paragraph',
                    'content': [
                        {'type': 'text', 'text': 'Line one'},
                        {'type': 'text', 'text': 'Line two'},
                    ],
                }
            ],
        }
        result = JiraClient._adf_to_text(node)
        self.assertIn('Line one', result)
        self.assertIn('Line two', result)

    def test_converts_non_dict_non_list_to_str(self) -> None:
        self.assertEqual(JiraClient._adf_to_text(42), '42')

    def test_uses_newline_separator_for_heading_type(self) -> None:
        node = {
            'type': 'heading',
            'content': [
                {'type': 'text', 'text': 'Title'},
            ],
        }
        result = JiraClient._adf_to_text(node)
        self.assertEqual(result, 'Title')


class JiraClientAttachmentTests(unittest.TestCase):
    def test_is_text_attachment_for_text_mime(self) -> None:
        self.assertTrue(
            JiraClient._is_text_attachment({JiraAttachmentFields.MIME_TYPE: 'text/plain'})
        )

    def test_is_text_attachment_for_application_json(self) -> None:
        self.assertTrue(
            JiraClient._is_text_attachment({JiraAttachmentFields.MIME_TYPE: 'application/json'})
        )

    def test_is_not_text_attachment_for_image(self) -> None:
        self.assertFalse(
            JiraClient._is_text_attachment({JiraAttachmentFields.MIME_TYPE: 'image/png'})
        )

    def test_attachment_name_from_filename(self) -> None:
        result = JiraClient._attachment_name({JiraAttachmentFields.FILENAME: 'notes.txt'})
        self.assertEqual(result, 'notes.txt')

    def test_attachment_name_defaults_to_unknown(self) -> None:
        result = JiraClient._attachment_name({})
        self.assertEqual(result, 'unknown')

    def test_screenshot_section_with_size(self) -> None:
        client = _make_client()
        attachments = [
            {
                JiraAttachmentFields.FILENAME: 'shot.png',
                JiraAttachmentFields.MIME_TYPE: 'image/png',
                JiraAttachmentFields.CONTENT: 'https://jira.example/file/shot.png',
                JiraAttachmentFields.SIZE: 9999,
            }
        ]
        lines = client._format_screenshot_attachments(attachments)

        self.assertEqual(len(lines), 1)
        self.assertIn('shot.png', lines[0])
        self.assertIn('9999 bytes', lines[0])

    def test_screenshot_section_without_size(self) -> None:
        client = _make_client()
        attachments = [
            {
                JiraAttachmentFields.FILENAME: 'shot.png',
                JiraAttachmentFields.MIME_TYPE: 'image/png',
                JiraAttachmentFields.CONTENT: 'https://jira.example/file/shot.png',
            }
        ]
        lines = client._format_screenshot_attachments(attachments)

        self.assertIn('image attachment', lines[0])

    def test_download_returns_empty_for_blank_url(self) -> None:
        client = _make_client()
        result = client._download_text_attachment(
            '', attachment_name='file.txt', max_chars=1000
        )
        self.assertEqual(result, '')

    def test_download_truncates_at_max_chars(self) -> None:
        client = _make_client()
        long_text = 'x' * 10000
        response = mock_response(text=long_text)

        with patch.object(client.session, 'get', return_value=response):
            result = client._download_text_attachment(
                'https://jira.example/file.txt',
                attachment_name='file.txt',
                max_chars=100,
            )

        self.assertEqual(len(result), 100)

    def test_download_returns_none_on_exception(self) -> None:
        client = _make_client()

        with patch.object(client.session, 'get', side_effect=RuntimeError('boom')):
            result = client._download_text_attachment(
                'https://jira.example/file.txt',
                attachment_name='file.txt',
                max_chars=100,
            )

        self.assertIsNone(result)

    def test_download_falls_back_to_raw_content(self) -> None:
        client = _make_client()
        response = mock_response(text='', content=b'raw bytes')

        with patch.object(client.session, 'get', return_value=response):
            result = client._download_text_attachment(
                'https://jira.example/file.txt',
                attachment_name='file.txt',
                max_chars=1000,
            )

        self.assertEqual(result, 'raw bytes')

    def test_format_text_attachments_shows_failure_when_none_returned(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'report.txt',
            JiraAttachmentFields.MIME_TYPE: 'text/plain',
            JiraAttachmentFields.CONTENT: 'https://jira.example/report.txt',
        }

        with patch.object(client.session, 'get', side_effect=RuntimeError('network')):
            lines = client._format_text_attachments([attachment])

        self.assertEqual(len(lines), 1)
        self.assertIn('could not be downloaded', lines[0])

    def test_format_text_attachments_skips_empty_content(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'empty.txt',
            JiraAttachmentFields.MIME_TYPE: 'text/plain',
            JiraAttachmentFields.CONTENT: 'https://jira.example/empty.txt',
        }
        response = mock_response(text='', content=b'')

        with patch.object(client.session, 'get', return_value=response):
            lines = client._format_text_attachments([attachment])

        self.assertEqual(lines, [])

    def test_get_attachment_with_retry_uses_session_for_absolute_urls(self) -> None:
        client = _make_client()
        response = mock_response(text='content')

        with patch.object(client.session, 'get', return_value=response) as mock_sess:
            client._get_attachment_with_retry('https://example.com/file.txt')

        mock_sess.assert_called_once()

    def test_get_attachment_with_retry_uses_get_for_relative_paths(self) -> None:
        client = _make_client()
        response = mock_response(text='content')

        with patch.object(client, '_get', return_value=response) as mock_get:
            client._get_attachment_with_retry('/relative/path')

        mock_get.assert_called_once()


class JiraClientStaticHelpersTests(unittest.TestCase):
    def test_build_assigned_tasks_query_formats_jql(self) -> None:
        result = JiraClient._build_assigned_tasks_query('PROJ', 'alice', ['To Do', 'In Progress'])

        self.assertIn('project = "PROJ"', result)
        self.assertIn('assignee = "alice"', result)
        self.assertIn('"To Do", "In Progress"', result)
        self.assertIn('ORDER BY updated DESC', result)

    def test_build_assigned_tasks_query_raises_for_empty_states(self) -> None:
        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            JiraClient._build_assigned_tasks_query('PROJ', 'alice', [])

    def test_task_tags_extracts_plain_strings(self) -> None:
        result = JiraClient._task_tags(['bug', 'enhancement'])
        self.assertEqual(result, ['bug', 'enhancement'])

    def test_task_tags_extracts_from_dict(self) -> None:
        result = JiraClient._task_tags([{'name': 'backend'}])
        self.assertEqual(result, ['backend'])

    def test_task_tags_returns_empty_for_non_list(self) -> None:
        self.assertEqual(JiraClient._task_tags(None), [])

    def test_json_items_uses_issues_key(self) -> None:
        response = mock_response(json_data={'issues': [{'key': 'A-1'}]})
        result = JiraClient._json_items(response, items_key='issues')
        self.assertEqual(result, [{'key': 'A-1'}])

    def test_json_items_returns_empty_when_key_missing(self) -> None:
        response = mock_response(json_data={'other': []})
        result = JiraClient._json_items(response, items_key='issues')
        self.assertEqual(result, [])

    def test_safe_dict_returns_dict(self) -> None:
        result = JiraClient._safe_dict({'author': {'name': 'bob'}}, 'author')
        self.assertEqual(result, {'name': 'bob'})

    def test_safe_dict_returns_empty_for_non_dict(self) -> None:
        result = JiraClient._safe_dict({'author': 'bob'}, 'author')
        self.assertEqual(result, {})


class JiraClientIssueCommentParsing(unittest.TestCase):
    def test_extracts_comments_from_fields(self) -> None:
        client = _make_client()
        fields = {
            JiraIssueFields.COMMENT: {'comments': [_comment_payload('Hello')]}
        }
        result = client._issue_comments(fields)
        self.assertEqual(len(result), 1)

    def test_returns_empty_for_missing_comment_key(self) -> None:
        client = _make_client()
        result = client._issue_comments({})
        self.assertEqual(result, [])

    def test_returns_empty_for_non_dict_comment_value(self) -> None:
        client = _make_client()
        result = client._issue_comments({JiraIssueFields.COMMENT: 'bad'})
        self.assertEqual(result, [])

    def test_extracts_attachments_from_fields(self) -> None:
        client = _make_client()
        fields = {
            JiraIssueFields.ATTACHMENT: [{'filename': 'a.txt'}]
        }
        result = client._issue_attachments(fields)
        self.assertEqual(result, [{'filename': 'a.txt'}])

    def test_returns_empty_for_missing_attachment_key(self) -> None:
        client = _make_client()
        result = client._issue_attachments({})
        self.assertEqual(result, [])


class JiraClientFlowTests(unittest.TestCase):
    """A-Z flows: create client, fetch issues, add comment, move state, add/remove tag."""

    def test_full_flow_fetch_issues_with_comments_and_attachments(self) -> None:
        client = JiraClient(
            'https://company.atlassian.net',
            'my-token',
            is_operational_comment=lambda body: body.startswith('[bot]'),
        )

        issues_payload = {
            'issues': [
                {
                    JiraIssueFields.KEY: 'ACME-101',
                    'fields': {
                        JiraIssueFields.SUMMARY: 'Implement caching',
                        JiraIssueFields.DESCRIPTION: _adf_paragraph('Cache the DB calls.'),
                        JiraIssueFields.COMMENT: {
                            'comments': [
                                _comment_payload('[bot] automated check', 'ci-bot'),
                                _comment_payload('LGTM!', 'alice'),
                            ]
                        },
                        JiraIssueFields.ATTACHMENT: [
                            {
                                JiraAttachmentFields.FILENAME: 'spec.txt',
                                JiraAttachmentFields.MIME_TYPE: 'text/plain',
                                JiraAttachmentFields.CONTENT: 'https://jira.example/spec.txt',
                            }
                        ],
                        JiraIssueFields.LABELS: ['performance', 'backend'],
                    },
                }
            ]
        }
        issues_response = mock_response(json_data=issues_payload)
        attachment_response = mock_response(text='Spec content here')

        with patch.object(client, '_get', return_value=issues_response), \
             patch.object(client.session, 'get', return_value=attachment_response):
            records = client.get_assigned_tasks('ACME', 'alice', ['In Progress'])

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.id, 'ACME-101')
        self.assertEqual(record.summary, 'Implement caching')
        self.assertIn('Cache the DB calls.', record.description)
        self.assertNotIn('[bot] automated check', record.description)
        self.assertIn('alice: LGTM!', record.description)
        self.assertIn(_TEXT_ATTACHMENTS_SECTION_TITLE, record.description)
        self.assertIn('Spec content here', record.description)
        self.assertEqual(record.tags, ['performance', 'backend'])
        self.assertEqual(record.branch_name, 'feature/acme-101')
        all_comments = getattr(record, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)

    def test_full_flow_add_comment_move_state_add_remove_tag(self) -> None:
        client = JiraClient('https://company.atlassian.net', 'my-token')

        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '21', JiraTransitionFields.NAME: 'Start Progress',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Progress'}}
                ]
            }
        )
        ok_response = mock_response()

        comment_response = mock_response()
        add_tag_response = mock_response()
        remove_tag_response = mock_response()

        with patch.object(client, '_post', side_effect=[comment_response, ok_response]) as mock_post, \
             patch.object(client, '_get', return_value=transitions_response), \
             patch.object(client, '_put', side_effect=[add_tag_response, remove_tag_response]) as mock_put:
            client.add_comment('ACME-1', 'Starting work.')
            client.move_issue_to_state('ACME-1', 'status', 'In Progress')
            client.add_tag('ACME-1', 'in-progress')
            client.remove_tag('ACME-1', 'in-progress')

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_put.call_count, 2)
        comment_call = mock_post.call_args_list[0]
        self.assertIn('comment', comment_call.args[0])
        add_tag_call = mock_put.call_args_list[0]
        self.assertIn('add', str(add_tag_call))
        remove_tag_call = mock_put.call_args_list[1]
        self.assertIn('remove', str(remove_tag_call))

    def test_full_flow_validate_then_get_tasks(self) -> None:
        client = JiraClient('https://company.atlassian.net', 'my-token')

        validate_response = mock_response(json_data={'issues': []})
        issues_response = mock_response(
            json_data={
                'issues': [
                    _issue_payload(key='ACME-77', summary='Task A', description=_adf_paragraph('Do it.'))
                ]
            }
        )

        with patch.object(client, '_get', side_effect=[validate_response, issues_response]):
            client.validate_connection('ACME', 'alice', ['To Do'])
            records = client.get_assigned_tasks('ACME', 'alice', ['To Do'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, 'ACME-77')
        self.assertEqual(records[0].summary, 'Task A')


class JiraClientDefensiveBranchTests(unittest.TestCase):
    """Cover the small ``isinstance``/skip defensive branches."""

    def _client(self):
        return JiraClient('https://jira.example.com', 'jira-token', 'alice@example')

    def test_find_transition_skips_non_dict_entries(self) -> None:
        # Line 140: non-dict transition in the list → skip.
        client = self._client()
        valid_transition = {
            'id': '21',
            'name': 'In Progress',
            'to': {'name': 'In Progress'},
        }
        response = mock_response(json_data={
            'transitions': ['not a dict', valid_transition],
        })
        with patch.object(client, '_get_with_retry', return_value=response):
            t = client._find_transition('ACME-1', 'In Progress')
        self.assertEqual(t['id'], '21')

    def test_to_record_coerces_non_dict_fields_to_empty(self) -> None:
        # Line 156: ``fields`` isn't a dict → fall back to {}.
        client = self._client()
        record = client._to_record({
            'key': 'ACME-9', 'fields': 'oops-not-a-dict',
        })
        self.assertEqual(record.id, 'ACME-9')

    def test_normalize_issue_records_skips_when_include_returns_false(self) -> None:
        # Line 349: ``include`` is provided and returns False → skip.
        client = self._client()
        result = client._normalize_issue_records(
            [
                {'key': 'KEEP-1', 'fields': {'summary': 'keep'}},
                {'key': 'DROP-1', 'fields': {'summary': 'drop'}},
            ],
            to_record=lambda item: IssueRecord(
                id=item['key'], summary=item['fields']['summary'], description='',
            ),
            include=lambda item: item['key'].startswith('KEEP'),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'KEEP-1')

    def test_comment_lines_skips_non_dict_comment_entries(self) -> None:
        # Line 243: non-dict comment in comments list → skip.
        client = self._client()
        result = client._comment_lines([
            'not a dict',
            {ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'real comment'},
        ])
        self.assertEqual(len(result), 1)
        self.assertIn('real comment', result[0])

    def test_format_screenshot_attachments_skips_non_dict_entries(self) -> None:
        # Line 271: non-dict attachment → skip.
        client = self._client()
        result = client._format_screenshot_attachments([
            'not a dict',
            {'mimeType': 'image/png', 'filename': 'x.png',
             'content': 'http://x', 'size': 100},
        ])
        self.assertEqual(len(result), 1)
        self.assertIn('x.png', result[0])

    def test_normalize_issue_records_skips_non_dict_items(self) -> None:
        # Line 349: non-dict item → skip.
        client = self._client()
        result = client._normalize_issue_records(
            ['junk-not-a-dict', {'key': 'ACME-1', 'fields': {'summary': 's'}}],
            to_record=lambda item: IssueRecord(
                id=item['key'], summary=item['fields']['summary'], description='',
            ),
        )
        self.assertEqual(len(result), 1)

    def test_json_items_returns_empty_when_items_key_set_but_payload_not_dict(self) -> None:
        # Line 379: items_key requested but payload is a list → return [].
        response = mock_response(json_data=['list', 'not', 'dict'])
        result = JiraClient._json_items(response, items_key='issues')
        self.assertEqual(result, [])

    def test_build_comment_entries_skips_non_dict_and_blank_body(self) -> None:
        # Lines 394, 397: non-dict skipped; blank body skipped.
        result = JiraClient._build_comment_entries(
            [
                'not a dict',  # skipped (line 394)
                {'author': 'alice', 'body': ''},  # blank body skipped (line 397)
                {'author': 'bob', 'body': 'real'},
            ],
            extract_body=lambda c: c.get('body', ''),
            extract_author=lambda c: c.get('author', ''),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][ISSUE_COMMENT_AUTHOR], 'bob')
