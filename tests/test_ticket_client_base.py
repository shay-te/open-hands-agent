import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.client.ticket_client_base import TicketClientBase
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data.fields import TaskCommentFields


class TicketClientBaseTests(unittest.TestCase):
    def test_recognizes_agent_operational_comment_prefixes(self) -> None:
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'Kato agent started working on this task in repository backend.'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'Kato completed task PROJ-1: Fix the auth flow.'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'Kato addressed review comment 99 on pull request 17.'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'Kato agent stopped working on this task: gateway timeout'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'Kato agent could not safely process this task: timeout'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'Kato agent skipped this task because the task definition is too thin to work from safely.'
            )
        )
        self.assertFalse(
            TicketClientBase._is_agent_operational_comment(
                'Please add tests before merging.'
            )
        )

    def test_active_execution_blocking_comment_tracks_completion_comment(self) -> None:
        comment = TicketClientBase.active_execution_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato completed task PROJ-1: Fix the auth flow.'
                    ),
                }
            ]
        )

        self.assertEqual(
            comment,
            'Kato completed task PROJ-1: Fix the auth flow.',
        )

    def test_active_execution_blocking_comment_clears_completion_after_explicit_retry_instruction(self) -> None:
        comment = TicketClientBase.active_execution_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato completed task PROJ-1: Fix the auth flow.'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'kato: retry approved for this task.',
                },
            ]
        )

        self.assertEqual(comment, '')

    def test_active_execution_blocking_comment_tracks_started_working_comment(self) -> None:
        comment = TicketClientBase.active_execution_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'kato',
                    TaskCommentFields.BODY: (
                        'Kato agent started working on this task in repository backend.'
                    ),
                }
            ]
        )

        self.assertEqual(
            comment,
            'Kato agent started working on this task in repository backend.',
        )

    def test_active_execution_blocking_comment_started_working_clears_after_retry_instruction(self) -> None:
        comment = TicketClientBase.active_execution_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'kato',
                    TaskCommentFields.BODY: (
                        'Kato agent started working on this task in repository backend.'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: 'kato: retry approved',
                },
            ]
        )

        self.assertEqual(comment, '')

    def test_active_execution_blocking_comment_completion_supersedes_started_working(self) -> None:
        comment = TicketClientBase.active_execution_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'kato',
                    TaskCommentFields.BODY: (
                        'Kato agent started working on this task in repository backend.'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'kato',
                    TaskCommentFields.BODY: 'Kato completed task PROJ-1: Fix the auth flow.',
                },
            ]
        )

        self.assertEqual(comment, 'Kato completed task PROJ-1: Fix the auth flow.')

    def test_is_pre_start_blocking_comment_matches_only_pre_start_blockers(self) -> None:
        self.assertTrue(
            TicketClientBase.is_pre_start_blocking_comment(
                'Kato agent could not safely process this task: timeout'
            )
        )
        self.assertTrue(
            TicketClientBase.is_pre_start_blocking_comment(
                'Kato agent skipped this task because the task definition is too thin to work from safely.'
            )
        )
        self.assertFalse(
            TicketClientBase.is_pre_start_blocking_comment(
                'Kato agent stopped working on this task: branch conflict'
            )
        )
        self.assertFalse(
            TicketClientBase.is_pre_start_blocking_comment(
                'Kato completed task PROJ-1: Fix the auth flow.'
            )
        )

    def test_set_task_comments_persists_normalized_comments_on_task(self) -> None:
        task = Task(
            id='PROJ-1',
            summary='fix it already',
            description='Details',
            branch_name='feature/proj-1',
        )
        comments = [
            {
                TaskCommentFields.AUTHOR: 'reviewer',
                TaskCommentFields.BODY: 'Please add tests.',
            }
        ]

        TicketClientBase._set_task_comments(task, comments)

        self.assertEqual(getattr(task, TaskCommentFields.ALL_COMMENTS), comments)


