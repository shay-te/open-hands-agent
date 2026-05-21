"""End-to-end flow tests for YouTrackClient — A-Z scenarios.

Each test class represents one named flow and exercises the full call chain
from a public method call down through mocked HTTP responses back to the
structured result.  No internal methods are patched; only the lowest-level
`_get` / `_post` / `_delete` on the underlying session is intercepted so the
full parsing, retry, and assembly logic runs.
"""
from __future__ import annotations

import unittest
from unittest.mock import call, patch

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client import YouTrackClient
from youtrack_core_lib.youtrack_core_lib.client.youtrack_client_base import (
    UNTRUSTED_ISSUE_COMMENTS_SECTION_TITLE,
    UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE,
    UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE,
)
from youtrack_core_lib.youtrack_core_lib.data.fields import TaskCommentFields
from youtrack_core_lib.youtrack_core_lib.tests.utils import mock_response

BASE_URL = 'https://youtrack.example'
TOKEN = 'secret'
TIMEOUT = 30

_OP_PREFIXES = ('Agent note:',)


def _client(**kwargs):
    return YouTrackClient(BASE_URL, TOKEN, max_retries=1, **kwargs)


# ---------------------------------------------------------------------------
# F1 — Happy-path: fetch assigned tasks with comments and tags
# ---------------------------------------------------------------------------
class F1_GetAssignedTasksWithCommentsAndTags(unittest.TestCase):
    """Full flow: list → per-issue tags + comments + attachments → Task objects."""

    def test_flow(self):
        client = _client()

        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 'fix it already', 'description': 'Details'},
            {'idReadable': 'PROJ-2', 'summary': 'Add feature', 'description': 'Spec'},
        ])
        tags_resp_1 = mock_response(json_data=[{'name': 'backend'}])
        tags_resp_2 = mock_response(json_data=[{'name': 'frontend'}])
        comments_resp = mock_response(json_data=[
            {'id': 'c1', 'text': 'looks good', 'author': {'login': 'alice', 'name': 'Alice'}},
        ])
        empty_resp = mock_response(json_data=[])

        def side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if path == '/api/issues/PROJ-1/tags':
                return tags_resp_1
            if path == '/api/issues/PROJ-2/tags':
                return tags_resp_2
            if 'comments' in path:
                return comments_resp
            return empty_resp

        with patch.object(client, '_get', side_effect=side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Todo', 'Open'])

        self.assertEqual(len(tasks), 2)
        t1, t2 = tasks
        self.assertEqual(t1.id, 'PROJ-1')
        self.assertEqual(t1.tags, ['backend'])
        self.assertIn('Alice', t1.description)
        self.assertIn('looks good', t1.description)
        self.assertEqual(t2.id, 'PROJ-2')
        self.assertEqual(t2.tags, ['frontend'])

    def test_all_comments_stored_on_task(self):
        client = _client()

        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'd'},
        ])
        comments_resp = mock_response(json_data=[
            {'id': 'c1', 'text': 'note', 'author': {'login': 'bob', 'name': 'Bob'}},
        ])
        empty_resp = mock_response(json_data=[])

        def side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'comments' in path:
                return comments_resp
            return empty_resp

        with patch.object(client, '_get', side_effect=side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        all_comments = getattr(tasks[0], TaskCommentFields.ALL_COMMENTS)
        self.assertEqual(len(all_comments), 1)
        self.assertEqual(all_comments[0]['body'], 'note')
        self.assertEqual(all_comments[0]['author'], 'Bob')


# ---------------------------------------------------------------------------
# F2 — Empty task list
# ---------------------------------------------------------------------------
class F2_GetAssignedTasksEmpty(unittest.TestCase):
    """Flow: no open tasks returns empty list."""

    def test_flow(self):
        client = _client()
        empty = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=empty):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Todo'])

        self.assertEqual(tasks, [])


