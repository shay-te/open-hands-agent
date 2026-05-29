from __future__ import annotations

import unittest
from unittest.mock import patch

from provider_client_base.provider_client_base.client.issue_client_base import (
    IssueClientBase,
    _COMMENT_SECTION_TITLE,
    _TEXT_ATTACHMENT_MIME_TYPES,
)
from provider_client_base.provider_client_base.data.issue_record import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    IssueRecord,
)
from tests.utils import mock_response


class ConcreteIssueClient(IssueClientBase):
    provider_name = 'test'


def _make_client(**kwargs) -> ConcreteIssueClient:
    return ConcreteIssueClient('https://api.example.com', 'token', timeout=30, **kwargs)


# ---------------------------------------------------------------------------
# IssueRecord + constants
# ---------------------------------------------------------------------------

class IssueRecordTests(unittest.TestCase):
    def test_constants(self) -> None:
        self.assertEqual(ISSUE_COMMENT_AUTHOR, 'author')
        self.assertEqual(ISSUE_COMMENT_BODY, 'body')
        self.assertEqual(ISSUE_ALL_COMMENTS, 'all_comments')

    def test_defaults(self) -> None:
        record = IssueRecord()
        self.assertEqual(record.id, '')
        self.assertEqual(record.summary, '')
        self.assertEqual(record.description, '')
        self.assertEqual(record.branch_name, '')
        self.assertEqual(record.tags, [])
        self.assertEqual(record.all_comments, [])

    def test_independent_default_lists(self) -> None:
        a = IssueRecord()
        b = IssueRecord()
        a.tags.append('x')
        a.all_comments.append({'k': 'v'})
        self.assertEqual(b.tags, [])
        self.assertEqual(b.all_comments, [])


# ---------------------------------------------------------------------------
# _build_record
# ---------------------------------------------------------------------------

class BuildRecordTests(unittest.TestCase):
    def test_builds_with_explicit_branch_and_tags(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='  ABC-1  ',
            summary='  Title  ',
            description='  Body  ',
            comment_entries=[{ISSUE_COMMENT_AUTHOR: 'a', ISSUE_COMMENT_BODY: 'b'}],
            branch_name='custom/branch',
            tags=['bug'],
        )
        self.assertEqual(record.id, 'ABC-1')
        self.assertEqual(record.summary, 'Title')
        self.assertEqual(record.description, 'Body')
        self.assertEqual(record.branch_name, 'custom/branch')
        self.assertEqual(record.tags, ['bug'])
        self.assertEqual(
            getattr(record, ISSUE_ALL_COMMENTS),
            [{ISSUE_COMMENT_AUTHOR: 'a', ISSUE_COMMENT_BODY: 'b'}],
        )

    def test_derives_branch_name_when_blank(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='My Issue 5',
            summary='s',
            description='d',
            comment_entries=[],
        )
        self.assertEqual(record.branch_name, 'feature/my-issue-5')

    def test_tags_defaults_to_empty_list(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='1', summary='s', description='d', comment_entries=[], tags=None,
        )
        self.assertEqual(record.tags, [])


# ---------------------------------------------------------------------------
# _normalize_issue_records
# ---------------------------------------------------------------------------

class NormalizeIssueRecordsTests(unittest.TestCase):
    def test_skips_non_dicts(self) -> None:
        client = _make_client()
        result = client._normalize_issue_records(
            ['not-a-dict', {'id': '1'}],
            to_record=lambda item: IssueRecord(id=item['id']),
        )
        self.assertEqual([r.id for r in result], ['1'])

    def test_applies_include_filter(self) -> None:
        client = _make_client()
        result = client._normalize_issue_records(
            [{'id': '1', 'keep': True}, {'id': '2', 'keep': False}],
            to_record=lambda item: IssueRecord(id=item['id']),
            include=lambda item: item['keep'],
        )
        self.assertEqual([r.id for r in result], ['1'])

    def test_swallows_record_build_errors_and_logs(self) -> None:
        client = _make_client()

        def to_record(item):
            raise KeyError('missing')

        with patch.object(client.logger, 'exception') as mock_log:
            result = client._normalize_issue_records(
                [{'id': '1'}], to_record=to_record,
            )
        self.assertEqual(result, [])
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# _build_description_with_comments / _comment_lines
# ---------------------------------------------------------------------------