class StaticHelperTests(unittest.TestCase):
    """Stateless helpers that are pure functions — no instance needed."""

    def test_json_items_returns_list_when_payload_is_list(self) -> None:
        response = SimpleNamespace(json=lambda: [{'a': 1}, {'b': 2}])
        self.assertEqual(
            TicketClientBase._json_items(response),
            [{'a': 1}, {'b': 2}],
        )

    def test_json_items_extracts_items_key_when_payload_is_dict(self) -> None:
        response = SimpleNamespace(json=lambda: {'items': [{'a': 1}]})
        self.assertEqual(
            TicketClientBase._json_items(response, items_key='items'),
            [{'a': 1}],
        )

    def test_json_items_returns_empty_when_items_key_missing(self) -> None:
        response = SimpleNamespace(json=lambda: {'other': []})
        self.assertEqual(
            TicketClientBase._json_items(response, items_key='items'),
            [],
        )

    def test_json_items_returns_empty_when_dict_unexpected(self) -> None:
        response = SimpleNamespace(json=lambda: None)
        self.assertEqual(
            TicketClientBase._json_items(response, items_key='items'),
            [],
        )

    def test_json_items_no_items_key_with_non_list_returns_empty(self) -> None:
        response = SimpleNamespace(json=lambda: {'unexpected': 'dict'})
        self.assertEqual(TicketClientBase._json_items(response), [])

    def test_task_tags_extracts_name_from_dict_entries(self) -> None:
        self.assertEqual(
            TicketClientBase._task_tags([
                {'name': 'bug'}, {'label': 'priority'}, {'text': 'urgent'},
            ]),
            ['bug', 'priority', 'urgent'],
        )

    def test_task_tags_treats_strings_directly(self) -> None:
        self.assertEqual(
            TicketClientBase._task_tags(['alpha', '', 'beta']),
            ['alpha', 'beta'],
        )

    def test_task_tags_non_list_returns_empty(self) -> None:
        self.assertEqual(TicketClientBase._task_tags('not a list'), [])
        self.assertEqual(TicketClientBase._task_tags(None), [])

    def test_safe_dict_returns_value_when_dict(self) -> None:
        self.assertEqual(
            TicketClientBase._safe_dict({'k': {'a': 1}}, 'k'),
            {'a': 1},
        )

    def test_safe_dict_returns_empty_when_not_dict(self) -> None:
        # Missing key or wrong type → {} so callers can chain ``.get()``
        # without scattering ``isinstance`` checks.
        self.assertEqual(TicketClientBase._safe_dict({}, 'k'), {})
        self.assertEqual(TicketClientBase._safe_dict({'k': 'str'}, 'k'), {})
        self.assertEqual(TicketClientBase._safe_dict({'k': None}, 'k'), {})

    def test_task_comment_entry_returns_normalized_dict(self) -> None:
        entry = TicketClientBase._task_comment_entry('  Alice  ', '  hi  ')
        self.assertEqual(
            entry,
            {TaskCommentFields.AUTHOR: 'Alice', TaskCommentFields.BODY: 'hi'},
        )

    def test_task_comment_entry_returns_none_when_body_blank(self) -> None:
        # Critical contract: blank-body comments are dropped, not passed
        # through as ``{'author': 'X', 'body': ''}`` which would clutter
        # the description section with empty bullet lines.
        self.assertIsNone(TicketClientBase._task_comment_entry('Alice', ''))
        self.assertIsNone(TicketClientBase._task_comment_entry('Alice', '   '))

    def test_task_comment_entry_uses_unknown_for_blank_author(self) -> None:
        entry = TicketClientBase._task_comment_entry('', 'hi')
        self.assertEqual(entry[TaskCommentFields.AUTHOR], 'unknown')

    def test_is_text_attachment_mime_type_recognizes_text_prefix(self) -> None:
        self.assertTrue(TicketClientBase._is_text_attachment_mime_type('text/plain'))
        self.assertTrue(TicketClientBase._is_text_attachment_mime_type('text/markdown'))

    def test_is_text_attachment_mime_type_recognizes_extra_types(self) -> None:
        # Specific non-text/ MIME types in the class allowlist.
        for mime in TicketClientBase._TEXT_ATTACHMENT_MIME_TYPES:
            self.assertTrue(TicketClientBase._is_text_attachment_mime_type(mime))

    def test_is_text_attachment_mime_type_rejects_unknown(self) -> None:
        self.assertFalse(TicketClientBase._is_text_attachment_mime_type('image/png'))
        self.assertFalse(TicketClientBase._is_text_attachment_mime_type(''))

    def test_attachment_download_failure_text(self) -> None:
        self.assertEqual(
            TicketClientBase._attachment_download_failure_text('config.yaml'),
            'Attachment config.yaml could not be downloaded.',
        )

    def test_normalized_allowed_states_lowercases_and_dedupes(self) -> None:
        result = TicketClientBase._normalized_allowed_states(
            ['Open', 'open ', 'In Progress', ''],
        )
        self.assertEqual(result, {'open', 'in progress'})

    def test_matches_allowed_state_allows_anything_when_set_empty(self) -> None:
        # Empty filter = no filtering (lets every state through).
        self.assertTrue(TicketClientBase._matches_allowed_state('Any', set()))

    def test_matches_allowed_state_uses_normalized_comparison(self) -> None:
        allowed = {'open', 'in progress'}
        self.assertTrue(TicketClientBase._matches_allowed_state('OPEN', allowed))
        self.assertTrue(TicketClientBase._matches_allowed_state(' In Progress ', allowed))
        self.assertFalse(TicketClientBase._matches_allowed_state('Closed', allowed))