# ---------------------------------------------------------------------------
# F3 — Operational comment filtering
# ---------------------------------------------------------------------------
class F3_OperationalCommentFiltering(unittest.TestCase):
    """Flow: agent-posted comments excluded from description but kept in all_comments."""

    def test_operational_comment_excluded_from_description_included_in_all_comments(self):
        client = _client(operational_comment_prefixes=_OP_PREFIXES)

        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'd'},
        ])
        comments_resp = mock_response(json_data=[
            {'id': 'c1', 'text': 'Agent note: status update', 'author': {'login': 'bot', 'name': 'Bot'}},
            {'id': 'c2', 'text': 'Real user comment', 'author': {'login': 'alice', 'name': 'Alice'}},
        ])
        empty_resp = mock_response(json_data=[])

        def side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'comments' in path:
                return comments_resp
            return empty_resp

        with patch.object(client, '_get', side_effect=side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        task = tasks[0]
        self.assertNotIn('Agent note:', task.description)
        self.assertIn('Real user comment', task.description)
        all_comments = getattr(task, TaskCommentFields.ALL_COMMENTS)
        bodies = [c['body'] for c in all_comments]
        self.assertIn('Agent note: status update', bodies)
        self.assertIn('Real user comment', bodies)

    def test_no_prefix_config_includes_all_in_description(self):
        client = _client()

        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'd'},
        ])
        comments_resp = mock_response(json_data=[
            {'id': 'c1', 'text': 'Agent note: status update', 'author': {'login': 'bot', 'name': 'Bot'}},
        ])
        empty_resp = mock_response(json_data=[])

        def side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'comments' in path:
                return comments_resp
            return empty_resp

        with patch.object(client, '_get', side_effect=side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertIn('Agent note:', tasks[0].description)


# ---------------------------------------------------------------------------
# F4 — Add pull-request comment
# ---------------------------------------------------------------------------
class F4_AddPullRequestComment(unittest.TestCase):
    """Flow: add_pull_request_comment posts a comment with the PR URL embedded."""

    def test_flow(self):
        client = _client()
        ok = mock_response(json_data={})
        pr_url = 'https://bitbucket.example/pr/42'

        with patch.object(client, '_post', return_value=ok) as mock_post:
            client.add_pull_request_comment('PROJ-7', pr_url)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        posted_json = call_kwargs[1].get('json') or call_kwargs[0][1]
        self.assertIn(pr_url, posted_json.get('text', ''))

    def test_raises_on_http_error(self):
        client = _client()
        err_resp = mock_response(status_code=403)
        err_resp.raise_for_status.side_effect = Exception('forbidden')

        with patch.object(client, '_post', return_value=err_resp):
            with self.assertRaises(Exception):
                client.add_pull_request_comment('PROJ-7', 'https://bitbucket/pr/1')


# ---------------------------------------------------------------------------
# F5 — Add tag
# ---------------------------------------------------------------------------
class F5_AddTag(unittest.TestCase):
    """Flow: add_tag POSTs to the tags endpoint with the tag name."""

    def test_flow(self):
        client = _client()
        ok = mock_response(json_data={})

        with patch.object(client, '_post', return_value=ok) as mock_post:
            client.add_tag('PROJ-1', 'urgent')

        mock_post.assert_called_once()
        path = mock_post.call_args[0][0]
        self.assertIn('PROJ-1', path)
        self.assertIn('tags', path)
        sent_name = mock_post.call_args[1].get('json', {}).get('name')
        self.assertEqual(sent_name, 'urgent')

    def test_raises_on_http_error(self):
        client = _client()
        err = mock_response(status_code=500)
        err.raise_for_status.side_effect = Exception('server error')

        with patch.object(client, '_post', return_value=err):
            with self.assertRaises(Exception):
                client.add_tag('PROJ-1', 'urgent')


# ---------------------------------------------------------------------------
# F6 — Remove tag (tag present)
# ---------------------------------------------------------------------------
class F6_RemoveTag_Present(unittest.TestCase):
    """Flow: remove_tag looks up id then DELETEs the tag."""

    def test_flow(self):
        client = _client()
        tags_resp = mock_response(json_data=[{'id': 'tag-99', 'name': 'urgent'}])
        delete_resp = mock_response(json_data={})

        with patch.object(client, '_get', return_value=tags_resp):
            with patch.object(client, '_delete', return_value=delete_resp) as mock_del:
                client.remove_tag('PROJ-1', 'urgent')

        mock_del.assert_called_once()
        delete_path = mock_del.call_args[0][0]
        self.assertIn('tag-99', delete_path)


# ---------------------------------------------------------------------------
# F7 — Remove tag (tag absent — no-op)
# ---------------------------------------------------------------------------
class F7_RemoveTag_Absent(unittest.TestCase):
    """Flow: remove_tag does nothing when the tag is not on the issue."""

    def test_flow(self):
        client = _client()
        tags_resp = mock_response(json_data=[{'id': 'tag-1', 'name': 'other'}])

        with patch.object(client, '_get', return_value=tags_resp):
            with patch.object(client, '_delete') as mock_del:
                client.remove_tag('PROJ-1', 'urgent')

        mock_del.assert_not_called()


# ---------------------------------------------------------------------------
# F8 — Move issue (value field, state change needed)
# ---------------------------------------------------------------------------
class F8_MoveIssueValueField(unittest.TestCase):
    """Flow: move_issue_to_state updates a non-state-machine field."""

    def _fields_resp(self, current_state='Open'):
        return mock_response(json_data=[
            {
                'id': 'f-1',
                'name': 'State',
                '$type': 'SingleEnumIssueCustomField',
                'value': {'id': 'v1', 'name': current_state},
                'possibleEvents': [],
            }
        ])

    def test_flow(self):
        client = _client()
        fields_resp = self._fields_resp('Open')
        post_resp = mock_response(json_data={
            'id': 'f-1',
            'name': 'State',
            '$type': 'SingleEnumIssueCustomField',
            'value': {'id': 'v2', 'name': 'In Review'},
            'possibleEvents': [],
        })

        get_calls = iter([fields_resp])

        with patch.object(client, '_get', side_effect=lambda *a, **kw: next(get_calls)):
            with patch.object(client, '_post', return_value=post_resp):
                client.move_issue_to_state('PROJ-1', 'State', 'In Review')

    def test_noop_when_already_in_target_state(self):
        client = _client()
        fields_resp = self._fields_resp('In Review')

        with patch.object(client, '_get', return_value=fields_resp):
            with patch.object(client, '_post') as mock_post:
                client.move_issue_to_state('PROJ-1', 'State', 'In Review')

        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# F9 — Move issue (state-machine field)
# ---------------------------------------------------------------------------
class F9_MoveIssueStateMachineField(unittest.TestCase):
    """Flow: move_issue_to_state triggers the matching state-machine event."""

    def _fields_resp(self):
        return mock_response(json_data=[
            {
                'id': 'f-sm',
                'name': 'State',
                '$type': 'StateMachineIssueCustomField',
                'value': {'id': 'v-open', 'name': 'Open'},
                'possibleEvents': [
                    {'id': 'InReview', 'presentation': 'In Review', '$type': 'Event'},
                    {'id': 'Done', 'presentation': 'Done', '$type': 'Event'},
                ],
            }
        ])

    def test_flow(self):
        client = _client()
        fields_resp = self._fields_resp()
        post_resp = mock_response(json_data={
            'id': 'f-sm',
            'name': 'State',
            '$type': 'StateMachineIssueCustomField',
            'value': {'id': 'v-ir', 'name': 'In Review'},
            'possibleEvents': [],
        })

        with patch.object(client, '_get', return_value=fields_resp):
            with patch.object(client, '_post', return_value=post_resp) as mock_post:
                client.move_issue_to_state('PROJ-1', 'State', 'In Review')

        mock_post.assert_called_once()
        posted = mock_post.call_args[1].get('json', {})
        self.assertIn('event', posted)
        self.assertEqual(posted['event']['id'], 'InReview')

    def test_raises_when_event_not_found(self):
        client = _client()
        fields_resp = self._fields_resp()
        with patch.object(client, '_get', return_value=fields_resp):
            with self.assertRaises(ValueError):
                client.move_issue_to_state('PROJ-1', 'State', 'Nonexistent State')


# ---------------------------------------------------------------------------
# F10 — Text attachment included in description
# ---------------------------------------------------------------------------
class F10_TextAttachmentInDescription(unittest.TestCase):
    """Flow: a text/plain attachment is downloaded and appended to description."""

    def test_flow(self):
        client = _client()

        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'base desc'},
        ])
        attachments_resp = mock_response(json_data=[
            {
                'id': 'att-1',
                'name': 'notes.txt',
                'mimeType': 'text/plain',
                'charset': 'utf-8',
                'metaData': None,
                'url': 'https://youtrack.example/attachments/notes.txt',
            }
        ])
        attachment_content_resp = mock_response(text='Important text content')
        comments_resp = mock_response(json_data=[])
        tags_resp = mock_response(json_data=[])

        def get_side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'attachments' in path and path.endswith('attachments'):
                return attachments_resp
            if 'comments' in path:
                return comments_resp
            if 'tags' in path:
                return tags_resp
            return attachment_content_resp

        with patch.object(client, '_get', side_effect=get_side_effect):
            with patch.object(client.session, 'get', return_value=attachment_content_resp):
                tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        task = tasks[0]
        self.assertIn('base desc', task.description)
        self.assertIn(UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE, task.description)