class DescriptionTests(unittest.TestCase):
    def test_description_only_when_no_comments(self) -> None:
        client = _make_client()
        result = client._build_description_with_comments('A description', [])
        self.assertEqual(result, 'A description')

    def test_placeholder_when_blank_description(self) -> None:
        client = _make_client()
        result = client._build_description_with_comments('', [])
        self.assertEqual(result, 'No description provided.')

    def test_appends_comment_section(self) -> None:
        client = _make_client()
        result = client._build_description_with_comments(
            'desc',
            [{ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'hi'}],
        )
        self.assertIn(f'{_COMMENT_SECTION_TITLE}:', result)
        self.assertIn('- alice: hi', result)

    def test_comment_lines_skip_non_dict_and_empty(self) -> None:
        client = _make_client()
        result = client._comment_lines([
            'not-a-dict',
            {ISSUE_COMMENT_BODY: '   '},
            {ISSUE_COMMENT_AUTHOR: 'bob', ISSUE_COMMENT_BODY: 'kept'},
        ])
        self.assertEqual(result, ['- bob: kept'])

    def test_comment_lines_default_author(self) -> None:
        client = _make_client()
        result = client._comment_lines([{ISSUE_COMMENT_BODY: 'body'}])
        self.assertEqual(result, ['- unknown: body'])

    def test_comment_lines_skip_operational(self) -> None:
        client = _make_client()
        client._is_operational_comment = lambda body: body.startswith('[bot]')
        result = client._comment_lines([
            {ISSUE_COMMENT_AUTHOR: 'kato', ISSUE_COMMENT_BODY: '[bot] op'},
            {ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'real'},
        ])
        self.assertEqual(result, ['- alice: real'])


# ---------------------------------------------------------------------------
# _build_comment_entries
# ---------------------------------------------------------------------------

class BuildCommentEntriesTests(unittest.TestCase):
    def test_builds_entries(self) -> None:
        entries = IssueClientBase._build_comment_entries(
            [{'b': 'hello', 'a': 'alice'}],
            extract_body=lambda c: c['b'],
            extract_author=lambda c: c['a'],
        )
        self.assertEqual(
            entries,
            [{ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'hello'}],
        )

    def test_skips_non_dict_and_empty_body(self) -> None:
        entries = IssueClientBase._build_comment_entries(
            ['x', {'b': '  '}, {'b': 'kept', 'a': 'bob'}],
            extract_body=lambda c: c.get('b', ''),
            extract_author=lambda c: c.get('a'),
        )
        self.assertEqual(
            entries, [{ISSUE_COMMENT_AUTHOR: 'bob', ISSUE_COMMENT_BODY: 'kept'}],
        )

    def test_default_author_when_blank(self) -> None:
        entries = IssueClientBase._build_comment_entries(
            [{'b': 'body'}],
            extract_body=lambda c: c['b'],
            extract_author=lambda c: c.get('a'),
        )
        self.assertEqual(entries[0][ISSUE_COMMENT_AUTHOR], 'unknown')

    def test_skip_predicate(self) -> None:
        entries = IssueClientBase._build_comment_entries(
            [{'b': 'drop'}, {'b': 'keep'}],
            extract_body=lambda c: c['b'],
            extract_author=lambda c: 'a',
            skip=lambda c: c['b'] == 'drop',
        )
        self.assertEqual([e[ISSUE_COMMENT_BODY] for e in entries], ['keep'])


# ---------------------------------------------------------------------------
# state filtering / tags
# ---------------------------------------------------------------------------

class StateAndTagTests(unittest.TestCase):
    def test_normalized_allowed_states(self) -> None:
        self.assertEqual(
            IssueClientBase._normalized_allowed_states(['Open', ' CLOSED ', '', '  ']),
            {'open', 'closed'},
        )

    def test_matches_allowed_state_empty_allows_all(self) -> None:
        self.assertTrue(IssueClientBase._matches_allowed_state('anything', set()))

    def test_matches_allowed_state_membership(self) -> None:
        self.assertTrue(IssueClientBase._matches_allowed_state('Open', {'open'}))
        self.assertFalse(IssueClientBase._matches_allowed_state('closed', {'open'}))

    def test_task_tags_strings(self) -> None:
        self.assertEqual(
            IssueClientBase._task_tags(['bug', '  ', 'urgent']), ['bug', 'urgent'],
        )

    def test_task_tags_dicts(self) -> None:
        self.assertEqual(
            IssueClientBase._task_tags(
                [{'name': 'a'}, {'label': 'b'}, {'text': 'c'}, {'x': 'y'}],
            ),
            ['a', 'b', 'c'],
        )

    def test_task_tags_non_list(self) -> None:
        self.assertEqual(IssueClientBase._task_tags(None), [])
        self.assertEqual(IssueClientBase._task_tags('string'), [])
        self.assertEqual(IssueClientBase._task_tags({}), [])


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------

class JsonItemsTests(unittest.TestCase):
    def test_top_level_list(self) -> None:
        response = mock_response(json_data=[{'a': 1}])
        self.assertEqual(IssueClientBase._json_items(response), [{'a': 1}])

    def test_top_level_non_list_returns_empty(self) -> None:
        response = mock_response(json_data={'not': 'list'})
        self.assertEqual(IssueClientBase._json_items(response), [])

    def test_none_json_with_items_key(self) -> None:
        response = mock_response(json_data=None)
        self.assertEqual(IssueClientBase._json_items(response, items_key='values'), [])

    def test_items_key_extraction(self) -> None:
        response = mock_response(json_data={'values': [{'a': 1}]})
        self.assertEqual(
            IssueClientBase._json_items(response, items_key='values'), [{'a': 1}],
        )

    def test_items_key_on_non_dict(self) -> None:
        response = mock_response(json_data=[1, 2])
        self.assertEqual(IssueClientBase._json_items(response, items_key='values'), [])

    def test_items_key_value_not_list(self) -> None:
        response = mock_response(json_data={'values': 'nope'})
        self.assertEqual(IssueClientBase._json_items(response, items_key='values'), [])