class BuildCommentEntriesTests(unittest.TestCase):
    def test_extracts_via_callbacks_and_skips_filtered(self) -> None:
        comments = [
            {'creator': 'alice', 'text': 'hi'},
            {'creator': 'bob', 'text': '', 'skip_me': True},
            'not a dict',
            {'creator': 'carol', 'text': 'ok'},
        ]
        entries = TicketClientBase._build_comment_entries(
            comments,
            extract_body=lambda c: c.get('text', ''),
            extract_author=lambda c: c.get('creator', ''),
            skip=lambda c: c.get('skip_me', False),
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][TaskCommentFields.AUTHOR], 'alice')
        self.assertEqual(entries[1][TaskCommentFields.AUTHOR], 'carol')

    def test_skip_callback_optional(self) -> None:
        entries = TicketClientBase._build_comment_entries(
            [{'creator': 'a', 'text': 'hi'}],
            extract_body=lambda c: c.get('text', ''),
            extract_author=lambda c: c.get('creator', ''),
        )
        self.assertEqual(len(entries), 1)

    def test_drops_comments_whose_entry_is_none(self) -> None:
        # Branch 336->330: when ``_task_comment_entry`` returns None (blank
        # body), the for-loop must skip the append and continue iterating.
        entries = TicketClientBase._build_comment_entries(
            [
                {'creator': 'alice', 'text': '   '},  # blank body -> None entry
                {'creator': 'bob', 'text': 'real'},
            ],
            extract_body=lambda c: c.get('text', ''),
            extract_author=lambda c: c.get('creator', ''),
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][TaskCommentFields.AUTHOR], 'bob')