# ---------------------------------------------------------------------------
# F11 — Screenshot attachment in description
# ---------------------------------------------------------------------------
class F11_ScreenshotAttachmentInDescription(unittest.TestCase):
    """Flow: image/* attachments are listed in the screenshot section."""

    def test_flow(self):
        client = _client()

        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'd'},
        ])
        attachments_resp = mock_response(json_data=[
            {
                'id': 'att-2',
                'name': 'screen.png',
                'mimeType': 'image/png',
                'charset': None,
                'metaData': 'screenshot',
                'url': 'https://youtrack.example/attachments/screen.png',
            }
        ])
        empty_resp = mock_response(json_data=[])

        def get_side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'attachments' in path and path.endswith('attachments'):
                return attachments_resp
            return empty_resp

        with patch.object(client, '_get', side_effect=get_side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        task = tasks[0]
        self.assertIn(UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE, task.description)
        self.assertIn('screen.png', task.description)


# ---------------------------------------------------------------------------
# F12 — Validate connection succeeds
# ---------------------------------------------------------------------------
class F12_ValidateConnection(unittest.TestCase):
    """Flow: validate_connection makes exactly one GET and raises on error."""

    def test_success_does_not_raise(self):
        client = _client()
        ok = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=ok):
            client.validate_connection('PROJ', 'me', ['Open'])

        ok.raise_for_status.assert_called_once()

    def test_raises_on_http_error(self):
        client = _client()
        err = mock_response(status_code=401)
        err.raise_for_status.side_effect = Exception('unauthorized')

        with patch.object(client, '_get', return_value=err):
            with self.assertRaises(Exception):
                client.validate_connection('PROJ', 'me', ['Open'])


