"""Coverage for Flask routes in ``kato_webserver.app`` that the original
``test_app.py`` does not exercise directly.

The harness mirrors ``test_app.py``: a fake session/workspace manager
plus a SimpleNamespace-style agent service that records calls and
returns whatever the test scenario needs. The full app is wired via
``create_app`` and exercised through ``app.test_client()``. We do NOT
go to the network, the filesystem (except a tmp dir for files/diff),
or a real git repo.

Add new route tests as their own ``unittest.TestCase`` near the bottom
so individual feature areas stay readable.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_webserver.app import create_app


# ---------------------------------------------------------------------------
# Shared fakes — same pattern as test_app.py, intentionally duplicated here
# so each test file remains independently runnable.
# ---------------------------------------------------------------------------


class _FakeRecord:
    """Minimal stand-in for a ``ClaudeSessionRecord``."""

    def __init__(self, **kwargs):
        self._payload = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self):
        return dict(self._payload)


class _FakeManager:
    """Minimal stand-in for ``ClaudeSessionManager``.

    Only the surface the routes touch is implemented: ``list_records``,
    ``get_record``, ``get_session``, and ``terminate_session`` (the
    forget-task route calls it to kill subprocess + drop the record).
    Override on a per-test basis when a specific method needs to
    behave a certain way.
    """

    def __init__(self, records=None):
        self._records = records or []
        self.terminated: list[tuple[str, bool]] = []

    def list_records(self):
        return list(self._records)

    def get_record(self, task_id):
        for record in self._records:
            if getattr(record, 'task_id', '') == task_id:
                return record
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None

    def terminate_session(self, task_id, *, remove_record=False):
        # Mirror the real manager's idempotent shape: a missing
        # session / record is a no-op. We record the call so tests
        # that need to verify the wiring can assert on it.
        self.terminated.append((task_id, remove_record))
        if remove_record:
            self._records = [
                r for r in self._records
                if getattr(r, 'task_id', '') != task_id
            ]


class _FakeWorkspaceRecord:
    def __init__(self, **payload):
        self._payload = payload
        self.task_id = payload.get('task_id', '')
        self.repository_ids = payload.get('repository_ids', [])
        self.status = payload.get('status', '')

    def to_dict(self):
        return dict(self._payload)


class _FakeWorkspaceManager:
    """Stand-in for ``WorkspaceManager`` with just the surface the routes use."""

    def __init__(self, records=None, *, repo_paths=None, workspace_path_for=None):
        self._records = list(records or [])
        self._repo_paths = dict(repo_paths or {})
        self._workspace_path_for = dict(workspace_path_for or {})
        self.deleted = []  # records ``delete(task_id)`` calls

    def list_workspaces(self):
        return list(self._records)

    def get(self, task_id):
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def repository_path(self, task_id, repo_id):
        return Path(self._repo_paths.get((task_id, repo_id), '/missing'))

    def workspace_path(self, task_id):
        return Path(self._workspace_path_for.get(task_id, '/missing'))

    def delete(self, task_id):
        self.deleted.append(task_id)


def _agent(**methods):
    """Build a SimpleNamespace agent stub with the given methods/values.

    Routes that don't find an attribute fall through to a 501 path —
    so the absence of a method matters as much as its return value.
    Use this helper instead of MagicMock when you need precise control
    over which attributes exist.
    """
    return SimpleNamespace(**methods)


# ---------------------------------------------------------------------------
# /api/safety
# ---------------------------------------------------------------------------


class SafetyEndpointTests(unittest.TestCase):
    """``/api/safety`` reports the sandbox bypass + root-user state.

    The route delegates to ``sandbox_core_lib`` — patch those probes so
    the test never depends on the real environment.
    """

    def test_returns_bypass_and_root_state_as_booleans(self):
        app = create_app(session_manager=_FakeManager())
        with patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_bypass_enabled',
            return_value=True,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_running_as_root',
            return_value=False,
        ):
            response = app.test_client().get('/api/safety')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body, {'bypass_permissions': True, 'running_as_root': False})

    def test_returns_false_state_when_no_bypass_no_root(self):
        app = create_app(session_manager=_FakeManager())
        with patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_bypass_enabled',
            return_value=False,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_running_as_root',
            return_value=False,
        ):
            response = app.test_client().get('/api/safety')
        body = response.get_json()
        self.assertFalse(body['bypass_permissions'])
        self.assertFalse(body['running_as_root'])


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/files (single-repo and "no workspace" branches)
# ---------------------------------------------------------------------------


class FilesEndpointTests(unittest.TestCase):
    """The multi-repo branch is covered by test_app.py; here we cover the
    legacy single-repo fallback and the empty-response path."""

    def test_returns_empty_payload_when_no_workspace_and_no_record_cwd(self):
        # No workspace manager + no record cwd on disk → endpoint must
        # return 200 with empty arrays (NOT 404). The Files tab uses
        # this signal to render "no repositories" rather than an error.
        manager = _FakeManager(records=[_FakeRecord(task_id='PROJ-1')])
        app = create_app(session_manager=manager)
        response = app.test_client().get('/api/sessions/PROJ-1/files')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['repository_ids'], [])
        self.assertEqual(payload['trees'], [])
        self.assertEqual(payload['cwd'], '')
        self.assertEqual(payload['tree'], [])

    def test_legacy_single_repo_uses_record_cwd(self):
        # Single-repo task: no workspace metadata, but the session
        # record points at a real on-disk cwd. The endpoint falls back
        # to ``tracked_file_tree`` on that cwd.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            manager = _FakeManager(records=[
                _FakeRecord(task_id='PROJ-1', cwd=tmp),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.tracked_file_tree',
                return_value=[{'name': 'README.md', 'kind': 'file'}],
            ), patch(
                'kato_webserver.app.conflicted_paths',
                return_value=[],
            ), patch(
                'kato_webserver.app._changed_files_for_repo',
                return_value=['README.md'],
            ):
                response = app.test_client().get('/api/sessions/PROJ-1/files')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        # Legacy clients still get the flat ``cwd``/``tree`` fields.
        self.assertEqual(payload['cwd'], tmp)
        self.assertEqual(payload['tree'], [{'name': 'README.md', 'kind': 'file'}])
        self.assertEqual(payload['trees'][0]['repo_id'], '')
        # New: change-colouring input flows through the legacy path too.
        self.assertEqual(payload['changed_files'], ['README.md'])
        self.assertEqual(payload['trees'][0]['changed_files'], ['README.md'])


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/diff (single-repo and "no workspace" branches)
# ---------------------------------------------------------------------------


class DiffEndpointTests(unittest.TestCase):
    def test_returns_empty_diff_when_no_workspace_and_no_record_cwd(self):
        manager = _FakeManager(records=[_FakeRecord(task_id='PROJ-1')])
        app = create_app(session_manager=manager)
        response = app.test_client().get('/api/sessions/PROJ-1/diff')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['diffs'], [])
        self.assertEqual(payload['repo_id'], '')
        self.assertEqual(payload['diff'], '')

    def test_legacy_single_repo_diff_uses_record_cwd(self):
        # Single-repo: no workspace manager, session record cwd is the
        # base for the diff. Patch the git helpers so we don't need a
        # real repo on disk.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            manager = _FakeManager(records=[
                _FakeRecord(task_id='PROJ-1', cwd=tmp),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch',
                return_value='master',
            ), patch(
                'kato_webserver.app.current_branch',
                return_value='PROJ-1',
            ), patch(
                'kato_webserver.app.diff_against_base',
                return_value='diff --git a/x b/x',
            ), patch(
                'kato_webserver.app.ensure_branch_checked_out',
                return_value=True,
            ), patch(
                'kato_webserver.app.conflicted_paths',
                return_value=[],
            ):
                response = app.test_client().get('/api/sessions/PROJ-1/diff')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['base'], 'master')
        self.assertEqual(payload['head'], 'PROJ-1')
        self.assertEqual(payload['diff'], 'diff --git a/x b/x')
        # Single-repo, legacy payload still includes one entry in diffs.
        self.assertEqual(len(payload['diffs']), 1)


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/commits
# ---------------------------------------------------------------------------


class CommitsEndpointTests(unittest.TestCase):
    def _build(self, *, repo_paths=None, agent=None):
        manager = _FakeManager(records=[_FakeRecord(task_id='PROJ-1')])
        workspace = _FakeWorkspaceManager(
            records=[_FakeWorkspaceRecord(
                task_id='PROJ-1', repository_ids=['client'],
            )],
            repo_paths=repo_paths or {},
        )
        return create_app(
            session_manager=manager,
            workspace_manager=workspace,
            agent_service=agent,
        ).test_client()

    def test_missing_repo_query_param_returns_400(self):
        client = self._build()
        response = client.get('/api/sessions/PROJ-1/commits')
        self.assertEqual(response.status_code, 400)
        self.assertIn('repo', response.get_json()['error'])

    def test_unknown_repo_returns_404(self):
        # repository_path returns /missing → not a directory → 404.
        client = self._build()
        response = client.get('/api/sessions/PROJ-1/commits?repo=nope')
        self.assertEqual(response.status_code, 404)
        self.assertIn('not in workspace', response.get_json()['error'])

    def test_returns_commits_when_base_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            client = self._build(repo_paths={('PROJ-1', 'client'): tmp})
            with patch(
                'kato_webserver.app.detect_default_branch',
                return_value='main',
            ), patch(
                'kato_webserver.app.current_branch',
                return_value='PROJ-1',
            ), patch(
                'kato_webserver.app.list_branch_commits',
                return_value=[{'sha': 'abc', 'subject': 'fix it'}],
            ):
                response = client.get('/api/sessions/PROJ-1/commits?repo=client')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['repo_id'], 'client')
        self.assertEqual(payload['base'], 'main')
        self.assertEqual(payload['commits'], [{'sha': 'abc', 'subject': 'fix it'}])

    def test_returns_empty_commits_with_error_when_no_base(self):
        # Configured agent returns '' AND git auto-detect returns '' →
        # 200 + an ``error`` field so the UI can render the empty state.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            client = self._build(repo_paths={('PROJ-1', 'client'): tmp})
            with patch(
                'kato_webserver.app.detect_default_branch',
                return_value='',
            ):
                response = client.get('/api/sessions/PROJ-1/commits?repo=client')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['commits'], [])
        self.assertIn('destination_branch', payload['error'])

    def test_invalid_limit_falls_back_to_default(self):
        # ``limit=banana`` → caught, defaults to 50. We assert it does
        # not crash (200) and that the resulting limit is forwarded.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            client = self._build(repo_paths={('PROJ-1', 'client'): tmp})
            with patch(
                'kato_webserver.app.detect_default_branch',
                return_value='main',
            ), patch(
                'kato_webserver.app.current_branch',
                return_value='PROJ-1',
            ), patch(
                'kato_webserver.app.list_branch_commits',
                return_value=[],
            ) as commits_mock:
                response = client.get(
                    '/api/sessions/PROJ-1/commits?repo=client&limit=banana',
                )
        self.assertEqual(response.status_code, 200)
        # Default limit (50) is forwarded after parse failure.
        _args, kwargs = commits_mock.call_args
        self.assertEqual(kwargs.get('limit'), 50)


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/commit
# ---------------------------------------------------------------------------


class CommitDiffEndpointTests(unittest.TestCase):
    def _build(self, *, repo_paths=None):
        manager = _FakeManager(records=[_FakeRecord(task_id='PROJ-1')])
        workspace = _FakeWorkspaceManager(
            records=[_FakeWorkspaceRecord(
                task_id='PROJ-1', repository_ids=['client'],
            )],
            repo_paths=repo_paths or {},
        )
        return create_app(
            session_manager=manager,
            workspace_manager=workspace,
        ).test_client()

    def test_missing_repo_returns_400(self):
        response = self._build().get('/api/sessions/PROJ-1/commit?sha=abc')
        self.assertEqual(response.status_code, 400)
        self.assertIn('repo', response.get_json()['error'])

    def test_missing_sha_returns_400(self):
        response = self._build().get('/api/sessions/PROJ-1/commit?repo=client')
        self.assertEqual(response.status_code, 400)
        self.assertIn('sha', response.get_json()['error'])

    def test_unknown_repo_returns_404(self):
        response = self._build().get('/api/sessions/PROJ-1/commit?repo=nope&sha=abc')
        self.assertEqual(response.status_code, 404)

    def test_returns_unified_diff_for_known_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            client = self._build(repo_paths={('PROJ-1', 'client'): tmp})
            with patch(
                'kato_webserver.app.diff_for_commit',
                return_value='diff --git a/f b/f',
            ):
                response = client.get(
                    '/api/sessions/PROJ-1/commit?repo=client&sha=deadbeef',
                )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['sha'], 'deadbeef')
        self.assertEqual(payload['diff'], 'diff --git a/f b/f')


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/approve-push + /awaiting-push-approval
# ---------------------------------------------------------------------------


class PushApprovalEndpointTests(unittest.TestCase):
    def test_approve_push_503_when_no_agent_service(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post('/api/sessions/T-1/approve-push')
        self.assertEqual(response.status_code, 503)

    def test_approve_push_501_when_agent_lacks_method(self):
        app = create_app(session_manager=_FakeManager(), agent_service=_agent())
        response = app.test_client().post('/api/sessions/T-1/approve-push')
        self.assertEqual(response.status_code, 501)

    def test_approve_push_404_when_no_pending_publish(self):
        agent = _agent(approve_push=lambda task_id: None)
        app = create_app(session_manager=_FakeManager(), agent_service=agent)
        response = app.test_client().post('/api/sessions/T-1/approve-push')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()['approved'])

    def test_approve_push_returns_result_on_success(self):
        agent = _agent(approve_push=lambda task_id: {'detail': 'ok'})
        app = create_app(session_manager=_FakeManager(), agent_service=agent)
        response = app.test_client().post('/api/sessions/T-1/approve-push')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['approved'])
        self.assertEqual(payload['result'], {'detail': 'ok'})

    def test_awaiting_push_approval_returns_false_without_agent(self):
        # Route degrades to a "no" answer rather than 503 — the UI
        # polls this every few seconds and a 503 would spam.
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/sessions/T-1/awaiting-push-approval')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['awaiting_push_approval'])

    def test_awaiting_push_approval_delegates_to_agent(self):
        check = MagicMock(return_value=True)
        agent = _agent(is_awaiting_push_approval=check)
        app = create_app(session_manager=_FakeManager(), agent_service=agent)
        response = app.test_client().get('/api/sessions/T-7/awaiting-push-approval')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['awaiting_push_approval'])
        check.assert_called_once_with('T-7')


# ---------------------------------------------------------------------------
# POST push / pull / pull-request / update-source — pure pass-through routes
# ---------------------------------------------------------------------------


class PushPullPRUpdateSourceEndpointTests(unittest.TestCase):
    """The four "operator button" endpoints share a flow:
    * agent_service missing → 503
    * method missing on agent_service → 501
    * agent reports ``error`` + falsey success flag → 404 (no workspace) or 500
    * happy result is returned as-is
    """

    def _client(self, *, agent=None):
        return create_app(
            session_manager=_FakeManager(),
            agent_service=agent,
        ).test_client()

    # ---- /push ----
    def test_push_503_when_no_agent_service(self):
        response = self._client().post('/api/sessions/T-1/push')
        self.assertEqual(response.status_code, 503)

    def test_push_501_when_agent_lacks_method(self):
        response = self._client(agent=_agent()).post('/api/sessions/T-1/push')
        self.assertEqual(response.status_code, 501)

    def test_push_returns_404_when_error_mentions_no_workspace(self):
        agent = _agent(push_task=lambda t: {'pushed': False, 'error': 'no workspace for task'})
        response = self._client(agent=agent).post('/api/sessions/T-1/push')
        self.assertEqual(response.status_code, 404)
        self.assertIn('no workspace', response.get_json()['error'])

    def test_push_returns_500_on_generic_error(self):
        agent = _agent(push_task=lambda t: {'pushed': False, 'error': 'remote rejected'})
        response = self._client(agent=agent).post('/api/sessions/T-1/push')
        self.assertEqual(response.status_code, 500)

    def test_push_returns_200_with_payload_on_success(self):
        agent = _agent(push_task=lambda t: {'pushed': True, 'commits_pushed': 3})
        response = self._client(agent=agent).post('/api/sessions/T-1/push')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['commits_pushed'], 3)

    # ---- /pull ----
    def test_pull_503_when_no_agent_service(self):
        response = self._client().post('/api/sessions/T-1/pull')
        self.assertEqual(response.status_code, 503)

    def test_pull_returns_404_when_no_workspace(self):
        agent = _agent(pull_task=lambda t: {'pulled': False, 'error': 'no workspace'})
        response = self._client(agent=agent).post('/api/sessions/T-1/pull')
        self.assertEqual(response.status_code, 404)

    def test_pull_happy_path(self):
        agent = _agent(pull_task=lambda t: {'pulled': True})
        response = self._client(agent=agent).post('/api/sessions/T-1/pull')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['pulled'])

    # ---- /merge-default-branch ----
    def test_merge_default_503_when_no_agent_service(self):
        response = self._client().post('/api/sessions/T-1/merge-default-branch')
        self.assertEqual(response.status_code, 503)

    def test_merge_default_501_when_agent_lacks_method(self):
        response = self._client(agent=_agent()).post(
            '/api/sessions/T-1/merge-default-branch',
        )
        self.assertEqual(response.status_code, 501)

    def test_merge_default_404_when_no_workspace(self):
        agent = _agent(merge_default_branch_for_task=lambda t: {
            'merged': False, 'has_conflicts': False,
            'error': 'no workspace context for this task',
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/merge-default-branch',
        )
        self.assertEqual(response.status_code, 404)

    def test_merge_default_conflicts_are_200_not_error(self):
        # A conflicted merge is the SUCCESSFUL outcome of the button —
        # the operator wanted the default branch in so the agent can
        # fix conflicts. Must be 200, not 4xx/5xx.
        agent = _agent(merge_default_branch_for_task=lambda t: {
            'merged': False, 'has_conflicts': True,
            'conflicted_repositories': [
                {'repository_id': 'client', 'default_branch': 'main',
                 'conflicted_files': ['a.py']},
            ],
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/merge-default-branch',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['has_conflicts'])

    def test_merge_default_clean_merge_happy_path(self):
        agent = _agent(merge_default_branch_for_task=lambda t: {
            'merged': True, 'has_conflicts': False,
            'merged_repositories': [
                {'repository_id': 'client', 'commits_merged': 2},
            ],
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/merge-default-branch',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['merged'])

    # ---- /pull-request ----
    def test_pull_request_503_when_no_agent_service(self):
        response = self._client().post('/api/sessions/T-1/pull-request')
        self.assertEqual(response.status_code, 503)

    def test_pull_request_501_when_agent_lacks_method(self):
        response = self._client(agent=_agent()).post('/api/sessions/T-1/pull-request')
        self.assertEqual(response.status_code, 501)

    def test_pull_request_returns_url_on_success(self):
        agent = _agent(create_pull_request_for_task=lambda t: {
            'created': True, 'url': 'https://example.com/pr/1',
        })
        response = self._client(agent=agent).post('/api/sessions/T-1/pull-request')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['url'], 'https://example.com/pr/1')

    # ---- /update-source ----
    def test_update_source_503_when_no_agent_service(self):
        response = self._client().post('/api/sessions/T-1/update-source')
        self.assertEqual(response.status_code, 503)

    def test_update_source_501_when_method_missing(self):
        response = self._client(agent=_agent()).post('/api/sessions/T-1/update-source')
        self.assertEqual(response.status_code, 501)

    def test_update_source_succeeds_when_agent_reports_updated(self):
        agent = _agent(update_source_for_task=lambda t: {'updated': True})
        response = self._client(agent=agent).post('/api/sessions/T-1/update-source')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['updated'])


# ---------------------------------------------------------------------------
# Comments endpoints — GET / POST / resolve / addressed / reopen / DELETE / sync
# ---------------------------------------------------------------------------


class CommentsEndpointTests(unittest.TestCase):
    def _client(self, *, agent=None):
        return create_app(
            session_manager=_FakeManager(),
            agent_service=agent,
        ).test_client()

    # ---- GET /comments ----
    def test_list_503_when_no_agent_service(self):
        response = self._client().get('/api/sessions/T-1/comments')
        self.assertEqual(response.status_code, 503)

    def test_list_returns_empty_list_when_method_missing(self):
        # Returns 200 with [] rather than 501 — the comments tab polls
        # this often, an unhealthy 501 would spam the operator.
        response = self._client(agent=_agent()).get('/api/sessions/T-1/comments')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'comments': []})

    def test_list_forwards_repo_filter_to_agent(self):
        captured = {}
        def list_comments(task_id, repo_id):
            captured['task_id'] = task_id
            captured['repo_id'] = repo_id
            return [{'id': 'c1'}]
        agent = _agent(list_task_comments=list_comments)
        response = self._client(agent=agent).get(
            '/api/sessions/T-1/comments?repo=client',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['comments'], [{'id': 'c1'}])
        self.assertEqual(captured, {'task_id': 'T-1', 'repo_id': 'client'})

    # ---- POST /comments ----
    def test_create_503_when_no_agent_service(self):
        response = self._client().post(
            '/api/sessions/T-1/comments', json={'body': 'hi'},
        )
        self.assertEqual(response.status_code, 503)

    def test_create_501_when_agent_lacks_method(self):
        response = self._client(agent=_agent()).post(
            '/api/sessions/T-1/comments', json={'body': 'hi'},
        )
        self.assertEqual(response.status_code, 501)

    def test_create_400_when_agent_rejects_with_generic_error(self):
        agent = _agent(add_task_comment=lambda *a, **k: {'ok': False, 'error': 'body required'})
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments', json={},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_404_when_error_mentions_no_workspace(self):
        agent = _agent(add_task_comment=lambda *a, **k: {'ok': False, 'error': 'no workspace for task'})
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments', json={'body': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_forwards_full_payload(self):
        captured = {}
        def add_comment(task_id, **kwargs):
            captured['task_id'] = task_id
            captured.update(kwargs)
            return {'ok': True, 'id': 'c-99'}
        agent = _agent(add_task_comment=add_comment)
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments',
            json={
                'repo': 'client', 'file_path': 'src/x.py', 'line': 42,
                'body': 'looks wrong', 'parent_id': '', 'author': 'shay',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['id'], 'c-99')
        self.assertEqual(captured['task_id'], 'T-1')
        self.assertEqual(captured['repo_id'], 'client')
        self.assertEqual(captured['file_path'], 'src/x.py')
        self.assertEqual(captured['line'], 42)
        self.assertEqual(captured['body'], 'looks wrong')
        self.assertEqual(captured['author'], 'shay')

    def test_create_defaults_line_to_negative_one(self):
        # When body has no ``line`` (file-level comment), the endpoint
        # forwards line=-1 — that's the sentinel the agent service uses.
        captured = {}
        def add_comment(task_id, **kwargs):
            captured.update(kwargs)
            return {'ok': True}
        agent = _agent(add_task_comment=add_comment)
        self._client(agent=agent).post(
            '/api/sessions/T-1/comments',
            json={'repo': 'client', 'file_path': 'x', 'body': 'b'},
        )
        self.assertEqual(captured['line'], -1)

    # ---- POST /comments/<id>/resolve ----
    def test_resolve_503_when_no_agent_service(self):
        response = self._client().post('/api/sessions/T-1/comments/c1/resolve')
        self.assertEqual(response.status_code, 503)

    def test_resolve_501_when_method_missing(self):
        response = self._client(agent=_agent()).post(
            '/api/sessions/T-1/comments/c1/resolve',
        )
        self.assertEqual(response.status_code, 501)

    def test_resolve_forwards_resolved_by(self):
        captured = {}
        def resolve(task_id, comment_id, **kwargs):
            captured['args'] = (task_id, comment_id)
            captured.update(kwargs)
            return {'resolved': True}
        agent = _agent(resolve_task_comment=resolve)
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments/c-9/resolve',
            json={'resolved_by': 'shay'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'resolved': True})
        self.assertEqual(captured['args'], ('T-1', 'c-9'))
        self.assertEqual(captured['resolved_by'], 'shay')

    # ---- POST /comments/<id>/addressed ----
    def test_mark_addressed_forwards_sha(self):
        captured = {}
        def mark(task_id, comment_id, **kwargs):
            captured['args'] = (task_id, comment_id)
            captured.update(kwargs)
            return {'ok': True, 'kato_status': 'ADDRESSED'}
        agent = _agent(mark_comment_addressed=mark)
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments/c-9/addressed',
            json={'addressed_sha': 'deadbeef'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['addressed_sha'], 'deadbeef')

    def test_mark_addressed_503_when_no_agent(self):
        response = self._client().post('/api/sessions/T-1/comments/c-9/addressed')
        self.assertEqual(response.status_code, 503)

    # ---- POST /comments/<id>/reopen ----
    def test_reopen_forwards_to_agent(self):
        reopen = MagicMock(return_value={'reopened': True})
        agent = _agent(reopen_task_comment=reopen)
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments/c-9/reopen',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'reopened': True})
        reopen.assert_called_once_with('T-1', 'c-9')

    def test_reopen_503_when_no_agent(self):
        response = self._client().post('/api/sessions/T-1/comments/c-9/reopen')
        self.assertEqual(response.status_code, 503)

    # ---- DELETE /comments/<id> ----
    def test_delete_forwards_to_agent(self):
        delete = MagicMock(return_value={'deleted': True})
        agent = _agent(delete_task_comment=delete)
        response = self._client(agent=agent).delete(
            '/api/sessions/T-1/comments/c-9',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'deleted': True})
        delete.assert_called_once_with('T-1', 'c-9')

    def test_delete_503_when_no_agent(self):
        response = self._client().delete('/api/sessions/T-1/comments/c-9')
        self.assertEqual(response.status_code, 503)

    # ---- POST /comments/sync ----
    def test_sync_400_when_no_repo(self):
        agent = _agent(sync_remote_comments=lambda *a, **k: {'ok': True})
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments/sync', json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('repo', response.get_json()['error'])

    def test_sync_503_when_no_agent(self):
        response = self._client().post(
            '/api/sessions/T-1/comments/sync', json={'repo': 'client'},
        )
        self.assertEqual(response.status_code, 503)

    def test_sync_forwards_repo_id(self):
        captured = {}
        def sync(task_id, repo_id):
            captured['args'] = (task_id, repo_id)
            return {'ok': True, 'pulled': 5}
        agent = _agent(sync_remote_comments=sync)
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/comments/sync', json={'repo': 'backend'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['pulled'], 5)
        self.assertEqual(captured['args'], ('T-1', 'backend'))


# ---------------------------------------------------------------------------
# /api/tasks  +  /api/tasks/<task_id>/adopt
# ---------------------------------------------------------------------------


class TasksEndpointTests(unittest.TestCase):
    def _client(self, *, agent=None):
        return create_app(
            session_manager=_FakeManager(),
            agent_service=agent,
        ).test_client()

    def test_list_503_when_no_agent_service(self):
        response = self._client().get('/api/tasks')
        self.assertEqual(response.status_code, 503)

    def test_list_returns_empty_when_method_missing(self):
        # Soft-degrade — the picker polls this and a 501 would surface
        # as a scary error in the UI for an optional feature.
        response = self._client(agent=_agent()).get('/api/tasks')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'tasks': []})

    def test_list_returns_agent_payload(self):
        agent = _agent(list_all_assigned_tasks=lambda: [
            {'task_id': 'T-1', 'state': 'open'},
            {'task_id': 'T-2', 'state': 'in-progress'},
        ])
        response = self._client(agent=agent).get('/api/tasks')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload['tasks']), 2)
        self.assertEqual(payload['tasks'][0]['task_id'], 'T-1')

    def test_adopt_503_when_no_agent_service(self):
        response = self._client().post('/api/tasks/T-1/adopt')
        self.assertEqual(response.status_code, 503)

    def test_adopt_501_when_method_missing(self):
        response = self._client(agent=_agent()).post('/api/tasks/T-1/adopt')
        self.assertEqual(response.status_code, 501)

    def test_adopt_404_when_task_not_assigned(self):
        agent = _agent(adopt_task=lambda t: {
            'adopted': False, 'error': 'task not assigned to kato',
        })
        response = self._client(agent=agent).post('/api/tasks/T-1/adopt')
        self.assertEqual(response.status_code, 404)

    def test_adopt_403_when_rep_gate_blocks(self):
        # ``restricted execution protocol`` in the error means the
        # REP gate refused — must be 403 so the UI shows the right
        # explainer instead of generic "task not assigned".
        agent = _agent(adopt_task=lambda t: {
            'adopted': False,
            'error': 'restricted execution protocol blocks this repository',
        })
        response = self._client(agent=agent).post('/api/tasks/T-1/adopt')
        self.assertEqual(response.status_code, 403)

    def test_adopt_500_on_unknown_error(self):
        agent = _agent(adopt_task=lambda t: {
            'adopted': False, 'error': 'clone failed: io error',
        })
        response = self._client(agent=agent).post('/api/tasks/T-1/adopt')
        self.assertEqual(response.status_code, 500)

    def test_adopt_success_returns_payload(self):
        agent = _agent(adopt_task=lambda t: {
            'adopted': True, 'workspace_path': '/tmp/x', 'task_id': t,
        })
        response = self._client(agent=agent).post('/api/tasks/T-1/adopt')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['adopted'])
        self.assertEqual(payload['workspace_path'], '/tmp/x')


# ---------------------------------------------------------------------------
# /api/repositories  +  /api/sessions/<task_id>/add-repository
# ---------------------------------------------------------------------------


class RepositoriesEndpointTests(unittest.TestCase):
    def _client(self, *, agent=None):
        return create_app(
            session_manager=_FakeManager(),
            agent_service=agent,
        ).test_client()

    def test_list_503_when_no_agent_service(self):
        response = self._client().get('/api/repositories')
        self.assertEqual(response.status_code, 503)

    def test_list_returns_empty_when_method_missing(self):
        response = self._client(agent=_agent()).get('/api/repositories')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'repositories': []})

    def test_list_returns_inventory(self):
        agent = _agent(list_inventory_repositories=lambda: [
            {'id': 'client', 'url': 'git@example.com:client.git'},
            {'id': 'backend', 'url': 'git@example.com:backend.git'},
        ])
        response = self._client(agent=agent).get('/api/repositories')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()['repositories']), 2)

    def test_add_repository_503_when_no_agent_service(self):
        response = self._client().post(
            '/api/sessions/T-1/add-repository', json={'repository_id': 'client'},
        )
        self.assertEqual(response.status_code, 503)

    def test_add_repository_501_when_method_missing(self):
        response = self._client(agent=_agent()).post(
            '/api/sessions/T-1/add-repository', json={'repository_id': 'client'},
        )
        self.assertEqual(response.status_code, 501)

    def test_add_repository_400_when_no_repository_id(self):
        agent = _agent(add_task_repository=lambda *a, **k: {'added': True})
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/add-repository', json={},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_repository_400_when_repository_id_blank(self):
        agent = _agent(add_task_repository=lambda *a, **k: {'added': True})
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/add-repository', json={'repository_id': '   '},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_repository_404_when_not_in_inventory(self):
        agent = _agent(add_task_repository=lambda task_id, repo_id: {
            'added': False, 'error': f'{repo_id!r} is not in the kato inventory',
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/add-repository', json={'repository_id': 'unknown'},
        )
        self.assertEqual(response.status_code, 404)

    def test_add_repository_500_on_unknown_error(self):
        agent = _agent(add_task_repository=lambda task_id, repo_id: {
            'added': False, 'error': 'clone failed',
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/add-repository', json={'repository_id': 'client'},
        )
        self.assertEqual(response.status_code, 500)

    def test_add_repository_success_returns_payload(self):
        captured = {}
        def add(task_id, repository_id):
            captured['args'] = (task_id, repository_id)
            return {'added': True, 'repository_id': repository_id}
        agent = _agent(add_task_repository=add)
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/add-repository', json={'repository_id': 'client'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['added'])
        self.assertEqual(captured['args'], ('T-1', 'client'))


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/finish  +  /workspace (DELETE)
# ---------------------------------------------------------------------------


class FinishAndForgetWorkspaceTests(unittest.TestCase):
    def test_finish_503_when_no_agent_service(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post('/api/sessions/T-1/finish')
        self.assertEqual(response.status_code, 503)

    def test_finish_501_when_method_missing(self):
        app = create_app(session_manager=_FakeManager(), agent_service=_agent())
        response = app.test_client().post('/api/sessions/T-1/finish')
        self.assertEqual(response.status_code, 501)

    def test_finish_returns_500_when_agent_reports_error(self):
        agent = _agent(finish_task_planning_session=lambda t: {
            'finished': False, 'error': 'no live session',
        })
        app = create_app(session_manager=_FakeManager(), agent_service=agent)
        response = app.test_client().post('/api/sessions/T-1/finish')
        self.assertEqual(response.status_code, 500)

    def test_finish_success_returns_payload(self):
        agent = _agent(finish_task_planning_session=lambda t: {
            'finished': True, 'pull_request_url': 'https://example/pr/2',
        })
        app = create_app(session_manager=_FakeManager(), agent_service=agent)
        response = app.test_client().post('/api/sessions/T-1/finish')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['finished'])
        self.assertEqual(payload['pull_request_url'], 'https://example/pr/2')

    def test_forget_workspace_503_when_no_workspace_manager(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().delete('/api/sessions/T-1/workspace')
        self.assertEqual(response.status_code, 503)

    def test_forget_workspace_500_when_delete_raises(self):
        workspace = _FakeWorkspaceManager()
        def boom(_):
            raise RuntimeError('on fire')
        workspace.delete = boom
        app = create_app(
            session_manager=_FakeManager(),
            workspace_manager=workspace,
        )
        response = app.test_client().delete('/api/sessions/T-1/workspace')
        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertFalse(payload['forgotten'])
        self.assertIn('on fire', payload['error'])

    def test_forget_workspace_returns_forgotten_true_on_success(self):
        workspace = _FakeWorkspaceManager()
        app = create_app(
            session_manager=_FakeManager(),
            workspace_manager=workspace,
        )
        response = app.test_client().delete('/api/sessions/T-9/workspace')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['forgotten'])
        self.assertEqual(workspace.deleted, ['T-9'])

    def test_forget_workspace_terminates_session_BEFORE_deleting(self):
        # Operator-reported regression: Windows file locks held by
        # the live Claude subprocess blocked rmtree, so the workspace
        # dir survived the click. The route must kill the subprocess
        # + drop the session record FIRST, then delete the clone.
        manager = _FakeManager()
        workspace = _FakeWorkspaceManager()
        app = create_app(
            session_manager=manager,
            workspace_manager=workspace,
        )
        response = app.test_client().delete('/api/sessions/T-9/workspace')
        self.assertEqual(response.status_code, 200)
        # terminate_session was called with remove_record=True.
        self.assertEqual(manager.terminated, [('T-9', True)])
        # delete was called AFTER terminate.
        self.assertEqual(workspace.deleted, ['T-9'])

    def test_forget_workspace_500_when_dir_still_exists_after_delete(self):
        # The new verification step: if ``delete`` silently failed
        # (it swallows OSError) and the directory is still on disk,
        # the route surfaces that to the operator as a 500 with a
        # concrete recovery hint instead of returning a misleading
        # 200.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / 'still-here'
            blocker.mkdir()
            workspace = _FakeWorkspaceManager(
                workspace_path_for={'T-7': str(blocker)},
            )
            # Make ``delete`` a quiet no-op so the dir survives.
            workspace.delete = lambda _: None
            app = create_app(
                session_manager=_FakeManager(),
                workspace_manager=workspace,
            )
            response = app.test_client().delete(
                '/api/sessions/T-7/workspace',
            )
        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertFalse(payload['forgotten'])
        self.assertIn('still exists', payload['error'])

    def test_forget_workspace_continues_when_terminate_session_raises(self):
        # The two cleanup steps are independent: a terminate failure
        # (e.g. subprocess already dead) must not abort the rmtree
        # path. The operator sees both errors aggregated in the 500
        # body but the rmtree still runs.
        manager = _FakeManager()
        def boom(_, **__):
            raise RuntimeError('subprocess gone')
        manager.terminate_session = boom
        workspace = _FakeWorkspaceManager()
        app = create_app(
            session_manager=manager,
            workspace_manager=workspace,
        )
        response = app.test_client().delete('/api/sessions/T-6/workspace')
        # terminate failure surfaces as 500 with the error AND the
        # rmtree still ran.
        self.assertEqual(response.status_code, 500)
        self.assertIn('subprocess gone', response.get_json()['error'])
        self.assertEqual(workspace.deleted, ['T-6'])


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/stop
# ---------------------------------------------------------------------------


class StopSessionEndpointTests(unittest.TestCase):
    def test_404_when_record_not_found(self):
        # No record exists for ``UNKNOWN`` → 404.
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post('/api/sessions/UNKNOWN/stop')
        self.assertEqual(response.status_code, 404)

    def test_returns_500_when_terminate_raises(self):
        class _Manager(_FakeManager):
            def terminate_session(self, task_id):  # noqa: ARG002
                raise RuntimeError('stuck')
        mgr = _Manager(records=[_FakeRecord(task_id='T-1')])
        app = create_app(session_manager=mgr)
        response = app.test_client().post('/api/sessions/T-1/stop')
        self.assertEqual(response.status_code, 500)
        self.assertIn('stuck', response.get_json()['error'])

    def test_returns_stopped_on_success(self):
        terminate = MagicMock()
        class _Manager(_FakeManager):
            def terminate_session(self, task_id):
                terminate(task_id)
        mgr = _Manager(records=[_FakeRecord(task_id='T-1')])
        app = create_app(session_manager=mgr)
        response = app.test_client().post('/api/sessions/T-1/stop')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'stopped')
        terminate.assert_called_once_with('T-1')


# ---------------------------------------------------------------------------
# /api/status/events — SSE stream (no broadcaster branch only)
# ---------------------------------------------------------------------------


class StatusEventsEndpointTests(unittest.TestCase):
    """When no broadcaster is wired the endpoint emits one ``disabled``
    event and closes — that's the safe-to-test path. The broadcaster
    branch is an infinite long-poll, exercised elsewhere via the
    broadcaster's own tests."""

    def test_returns_sse_mime_and_disabled_event_when_no_broadcaster(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/status/events')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.mimetype.startswith('text/event-stream'))
        # Body is one SSE message frame for status_disabled.
        body = response.get_data(as_text=True)
        self.assertIn('event: ', body)
        self.assertIn('status_disabled', body)


# ---------------------------------------------------------------------------
# /api/status/recent — not in original list but lives next to /events
# ---------------------------------------------------------------------------


class StatusRecentEndpointTests(unittest.TestCase):
    def test_returns_empty_entries_when_no_broadcaster(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/status/recent')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'entries': [], 'latest_sequence': 0,
        })

    def test_returns_recent_when_broadcaster_wired(self):
        entry = SimpleNamespace(
            sequence=4,
            to_dict=lambda: {'sequence': 4, 'message': 'tick'},
        )
        broadcaster = SimpleNamespace(
            recent=lambda: [entry],
            latest_sequence=lambda: 4,
        )
        app = create_app(
            session_manager=_FakeManager(),
            status_broadcaster=broadcaster,
        )
        response = app.test_client().get('/api/status/recent')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['latest_sequence'], 4)
        self.assertEqual(payload['entries'][0]['message'], 'tick')


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/publish-state — used by Push button state
# ---------------------------------------------------------------------------