class BuildTaskTests(unittest.TestCase):
    def _client(self):
        return TicketClientBase.__new__(TicketClientBase)

    def test_builds_task_with_explicit_branch_name(self) -> None:
        task = self._client()._build_task(
            issue_id='PROJ-1',
            summary='Fix',
            description='details',
            comment_entries=[],
            branch_name='feature/foo',
        )
        self.assertEqual(task.id, 'PROJ-1')
        self.assertEqual(task.branch_name, 'feature/foo')

    def test_builds_task_with_default_branch_name(self) -> None:
        task = self._client()._build_task(
            issue_id='PROJ-1',
            summary='Fix',
            description='details',
            comment_entries=[],
        )
        # Defaults to ``feature/<lowercased issue id>``.
        self.assertEqual(task.branch_name, 'feature/proj-1')

    def test_builds_task_attaches_comment_entries(self) -> None:
        entries = [
            {TaskCommentFields.AUTHOR: 'a', TaskCommentFields.BODY: 'hi'},
        ]
        task = self._client()._build_task(
            issue_id='PROJ-1',
            summary='Fix',
            description='details',
            comment_entries=entries,
        )
        self.assertEqual(getattr(task, TaskCommentFields.ALL_COMMENTS), entries)


class NormalizeIssueTasksTests(unittest.TestCase):
    def _make_client(self):
        # Minimal: bypass __init__ and stamp a logger directly.
        client = TicketClientBase.__new__(TicketClientBase)
        client.logger = MagicMock()
        return client

    def test_returns_tasks_from_to_task(self) -> None:
        client = self._make_client()
        items = [{'id': '1'}, {'id': '2'}, 'not a dict']
        tasks = client._normalize_issue_tasks(
            items, to_task=lambda item: SimpleNamespace(id=item['id']),
        )
        self.assertEqual([t.id for t in tasks], ['1', '2'])

    def test_applies_include_filter(self) -> None:
        client = self._make_client()
        items = [{'id': '1', 'ok': True}, {'id': '2', 'ok': False}]
        tasks = client._normalize_issue_tasks(
            items,
            to_task=lambda item: SimpleNamespace(id=item['id']),
            include=lambda item: item['ok'],
        )
        self.assertEqual([t.id for t in tasks], ['1'])

    def test_logs_and_continues_on_to_task_failure(self) -> None:
        client = self._make_client()
        client.provider_name = 'fake'

        def to_task(item):
            if item['id'] == 'bad':
                raise ValueError('parse error')
            return SimpleNamespace(id=item['id'])

        tasks = client._normalize_issue_tasks(
            [{'id': '1'}, {'id': 'bad'}, {'id': '2'}],
            to_task=to_task,
        )
        # Bad item is skipped, others survive; logger.exception called once.
        self.assertEqual([t.id for t in tasks], ['1', '2'])
        client.logger.exception.assert_called_once()


class BestEffortIssueItemsTests(unittest.TestCase):
    def _make_client(self):
        client = TicketClientBase.__new__(TicketClientBase)
        client.logger = MagicMock()
        return client

    def test_returns_operation_result_on_success(self) -> None:
        client = self._make_client()
        result = client._best_effort_issue_items(
            'PROJ-1', 'comments', lambda: [{'a': 1}],
        )
        self.assertEqual(result, [{'a': 1}])

    def test_returns_empty_and_logs_on_failure(self) -> None:
        client = self._make_client()

        def boom():
            raise RuntimeError('api down')

        result = client._best_effort_issue_items('PROJ-1', 'comments', boom)
        self.assertEqual(result, [])
        client.logger.exception.assert_called_once()