class BestEffortResponseItemsTests(unittest.TestCase):
    def test_returns_items_on_success(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'values': [{'a': 1}]})
        with patch.object(client, '_get_with_retry', return_value=response):
            result = client._best_effort_response_items(
                'ISSUE-1', item_label='comments', path='/x', items_key='values',
            )
        self.assertEqual(result, [{'a': 1}])

    def test_returns_empty_and_logs_on_exception(self) -> None:
        client = _make_client()
        with patch.object(client, '_get_with_retry', side_effect=RuntimeError('net')), \
                patch.object(client.logger, 'exception') as mock_log:
            result = client._best_effort_response_items(
                'ISSUE-1', item_label='comments', path='/x',
            )
        self.assertEqual(result, [])
        mock_log.assert_called_once()


class SafeDictTests(unittest.TestCase):
    def test_returns_dict_value(self) -> None:
        self.assertEqual(
            IssueClientBase._safe_dict({'k': {'a': 1}}, 'k'), {'a': 1},
        )

    def test_returns_empty_for_non_dict(self) -> None:
        self.assertEqual(IssueClientBase._safe_dict({'k': 'str'}, 'k'), {})
        self.assertEqual(IssueClientBase._safe_dict({}, 'missing'), {})


# ---------------------------------------------------------------------------
# operational-comment hook default
# ---------------------------------------------------------------------------

class OperationalCommentHookTests(unittest.TestCase):
    def test_default_is_never_operational(self) -> None:
        client = _make_client()
        self.assertFalse(client._is_operational_comment('anything'))


# ---------------------------------------------------------------------------
# attachments
# ---------------------------------------------------------------------------

class MimeTypeTests(unittest.TestCase):
    def test_text_prefix(self) -> None:
        self.assertTrue(IssueClientBase._is_text_attachment_mime_type('text/plain'))

    def test_known_mime_types(self) -> None:
        for mime in _TEXT_ATTACHMENT_MIME_TYPES:
            self.assertTrue(IssueClientBase._is_text_attachment_mime_type(mime))

    def test_image_is_not_text(self) -> None:
        self.assertFalse(IssueClientBase._is_text_attachment_mime_type('image/png'))

    def test_blank_is_not_text(self) -> None:
        self.assertFalse(IssueClientBase._is_text_attachment_mime_type(''))
        self.assertFalse(IssueClientBase._is_text_attachment_mime_type(None))


class DownloadTextAttachmentTests(unittest.TestCase):
    def test_blank_url_returns_empty(self) -> None:
        client = _make_client()
        result = client._download_text_attachment(
            '', attachment_name='f.txt', max_chars=100,
        )
        self.assertEqual(result, '')

    def test_returns_truncated_text(self) -> None:
        client = _make_client()
        response = mock_response(text='x' * 500)
        with patch.object(client, '_get_attachment_with_retry', return_value=response):
            result = client._download_text_attachment(
                'https://e.com/f.txt', attachment_name='f.txt', max_chars=10,
            )
        self.assertEqual(result, 'x' * 10)

    def test_falls_back_to_raw_content(self) -> None:
        client = _make_client()
        response = mock_response(text='', content=b'raw')
        with patch.object(client, '_get_attachment_with_retry', return_value=response):
            result = client._download_text_attachment(
                'https://e.com/f.txt', attachment_name='f.txt', max_chars=100,
            )
        self.assertEqual(result, 'raw')

    def test_returns_empty_when_blank_raw(self) -> None:
        client = _make_client()
        response = mock_response(text='', content=b'')
        with patch.object(client, '_get_attachment_with_retry', return_value=response):
            result = client._download_text_attachment(
                'https://e.com/f.txt', attachment_name='f.txt', max_chars=100,
            )
        self.assertEqual(result, '')

    def test_returns_none_and_logs_on_exception(self) -> None:
        client = _make_client()
        with patch.object(
            client, '_get_attachment_with_retry', side_effect=RuntimeError('boom'),
        ), patch.object(client.logger, 'exception') as mock_log:
            result = client._download_text_attachment(
                'https://e.com/f.txt',
                attachment_name='f.txt',
                max_chars=100,
                log_label='jira text attachment',
            )
        self.assertIsNone(result)
        mock_log.assert_called_once_with(
            'failed to read %s %s', 'jira text attachment', 'f.txt',
        )


class GetAttachmentWithRetryTests(unittest.TestCase):
    def test_uses_session_for_absolute_urls(self) -> None:
        client = _make_client()
        response = mock_response(text='ok')
        with patch.object(client.session, 'get', return_value=response) as mock_get:
            client._get_attachment_with_retry('https://example.com/f.txt')
        mock_get.assert_called_once()

    def test_uses_get_for_relative_paths(self) -> None:
        client = _make_client()
        response = mock_response(text='ok')
        with patch.object(client, '_get', return_value=response) as mock_get:
            client._get_attachment_with_retry('/relative/path')
        mock_get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