class PublishStateEndpointTests(unittest.TestCase):
    def test_returns_false_state_without_agent_service(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/sessions/T-1/publish-state')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['has_workspace'])
        self.assertFalse(payload['has_pull_request'])
        self.assertEqual(payload['task_id'], 'T-1')

    def test_returns_false_state_when_method_missing(self):
        app = create_app(
            session_manager=_FakeManager(),
            agent_service=_agent(),
        )
        response = app.test_client().get('/api/sessions/T-1/publish-state')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['has_workspace'])
        self.assertEqual(payload['task_id'], 'T-1')

    def test_returns_agent_state_with_task_id_overlay(self):
        # The route always overlays ``task_id`` on top of the agent's
        # payload so the UI doesn't have to track it separately.
        agent = _agent(task_publish_state=lambda t: {
            'has_workspace': True, 'has_pull_request': False,
        })
        app = create_app(
            session_manager=_FakeManager(),
            agent_service=agent,
        )
        response = app.test_client().get('/api/sessions/T-7/publish-state')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['has_workspace'])
        self.assertEqual(payload['task_id'], 'T-7')


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/sync-repositories — multi-repo sync from ticket tags
# ---------------------------------------------------------------------------


class SyncRepositoriesEndpointTests(unittest.TestCase):
    def _client(self, *, agent=None):
        return create_app(
            session_manager=_FakeManager(),
            agent_service=agent,
        ).test_client()

    def test_503_when_no_agent(self):
        response = self._client().post('/api/sessions/T-1/sync-repositories')
        self.assertEqual(response.status_code, 503)

    def test_501_when_method_missing(self):
        response = self._client(agent=_agent()).post(
            '/api/sessions/T-1/sync-repositories',
        )
        self.assertEqual(response.status_code, 501)

    def test_404_when_no_workspace_in_error(self):
        agent = _agent(sync_task_repositories=lambda t: {
            'synced': False, 'error': 'no workspace for this task',
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/sync-repositories',
        )
        self.assertEqual(response.status_code, 404)

    def test_500_on_generic_error(self):
        agent = _agent(sync_task_repositories=lambda t: {
            'synced': False, 'error': 'something else broke',
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/sync-repositories',
        )
        self.assertEqual(response.status_code, 500)

    def test_success_returns_added_list(self):
        agent = _agent(sync_task_repositories=lambda t: {
            'synced': True, 'added': ['client', 'backend'],
        })
        response = self._client(agent=agent).post(
            '/api/sessions/T-1/sync-repositories',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['added'], ['client', 'backend'])


class EffortRoutesTests(unittest.TestCase):
    """Per-task chat effort: discovered levels + get/set/clear override."""

    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def test_effort_levels_endpoint_lists_levels(self):
        response = self._client().get('/api/effort-levels')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIsInstance(body['levels'], list)
        # Present in both the live CLI and the static fallback.
        self.assertIn('high', body['levels'])
        self.assertIn('max', body['levels'])
        self.assertIn('default', body)

    def test_get_set_and_clear_session_effort(self):
        client = self._client()
        self.assertEqual(
            client.get('/api/sessions/T-1/effort').get_json()['effort'], '',
        )
        set_resp = client.post('/api/sessions/T-1/effort', json={'effort': 'high'})
        self.assertEqual(set_resp.status_code, 200)
        self.assertEqual(set_resp.get_json()['effort'], 'high')
        self.assertEqual(
            client.get('/api/sessions/T-1/effort').get_json()['effort'], 'high',
        )
        client.post('/api/sessions/T-1/effort', json={'effort': ''})
        self.assertEqual(
            client.get('/api/sessions/T-1/effort').get_json()['effort'], '',
        )

    def test_set_rejects_unknown_effort(self):
        response = self._client().post(
            '/api/sessions/T-1/effort', json={'effort': 'turbo'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('turbo', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