class FormatTextAttachmentLinesTests(unittest.TestCase):
    def _make_client(self):
        client = TicketClientBase.__new__(TicketClientBase)
        client.logger = MagicMock()
        return client

    def test_yields_named_attachments(self) -> None:
        client = self._make_client()
        lines = client._format_text_attachment_lines(
            [{'name': 'a.txt'}, {'name': 'b.txt'}],
            is_text_attachment=lambda a: True,
            read_text_attachment=lambda a: f'content of {a["name"]}',
            attachment_name=lambda a: a['name'],
        )
        self.assertEqual(len(lines), 2)
        self.assertIn('a.txt', lines[0])
        self.assertIn('content of a.txt', lines[0])

    def test_skips_non_text_attachments(self) -> None:
        client = self._make_client()
        lines = client._format_text_attachment_lines(
            [{'name': 'img.png'}],
            is_text_attachment=lambda a: False,
            read_text_attachment=lambda a: 'unreachable',
            attachment_name=lambda a: a['name'],
        )
        self.assertEqual(lines, [])

    def test_uses_failure_text_when_read_returns_none(self) -> None:
        # ``None`` from read_text_attachment means "download failed";
        # the message becomes the placeholder instead of being dropped silently.
        client = self._make_client()
        lines = client._format_text_attachment_lines(
            [{'name': 'broken.txt'}],
            is_text_attachment=lambda a: True,
            read_text_attachment=lambda a: None,
            attachment_name=lambda a: a['name'],
        )
        self.assertEqual(len(lines), 1)
        self.assertIn('could not be downloaded', lines[0])

    def test_skips_empty_content_silently(self) -> None:
        # Empty string (not None) means "downloaded fine but empty" —
        # drop to keep the description tidy.
        client = self._make_client()
        lines = client._format_text_attachment_lines(
            [{'name': 'empty.txt'}],
            is_text_attachment=lambda a: True,
            read_text_attachment=lambda a: '',
            attachment_name=lambda a: a['name'],
        )
        self.assertEqual(lines, [])

    def test_non_dict_attachments_skipped(self) -> None:
        client = self._make_client()
        lines = client._format_text_attachment_lines(
            ['not a dict', {'name': 'real.txt'}],
            is_text_attachment=lambda a: True,
            read_text_attachment=lambda a: 'hi',
            attachment_name=lambda a: a['name'],
        )
        self.assertEqual(len(lines), 1)


class AppendDescriptionSectionTests(unittest.TestCase):
    def test_no_op_when_lines_empty(self) -> None:
        sections: list[str] = ['Existing']
        TicketClientBase._append_description_section(
            sections, 'Title', [],
        )
        self.assertEqual(sections, ['Existing'])

    def test_appends_section_with_title(self) -> None:
        sections: list[str] = ['Existing']
        TicketClientBase._append_description_section(
            sections, 'Attachments', ['line a', 'line b'],
        )
        self.assertEqual(sections, ['Existing', 'Attachments:\nline a\nline b'])

    def test_custom_separator(self) -> None:
        sections: list[str] = []
        TicketClientBase._append_description_section(
            sections, 'Attachments', ['a', 'b'],
            separator='\n\n',
        )
        self.assertEqual(sections, ['Attachments:\na\n\nb'])


class BuildTaskDescriptionWithAttachmentSectionsTests(unittest.TestCase):
    def _make_client(self):
        client = TicketClientBase.__new__(TicketClientBase)
        return client

    def test_joins_description_comments_text_and_screenshot_sections(self) -> None:
        client = self._make_client()
        result = client._build_task_description_with_attachment_sections(
            'Main description',
            [
                {TaskCommentFields.AUTHOR: 'a', TaskCommentFields.BODY: 'hi'},
            ],
            text_attachment_lines=['attachment txt'],
            screenshot_lines=['screenshot 1'],
        )
        self.assertIn('Main description', result)
        self.assertIn('Untrusted issue comments', result)
        self.assertIn('attachment txt', result)
        self.assertIn('screenshot 1', result)

    def test_no_description_falls_back_to_placeholder(self) -> None:
        client = self._make_client()
        result = client._build_task_description_with_attachment_sections(
            '',
            [],
            text_attachment_lines=[],
            screenshot_lines=[],
        )
        self.assertEqual(result, 'No description provided.')


class IsRetryOverrideCommentTests(unittest.TestCase):
    def test_returns_false_for_operational_comment(self) -> None:
        # Kato's own operational comments never count as override approvals.
        self.assertFalse(
            TicketClientBase._is_retry_override_comment(
                'Kato agent stopped working on this task: timeout',
            )
        )

    def test_returns_false_for_blank(self) -> None:
        self.assertFalse(TicketClientBase._is_retry_override_comment(''))
        self.assertFalse(TicketClientBase._is_retry_override_comment('   '))