# ---------------------------------------------------------------------------
# F13 — Comments fetch fails gracefully (best-effort)
# ---------------------------------------------------------------------------
class F13_CommentsFetchFailsGracefully(unittest.TestCase):
    """Flow: if comments endpoint errors the task still returns with empty comments."""

    def test_flow(self):
        from youtrack_core_lib.youtrack_core_lib.tests.utils import ClientTimeout

        client = _client()
        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'd'},
        ])
        empty_resp = mock_response(json_data=[])

        call_count = {'n': 0}

        def get_side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'comments' in path:
                raise ClientTimeout('comments down')
            return empty_resp

        with patch.object(client, '_get', side_effect=get_side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, 'PROJ-1')

    def test_attachments_fetch_fails_gracefully(self):
        from youtrack_core_lib.youtrack_core_lib.tests.utils import ClientTimeout

        client = _client()
        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-1', 'summary': 's', 'description': 'd'},
        ])
        empty_resp = mock_response(json_data=[])

        def get_side_effect(path, **kwargs):
            if path == '/api/issues' and 'query' in kwargs.get('params', {}):
                return list_resp
            if 'attachments' in path:
                raise ClientTimeout('attachments down')
            return empty_resp

        with patch.object(client, '_get', side_effect=get_side_effect):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(len(tasks), 1)


# ---------------------------------------------------------------------------
# F14 — Branch name derived from issue id when not set
# ---------------------------------------------------------------------------
class F14_BranchNameDerived(unittest.TestCase):
    """Flow: task branch_name defaults to feature/<lowercased-id>."""

    def test_branch_name_derived_from_id(self):
        client = _client()
        list_resp = mock_response(json_data=[
            {'idReadable': 'PROJ-99', 'summary': 's', 'description': 'd'},
        ])
        empty_resp = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=lambda p, **kw: list_resp if 'query' in kw.get('params', {}) else empty_resp):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(tasks[0].branch_name, 'feature/proj-99')


# ---------------------------------------------------------------------------
# F15 — Multiple issues returned
# ---------------------------------------------------------------------------
class F15_MultipleIssues(unittest.TestCase):
    """Flow: multiple items in the list response produce one Task each."""

    def test_three_issues_returned(self):
        client = _client()
        list_resp = mock_response(json_data=[
            {'idReadable': f'PROJ-{i}', 'summary': f'Task {i}', 'description': ''}
            for i in range(1, 4)
        ])
        empty_resp = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=lambda p, **kw: list_resp if 'query' in kw.get('params', {}) else empty_resp):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(len(tasks), 3)
        self.assertEqual([t.id for t in tasks], ['PROJ-1', 'PROJ-2', 'PROJ-3'])


if __name__ == '__main__':
    unittest.main()