class GetAttachmentWithRetryTests(unittest.TestCase):
    def test_uses_absolute_url_via_session_directly(self) -> None:
        # Absolute URL goes through ``run_with_retry`` wrapping a session.get
        # call. We patch the retry helper to invoke the lambda once so we can
        # observe the session.get arguments.
        client = TicketClientBase.__new__(TicketClientBase)
        client.session = MagicMock()
        client.max_retries = 0
        client.process_kwargs = lambda: {'headers': {'X-Test': '1'}}
        client.session.get.return_value = 'response-object'

        with patch(
            'kato_core_lib.client.ticket_client_base.run_with_retry',
            side_effect=lambda op, retries, operation_name: op(),
        ):
            result = client._get_attachment_with_retry('https://example.com/file.txt')

        self.assertEqual(result, 'response-object')
        client.session.get.assert_called_once_with(
            'https://example.com/file.txt', headers={'X-Test': '1'},
        )

    def test_falls_back_to_get_with_retry_for_relative_path(self) -> None:
        # Relative path (no scheme) → use the configured base via _get_with_retry.
        client = TicketClientBase.__new__(TicketClientBase)
        client._get_with_retry = MagicMock(return_value='via-retry')

        result = client._get_attachment_with_retry('/api/files/123')
        self.assertEqual(result, 'via-retry')
        client._get_with_retry.assert_called_once_with('/api/files/123')


class DownloadTextAttachmentTests(unittest.TestCase):
    def _make_client(self):
        client = TicketClientBase.__new__(TicketClientBase)
        client.logger = MagicMock()
        return client

    def test_returns_empty_when_url_blank(self) -> None:
        client = self._make_client()
        self.assertEqual(
            client._download_text_attachment(
                '', attachment_name='x', max_chars=100,
            ),
            '',
        )

    def test_returns_truncated_text(self) -> None:
        client = self._make_client()
        response = MagicMock()
        response.text = 'abcdefghij'
        client._get_attachment_with_retry = MagicMock(return_value=response)
        result = client._download_text_attachment(
            'https://x', attachment_name='x', max_chars=5,
        )
        self.assertEqual(result, 'abcde')

    def test_falls_back_to_raw_content_when_text_missing(self) -> None:
        client = self._make_client()
        response = MagicMock()
        response.text = ''
        response.content = b'hello world'
        client._get_attachment_with_retry = MagicMock(return_value=response)
        result = client._download_text_attachment(
            'https://x', attachment_name='x', max_chars=5,
        )
        self.assertEqual(result, 'hello')

    def test_returns_empty_when_raw_content_blank(self) -> None:
        client = self._make_client()
        response = MagicMock()
        response.text = ''
        response.content = b''
        client._get_attachment_with_retry = MagicMock(return_value=response)
        result = client._download_text_attachment(
            'https://x', attachment_name='x', max_chars=5,
        )
        self.assertEqual(result, '')

    def test_returns_none_on_exception(self) -> None:
        # Any exception path returns None so the caller can render the
        # ``Attachment X could not be downloaded.`` placeholder.
        client = self._make_client()
        client._get_attachment_with_retry = MagicMock(side_effect=RuntimeError('boom'))
        result = client._download_text_attachment(
            'https://x', attachment_name='x', max_chars=5,
        )
        self.assertIsNone(result)
        client.logger.exception.assert_called_once()


class PostKatoTagMarkerCommentTests(unittest.TestCase):
    def test_emits_machine_readable_marker_via_add_comment(self) -> None:
        client = TicketClientBase.__new__(TicketClientBase)
        client.add_comment = MagicMock()
        client._post_kato_tag_marker_comment(
            'PROJ-1', 'add', 'bug',
        )
        client.add_comment.assert_called_once()
        issue_id, body = client.add_comment.call_args.args
        self.assertEqual(issue_id, 'PROJ-1')
        self.assertIn('"action": "add"', body)
        self.assertIn('"tag": "bug"', body)
        self.assertIn('added tag `bug`', body)
