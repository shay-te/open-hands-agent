"""Targeted coverage closers for ``kato_webserver.app``.

Each test class focuses on one narrow error / fallback path that the
existing ``test_app.py`` / ``test_app_routes.py`` suites don't yet
exercise. Patterns mirror the existing tests:

* Flask test client only — no real network / fs (apart from ``tempfile``).
* SimpleNamespace agent stubs via ``_agent`` so route's
  ``getattr(..., 'method', None)`` resolution decides the branch.
* Module-level helpers (e.g. ``_settings_env_path``) are exercised
  directly when they expose a stable contract — saves spinning a
  Flask app just to reach a single ``return``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_webserver.app import (
    KATO_REPO_ROOT,
    _changed_files_for_repo,
    _enumerate_repo_ids_from_disk,
    _no_base_error_message,
    _record_cwd_or_none,
    _repo_relative_path,
    _repository_cwd,
    _resolve_repo_cwd,
    _settings_env_path,
    _task_repository_ids,
    _workspace_status,
    create_app,
)


# ---------------------------------------------------------------------------
# Shared fakes — same as test_app_routes.py
# ---------------------------------------------------------------------------


class _FakeRecord:
    def __init__(self, **kwargs):
        self._payload = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self):
        return dict(self._payload)


class _FakeManager:
    def __init__(self, records=None):
        self._records = records or []
        self.terminated = []

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
        self.terminated.append((task_id, remove_record))


class _FakeWorkspaceRecord:
    def __init__(self, **payload):
        self._payload = payload
        self.task_id = payload.get('task_id', '')
        self.repository_ids = payload.get('repository_ids', [])
        self.status = payload.get('status', '')

    def to_dict(self):
        return dict(self._payload)


class _FakeWorkspaceManager:
    def __init__(self, records=None, *, repo_paths=None, workspace_path_for=None):
        self._records = list(records or [])
        self._repo_paths = dict(repo_paths or {})
        self._workspace_path_for = dict(workspace_path_for or {})
        self.deleted = []

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
    return SimpleNamespace(**methods)


# ---------------------------------------------------------------------------
# Module-level helpers (no Flask wiring required)
# ---------------------------------------------------------------------------


class ModuleLevelHelperTests(unittest.TestCase):
    """Pure helpers that route handlers depend on. Hitting them
    directly saves spinning a full Flask app per branch."""

    def test_record_cwd_or_none_returns_none_when_record_missing(self):
        manager = _FakeManager()
        self.assertIsNone(_record_cwd_or_none(manager, 'NO-SUCH'))

    def test_record_cwd_or_none_returns_none_when_cwd_empty(self):
        manager = _FakeManager(records=[_FakeRecord(task_id='T-1')])
        self.assertIsNone(_record_cwd_or_none(manager, 'T-1'))

    def test_record_cwd_or_none_returns_none_when_cwd_not_a_dir(self):
        manager = _FakeManager(records=[_FakeRecord(task_id='T-1', cwd='/nonexistent/path/xyz')])
        self.assertIsNone(_record_cwd_or_none(manager, 'T-1'))

    def test_task_repository_ids_returns_empty_when_workspace_manager_none(self):
        self.assertEqual(_task_repository_ids(None, 'T-1'), [])

    def test_task_repository_ids_handles_workspace_get_raising(self):
        workspace = _FakeWorkspaceManager()
        def boom(_):
            raise RuntimeError('disk lost')
        workspace.get = boom
        # Disk scan also fails (no workspace path → /missing)
        result = _task_repository_ids(workspace, 'T-1')
        self.assertEqual(result, [])

    def test_enumerate_repo_ids_returns_empty_when_no_task_id(self):
        workspace = _FakeWorkspaceManager()
        self.assertEqual(_enumerate_repo_ids_from_disk(workspace, ''), [])

    def test_enumerate_repo_ids_returns_empty_when_workspace_path_raises(self):
        workspace = _FakeWorkspaceManager()
        def boom(_):
            raise RuntimeError('cant')
        workspace.workspace_path = boom
        self.assertEqual(_enumerate_repo_ids_from_disk(workspace, 'T-1'), [])

    def test_enumerate_repo_ids_returns_empty_when_path_not_dir(self):
        workspace = _FakeWorkspaceManager(
            workspace_path_for={'T-1': '/this/does/not/exist'},
        )
        self.assertEqual(_enumerate_repo_ids_from_disk(workspace, 'T-1'), [])

    def test_enumerate_repo_ids_handles_iterdir_oserror(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _FakeWorkspaceManager(workspace_path_for={'T-1': tmp})
            # Patch ``iterdir`` to raise on the resolved Path.
            with patch.object(Path, 'iterdir', side_effect=OSError('permission denied')):
                result = _enumerate_repo_ids_from_disk(workspace, 'T-1')
        self.assertEqual(result, [])

    def test_repository_cwd_returns_none_when_no_workspace_manager(self):
        self.assertIsNone(_repository_cwd(None, 'T-1', 'client'))

    def test_repository_cwd_returns_none_when_no_repo_id(self):
        workspace = _FakeWorkspaceManager()
        self.assertIsNone(_repository_cwd(workspace, 'T-1', ''))

    def test_repository_cwd_returns_none_when_repository_path_raises(self):
        workspace = _FakeWorkspaceManager()
        def boom(*_):
            raise RuntimeError('no path')
        workspace.repository_path = boom
        self.assertIsNone(_repository_cwd(workspace, 'T-1', 'client'))

    def test_repo_relative_path_returns_none_for_empty(self):
        self.assertIsNone(_repo_relative_path('', '/some/cwd'))

    def test_repo_relative_path_returns_none_for_dev_null(self):
        self.assertIsNone(_repo_relative_path('/dev/null', '/some/cwd'))

    def test_repo_relative_path_returns_none_when_no_cwd(self):
        self.assertIsNone(_repo_relative_path('src/x.py', ''))

    def test_repo_relative_path_handles_resolve_error(self):
        # The function does ``root = Path(cwd).resolve()`` first, then
        # the candidate. If the candidate path resolve raises, we get
        # None. Trigger by patching only on the second .resolve() call.
        original_resolve = Path.resolve
        call_count = {'n': 0}
        def fake_resolve(self_path, *a, **kw):
            call_count['n'] += 1
            # Skip the first call (the root resolve); fail the second
            # call (the candidate path resolve).
            if call_count['n'] == 2:
                raise OSError('bad path')
            return original_resolve(self_path, *a, **kw)
        with patch.object(Path, 'resolve', fake_resolve):
            result = _repo_relative_path('/some/absolute/x.py', '/tmp')
        self.assertIsNone(result)

    def test_repo_relative_path_returns_none_when_outside_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            outside = Path(other) / 'file.py'
            outside.write_text('x', encoding='utf-8')
            self.assertIsNone(_repo_relative_path(str(outside), root))

    def test_settings_env_path_falls_back_to_repo_root(self):
        # No ``KATO_SETTINGS_ENV_FILE`` → ``<repo>/.env``.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_SETTINGS_ENV_FILE', None)
            result = _settings_env_path()
        self.assertEqual(result, KATO_REPO_ROOT / '.env')

    def test_workspace_status_returns_empty_when_no_manager(self):
        self.assertEqual(_workspace_status(None, 'T-1'), '')

    def test_workspace_status_returns_empty_when_get_raises(self):
        workspace = _FakeWorkspaceManager()
        def boom(_):
            raise RuntimeError('lookup failed')
        workspace.get = boom
        self.assertEqual(_workspace_status(workspace, 'T-1'), '')

    def test_workspace_status_returns_empty_when_no_record(self):
        workspace = _FakeWorkspaceManager()
        self.assertEqual(_workspace_status(workspace, 'NO-SUCH'), '')

    def test_workspace_status_returns_status_string(self):
        workspace = _FakeWorkspaceManager(records=[
            _FakeWorkspaceRecord(task_id='T-1', status='review'),
        ])
        self.assertEqual(_workspace_status(workspace, 'T-1'), 'review')

    def test_no_base_error_message_uses_repo_id_when_provided(self):
        msg = _no_base_error_message('client')
        self.assertIn("'client'", msg)
        self.assertIn('destination_branch', msg)

    def test_no_base_error_message_empty_repo_uses_generic_text(self):
        # Hits the ``return`` after the ``if repo_id`` branch (lines 570).
        msg = _no_base_error_message('')
        self.assertIn('no destination branch', msg)
        self.assertNotIn("''", msg)

    def test_changed_files_for_repo_returns_empty_when_no_base(self):
        # ``_resolve_diff_base`` returns '' if detect_default_branch '' AND
        # no agent_service.configured_destination_branch.
        with patch('kato_webserver.app.detect_default_branch', return_value=''):
            self.assertEqual(_changed_files_for_repo('', '/tmp', None), [])

    def test_resolve_repo_cwd_with_repo_id_falls_through_to_session(self):
        # When ``repo_id`` resolves but ``_repository_cwd`` returns None,
        # the helper falls through to ``_record_cwd_or_none``.
        manager = _FakeManager(records=[_FakeRecord(task_id='T-1', cwd='/no/such')])
        workspace = _FakeWorkspaceManager()  # repository_path → /missing
        # Both branches fall through to None
        self.assertIsNone(_resolve_repo_cwd(manager, workspace, 'T-1', 'client'))

    def test_resolve_repo_cwd_uses_repository_cwd_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _FakeWorkspaceManager(
                repo_paths={('T-1', 'client'): tmp},
            )
            result = _resolve_repo_cwd(None, workspace, 'T-1', 'client')
        self.assertEqual(result, tmp)


# ---------------------------------------------------------------------------
# Settings routes — error / fallback paths
# ---------------------------------------------------------------------------


class SettingsRouteErrorTests(unittest.TestCase):
    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def test_update_settings_missing_path(self):
        response = self._client().post('/api/settings', json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn('repository_root_path', response.get_json()['error'])

    def test_update_settings_invalid_path_resolve_error(self):
        client = self._client()
        with patch.object(Path, 'expanduser', side_effect=OSError('bad expand')):
            response = client.post(
                '/api/settings', json={'repository_root_path': '/foo'},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('invalid path', response.get_json()['error'])

    def test_update_settings_path_does_not_exist(self):
        response = self._client().post(
            '/api/settings',
            json={'repository_root_path': '/nope/does/not/exist/anywhere'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('does not exist', response.get_json()['error'])

    def test_update_settings_path_is_file(self):
        with tempfile.NamedTemporaryFile() as fh:
            response = self._client().post(
                '/api/settings',
                json={'repository_root_path': fh.name},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('not a directory', response.get_json()['error'])

    def test_update_settings_persist_oserror(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp, patch(
            'kato_webserver.app._persist_settings',
            side_effect=OSError('disk full'),
        ):
            response = client.post(
                '/api/settings',
                json={'repository_root_path': tmp},
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn('failed to write settings file', response.get_json()['error'])


# ---------------------------------------------------------------------------
# /api/task-providers POST error paths
# ---------------------------------------------------------------------------


class TaskProviderUpdateErrorTests(unittest.TestCase):
    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def test_unknown_active_provider(self):
        response = self._client().post(
            '/api/task-providers', json={'active': 'fake-platform'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('unknown task provider', response.get_json()['error'])

    def test_unknown_provider_in_fields_block(self):
        response = self._client().post(
            '/api/task-providers',
            json={'provider': 'fake', 'fields': {'X': 'y'}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('unknown task provider', response.get_json()['error'])

    def test_fields_not_object_returns_400(self):
        response = self._client().post(
            '/api/task-providers',
            json={'provider': 'youtrack', 'fields': 'not an object'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('fields must be an object', response.get_json()['error'])

    def test_no_recognised_updates_returns_400(self):
        response = self._client().post(
            '/api/task-providers',
            json={'provider': 'youtrack', 'fields': {'NOT_ALLOWED': 'x'}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('no recognised updates', response.get_json()['error'])

    def test_validation_error_returns_400(self):
        with patch(
            'kato_webserver.app._validate_settings',
            return_value=['bad value'],
        ):
            response = self._client().post(
                '/api/task-providers',
                json={
                    'provider': 'youtrack',
                    'fields': {'YOUTRACK_API_BASE_URL': 'x'},
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('bad value', response.get_json()['error'])

    def test_persist_settings_oserror_returns_500(self):
        with patch(
            'kato_webserver.app._validate_settings', return_value=[],
        ), patch(
            'kato_webserver.app._persist_settings',
            side_effect=OSError('disk full'),
        ):
            response = self._client().post(
                '/api/task-providers',
                json={
                    'provider': 'youtrack',
                    'fields': {'YOUTRACK_API_BASE_URL': 'x'},
                },
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn('failed to write settings file', response.get_json()['error'])


# ---------------------------------------------------------------------------
# /api/git-providers POST error paths
# ---------------------------------------------------------------------------


class GitProviderUpdateErrorTests(unittest.TestCase):
    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def test_unknown_provider(self):
        response = self._client().post(
            '/api/git-providers', json={'provider': 'fake'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('unknown git host', response.get_json()['error'])

    def test_missing_provider(self):
        response = self._client().post('/api/git-providers', json={})
        self.assertEqual(response.status_code, 400)

    def test_fields_not_object_returns_400(self):
        response = self._client().post(
            '/api/git-providers',
            json={'provider': 'github', 'fields': 'oops'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('fields must be an object', response.get_json()['error'])

    def test_no_recognised_fields(self):
        response = self._client().post(
            '/api/git-providers',
            json={'provider': 'github', 'fields': {'NOT_REAL': 'x'}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('no recognised fields', response.get_json()['error'])

    def test_validation_error(self):
        with patch(
            'kato_webserver.app._validate_settings',
            return_value=['bad'],
        ):
            response = self._client().post(
                '/api/git-providers',
                json={'provider': 'github', 'fields': {'GITHUB_API_BASE_URL': 'x'}},
            )
        self.assertEqual(response.status_code, 400)

    def test_persist_oserror_returns_500(self):
        with patch(
            'kato_webserver.app._validate_settings', return_value=[],
        ), patch(
            'kato_webserver.app._persist_settings',
            side_effect=OSError('disk full'),
        ):
            response = self._client().post(
                '/api/git-providers',
                json={'provider': 'github', 'fields': {'GITHUB_API_BASE_URL': 'x'}},
            )
        self.assertEqual(response.status_code, 500)


# ---------------------------------------------------------------------------
# /api/all-settings POST error paths
# ---------------------------------------------------------------------------


class AllSettingsUpdateErrorTests(unittest.TestCase):
    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def test_updates_not_an_object(self):
        response = self._client().post(
            '/api/all-settings', json={'updates': 'not a dict'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('updates must be an object', response.get_json()['error'])

    def test_no_recognised_keys(self):
        # ``all_settings_keys()`` whitelists nothing of the test's keys.
        with patch(
            'kato_core_lib.helpers.kato_settings_schema_utils.all_settings_keys',
            return_value=set(),
        ):
            response = self._client().post(
                '/api/all-settings',
                json={'updates': {'X': 'y'}},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('no recognised settings', response.get_json()['error'])

    def test_validation_error_returns_400(self):
        with patch(
            'kato_core_lib.helpers.kato_settings_schema_utils.all_settings_keys',
            return_value={'KATO_X'},
        ), patch(
            'kato_webserver.app._validate_settings',
            return_value=['bad value'],
        ):
            response = self._client().post(
                '/api/all-settings',
                json={'updates': {'KATO_X': 'val'}},
            )
        self.assertEqual(response.status_code, 400)

    def test_persist_oserror_returns_500(self):
        with patch(
            'kato_core_lib.helpers.kato_settings_schema_utils.all_settings_keys',
            return_value={'KATO_X'},
        ), patch(
            'kato_webserver.app._validate_settings', return_value=[],
        ), patch(
            'kato_webserver.app._persist_settings',
            side_effect=OSError('disk full'),
        ):
            response = self._client().post(
                '/api/all-settings',
                json={'updates': {'KATO_X': 'val'}},
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn('failed to write settings file', response.get_json()['error'])

    def test_bool_value_coerced_to_string(self):
        # Cover the ``isinstance(value, bool)`` true branch.
        captured = {}
        def fake_persist(updates):
            captured.update(updates)
        with patch(
            'kato_core_lib.helpers.kato_settings_schema_utils.all_settings_keys',
            return_value={'KATO_FLAG'},
        ), patch(
            'kato_webserver.app._validate_settings', return_value=[],
        ), patch(
            'kato_webserver.app._persist_settings', side_effect=fake_persist,
        ):
            response = self._client().post(
                '/api/all-settings',
                json={'updates': {'KATO_FLAG': True}},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['KATO_FLAG'], 'true')


# ---------------------------------------------------------------------------
# /api/repository-approvals POST error paths
# ---------------------------------------------------------------------------


class RepositoryApprovalsTests(unittest.TestCase):
    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def test_post_rejects_non_array_approve(self):
        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.RepositoryApprovalService'
        ):
            response = self._client().post(
                '/api/repository-approvals',
                json={'approve': 'not array', 'revoke': []},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('approve / revoke must be arrays', response.get_json()['error'])

    def test_post_handles_bad_approval_mode_falls_back_to_restricted(self):
        # Triggers the ApprovalMode.from_string exception path.
        approve_calls = []
        class _StubService:
            def __init__(self):
                self.storage_path = '/tmp/x'
            def list_approvals(self):
                return []
            def approve(self, repo_id, remote_url, *, mode):
                approve_calls.append((repo_id, mode))
                return SimpleNamespace(
                    repository_id=repo_id,
                    approval_mode=SimpleNamespace(value=str(mode)),
                )
            def revoke(self, _):
                return False
        class _StubMode:
            RESTRICTED = SimpleNamespace(value='restricted')
            @staticmethod
            def from_string(s):
                raise ValueError('bad')
        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.RepositoryApprovalService',
            _StubService,
        ), patch(
            'kato_core_lib.data_layers.data.repository_approval.ApprovalMode',
            _StubMode,
        ):
            response = self._client().post(
                '/api/repository-approvals',
                json={
                    'approve': [{
                        'repository_id': 'r1',
                        'remote_url': 'u',
                        'mode': 'bogus-mode',
                    }],
                    'revoke': [],
                },
            )
        self.assertEqual(response.status_code, 200)
        # ApprovalMode.RESTRICTED was used as fallback.
        self.assertEqual(approve_calls[0][1], _StubMode.RESTRICTED)


# ---------------------------------------------------------------------------
# /logo.png /favicon.png /favicon.ico — 404 branches
# ---------------------------------------------------------------------------


class LogoFaviconTests(unittest.TestCase):
    def test_logo_returns_404_when_file_missing(self):
        app = create_app(session_manager=_FakeManager())
        with patch.object(Path, 'exists', return_value=False):
            response = app.test_client().get('/logo.png')
        self.assertEqual(response.status_code, 404)

    def test_favicon_png_returns_404_when_file_missing(self):
        app = create_app(session_manager=_FakeManager())
        with patch.object(Path, 'exists', return_value=False):
            response = app.test_client().get('/favicon.png')
        self.assertEqual(response.status_code, 404)

    def test_favicon_ico_returns_404_when_file_missing(self):
        app = create_app(session_manager=_FakeManager())
        with patch.object(Path, 'exists', return_value=False):
            response = app.test_client().get('/favicon.ico')
        self.assertEqual(response.status_code, 404)

    def test_favicon_png_sets_no_cache_when_file_exists(self):
        app = create_app(session_manager=_FakeManager())
        # The kato.png file should exist in the kato repo root.
        if not (KATO_REPO_ROOT / 'kato.png').exists():
            self.skipTest('kato.png not present in test environment')
        response = app.test_client().get('/favicon.png')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-cache', response.headers.get('Cache-Control', ''))


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/file — error / 403 / 404 / size cap branches
# ---------------------------------------------------------------------------


class SessionFileEndpointTests(unittest.TestCase):
    def test_missing_path_returns_400(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/sessions/T-1/file')
        self.assertEqual(response.status_code, 400)
        self.assertIn('path query parameter', response.get_json()['error'])

    def test_no_workspace_returns_404(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/sessions/T-1/file?path=foo.py')
        self.assertEqual(response.status_code, 404)
        self.assertIn('no workspace', response.get_json()['error'])

    def test_absolute_outside_workspace_returns_403(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside) / 'leak.txt'
            outside_file.write_text('secret', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            response = app.test_client().get(
                f'/api/sessions/T-1/file?path={outside_file}',
            )
        self.assertEqual(response.status_code, 403)

    def test_relative_path_resolves_inside_workspace(self):
        with tempfile.TemporaryDirectory() as workspace:
            f = Path(workspace) / 'hello.py'
            f.write_text('print("hi")\n', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            response = app.test_client().get(
                '/api/sessions/T-1/file?path=hello.py',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['binary'])
        self.assertIn('hi', payload['content'])

    def test_in_workspace_but_missing_returns_404(self):
        with tempfile.TemporaryDirectory() as workspace:
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            response = app.test_client().get(
                '/api/sessions/T-1/file?path=does-not-exist.py',
            )
        self.assertEqual(response.status_code, 404)

    def test_binary_file_returns_binary_true(self):
        with tempfile.TemporaryDirectory() as workspace:
            f = Path(workspace) / 'bin.dat'
            f.write_bytes(b'\x00\x01\x02 binary content')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            response = app.test_client().get(
                '/api/sessions/T-1/file?path=bin.dat',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['binary'])

    def test_too_large_returns_200_with_too_large_flag(self):
        # Write an actual >1MB file so we hit the size cap branch
        # without monkeypatching stat (which breaks is_file()).
        with tempfile.TemporaryDirectory() as workspace:
            f = Path(workspace) / 'big.txt'
            # 1_000_001 bytes — just over the 1MB cap.
            f.write_bytes(b'x' * 1_000_001)
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            response = app.test_client().get(
                '/api/sessions/T-1/file?path=big.txt',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['too_large'])
        self.assertGreater(payload['size'], 1_000_000)

    def test_read_oserror_returns_500(self):
        with tempfile.TemporaryDirectory() as workspace:
            f = Path(workspace) / 'y.txt'
            f.write_text('hello', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            real_read_bytes = Path.read_bytes
            def fake_read_bytes(self_path):
                if self_path.name == 'y.txt':
                    raise OSError('read failed')
                return real_read_bytes(self_path)
            with patch.object(Path, 'read_bytes', fake_read_bytes):
                response = app.test_client().get(
                    '/api/sessions/T-1/file?path=y.txt',
                )
        self.assertEqual(response.status_code, 500)
        self.assertIn('read failed', response.get_json()['error'])


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/base-file — multiple branches
# ---------------------------------------------------------------------------


class SessionBaseFileEndpointTests(unittest.TestCase):
    def test_missing_path_returns_400(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/sessions/T-1/base-file')
        self.assertEqual(response.status_code, 400)

    def test_dev_null_returns_404(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get(
            '/api/sessions/T-1/base-file?path=/dev/null',
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn('not found at base', response.get_json()['error'])

    def test_no_workspace_returns_404(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get(
            '/api/sessions/T-1/base-file?path=foo.py',
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn('no workspace', response.get_json()['error'])

    def test_outside_workspace_returns_403(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as other:
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            other_file = Path(other) / 'leak.py'
            other_file.write_text('x', encoding='utf-8')
            app = create_app(session_manager=manager)
            response = app.test_client().get(
                f'/api/sessions/T-1/base-file?path={other_file}',
            )
        self.assertEqual(response.status_code, 403)

    def test_no_base_returns_404(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / 'foo.py').write_text('x', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch', return_value='',
            ):
                response = app.test_client().get(
                    '/api/sessions/T-1/base-file?path=foo.py',
                )
        self.assertEqual(response.status_code, 404)

    def test_blob_size_none_returns_404(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / 'foo.py').write_text('x', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch', return_value='main',
            ), patch(
                'kato_webserver.app.blob_size_at_ref', return_value=None,
            ):
                response = app.test_client().get(
                    '/api/sessions/T-1/base-file?path=foo.py',
                )
        self.assertEqual(response.status_code, 404)

    def test_too_large_returns_too_large_flag(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / 'foo.py').write_text('x', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch', return_value='main',
            ), patch(
                'kato_webserver.app.blob_size_at_ref', return_value=2_000_000,
            ):
                response = app.test_client().get(
                    '/api/sessions/T-1/base-file?path=foo.py',
                )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['too_large'])

    def test_content_none_returns_404(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / 'foo.py').write_text('x', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch', return_value='main',
            ), patch(
                'kato_webserver.app.blob_size_at_ref', return_value=100,
            ), patch(
                'kato_webserver.app.file_text_at_ref', return_value=None,
            ):
                response = app.test_client().get(
                    '/api/sessions/T-1/base-file?path=foo.py',
                )
        self.assertEqual(response.status_code, 404)

    def test_binary_base_returns_binary_true(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / 'foo.py').write_text('x', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch', return_value='main',
            ), patch(
                'kato_webserver.app.blob_size_at_ref', return_value=100,
            ), patch(
                'kato_webserver.app.file_text_at_ref', return_value='\x00binary',
            ):
                response = app.test_client().get(
                    '/api/sessions/T-1/base-file?path=foo.py',
                )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['binary'])

    def test_text_base_returns_content(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / 'foo.py').write_text('x', encoding='utf-8')
            manager = _FakeManager(records=[
                _FakeRecord(task_id='T-1', cwd=workspace),
            ])
            app = create_app(session_manager=manager)
            with patch(
                'kato_webserver.app.detect_default_branch', return_value='main',
            ), patch(
                'kato_webserver.app.blob_size_at_ref', return_value=100,
            ), patch(
                'kato_webserver.app.file_text_at_ref', return_value='print(1)\n',
            ):
                response = app.test_client().get(
                    '/api/sessions/T-1/base-file?path=foo.py',
                )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['binary'])
        self.assertIn('print', payload['content'])


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/messages — error / 409 paths
# ---------------------------------------------------------------------------


class MessagesEndpointErrorTests(unittest.TestCase):
    def test_400_when_no_text_or_images(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('text or images is required', response.get_json()['error'])

    def test_images_non_list_falls_through(self):
        # ``images=string`` is coerced to ``[]`` then we still have no
        # text → 400. Exercises the ``isinstance`` guard.
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'images': 'not a list'},
        )
        self.assertEqual(response.status_code, 400)

    def test_no_live_session_no_runner_returns_409(self):
        # No live session + no PLANNING_SESSION_RUNNER → 409.
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hi'},
        )
        self.assertEqual(response.status_code, 409)

    def test_live_session_delivery_succeeds(self):
        session = MagicMock()
        session.is_alive = True
        session.send_user_message = MagicMock()
        manager = _FakeManager()
        manager.get_session = lambda task_id: session
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hello'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'delivered')

    def test_live_session_send_raises_returns_500(self):
        session = MagicMock()
        session.is_alive = True
        session.send_user_message = MagicMock(side_effect=RuntimeError('boom'))
        manager = _FakeManager()
        manager.get_session = lambda task_id: session
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hi'},
        )
        self.assertEqual(response.status_code, 500)

    def test_live_session_typeerror_falls_back_to_text_only(self):
        session = MagicMock()
        session.is_alive = True
        # First call (with images=) raises TypeError; fallback (text only) succeeds.
        call_count = {'n': 0}
        def send(text, images=None):
            call_count['n'] += 1
            if images is not None:
                raise TypeError('old signature')
        session.send_user_message = send
        manager = _FakeManager()
        manager.get_session = lambda task_id: session
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hi'},
        )
        self.assertEqual(response.status_code, 200)

    def test_live_session_fallback_also_raises_returns_500(self):
        session = MagicMock()
        session.is_alive = True
        def send(text, images=None):
            if images is not None:
                raise TypeError('old')
            raise RuntimeError('really broken')
        session.send_user_message = send
        manager = _FakeManager()
        manager.get_session = lambda task_id: session
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hi'},
        )
        self.assertEqual(response.status_code, 500)

    def test_spawn_chat_session_runner_raises_returns_500(self):
        runner = MagicMock()
        runner.resume_session_for_chat = MagicMock(
            side_effect=RuntimeError('spawn failed'),
        )
        app = create_app(
            session_manager=_FakeManager(),
            planning_session_runner=runner,
        )
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hi'},
        )
        self.assertEqual(response.status_code, 500)
        self.assertIn('spawn failed', response.get_json()['error'])

    def test_spawn_chat_session_success_returns_spawned(self):
        runner = MagicMock()
        runner.resume_session_for_chat = MagicMock(return_value=None)
        app = create_app(
            session_manager=_FakeManager(),
            planning_session_runner=runner,
        )
        response = app.test_client().post(
            '/api/sessions/T-1/messages', json={'text': 'hi'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'spawned')


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/permission — error paths + hook wiring
# ---------------------------------------------------------------------------


class PermissionEndpointTests(unittest.TestCase):
    def _live_session(self):
        session = MagicMock()
        session.is_alive = True
        session.send_permission_response = MagicMock()
        return session

    def _manager_with_session(self, session):
        manager = _FakeManager(records=[_FakeRecord(task_id='T-1')])
        manager.get_session = lambda task_id: session
        return manager

    def test_409_when_no_live_session(self):
        manager = _FakeManager()
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': True},
        )
        self.assertEqual(response.status_code, 409)

    def test_400_when_request_id_missing(self):
        session = self._live_session()
        app = create_app(session_manager=self._manager_with_session(session))
        response = app.test_client().post(
            '/api/sessions/T-1/permission', json={'allow': True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('request_id', response.get_json()['error'])

    def test_500_when_send_raises(self):
        session = self._live_session()
        session.send_permission_response = MagicMock(
            side_effect=RuntimeError('send failed'),
        )
        app = create_app(session_manager=self._manager_with_session(session))
        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': True},
        )
        self.assertEqual(response.status_code, 500)
        self.assertIn('send failed', response.get_json()['error'])

    def test_success_with_no_hook_runner(self):
        session = self._live_session()
        app = create_app(session_manager=self._manager_with_session(session))
        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': True, 'rationale': 'ok'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'delivered')
        self.assertTrue(payload['allow'])

    def test_deny_path_skips_pre_tool_use_hook(self):
        # When ``allow=False`` the pre-tool hook is skipped (no flip).
        session = self._live_session()
        runner = MagicMock()
        # Configure runner.fire so if it were called it would block —
        # we want to ensure it's NOT called.
        runner.fire = MagicMock(return_value=[])
        runner.is_blocked = MagicMock(return_value=False)
        app = create_app(
            session_manager=self._manager_with_session(session),
            hook_runner=runner,
        )
        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': False},
        )
        self.assertEqual(response.status_code, 200)
        # Only post_tool_use should have been called (1 invocation),
        # not pre_tool_use.
        self.assertEqual(runner.fire.call_count, 1)


# ---------------------------------------------------------------------------
# Internal helpers for SSE / events generator
# ---------------------------------------------------------------------------


class EventStreamGeneratorTests(unittest.TestCase):
    """Cover the ``_event_stream_generator`` 'no record / no workspace'
    branch — the only safe-to-test branch (the live tail is an infinite
    poll). The session-missing path emits one event then returns."""

    def test_returns_session_missing_for_unknown_task(self):
        from kato_webserver.app import _event_stream_generator

        manager = _FakeManager()
        chunks = list(_event_stream_generator(manager, None, 'UNKNOWN'))
        self.assertEqual(len(chunks), 1)
        self.assertIn('session_missing', chunks[0])

    def test_returns_session_idle_when_record_exists_no_live(self):
        from kato_webserver.app import _event_stream_generator

        manager = _FakeManager(records=[_FakeRecord(task_id='T-1')])
        # Live session = None, no agent service to drain.
        chunks = list(_event_stream_generator(manager, None, 'T-1'))
        # Some history events may be empty; the last meaningful event is idle.
        joined = ''.join(chunks)
        self.assertIn('session_idle', joined)


# ---------------------------------------------------------------------------
# _follow_live_session — covers the "session_closed" / final-tail branch
# ---------------------------------------------------------------------------


class FollowLiveSessionTests(unittest.TestCase):
    def test_closed_session_drains_tail_then_emits_closed(self):
        from kato_webserver.app import _follow_live_session

        class _ClosedSession:
            is_alive = False
            def events_after(self, start_index):
                # Return no new events; the helper will then hit the
                # ``is_alive == False`` branch + drain a final tail
                # (empty) + emit ``session_closed``.
                return ([], start_index)

        chunks = list(_follow_live_session(_ClosedSession()))
        joined = ''.join(chunks)
        self.assertIn('session_closed', joined)


# ---------------------------------------------------------------------------
# Pending-permission session probe helpers
# ---------------------------------------------------------------------------


class PendingPermissionProbeTests(unittest.TestCase):
    def test_live_probe_returns_tool_name(self):
        from kato_webserver.app import _session_pending_permission_tool

        session = SimpleNamespace(
            pending_control_request_tool=lambda: 'Bash',
        )
        self.assertEqual(_session_pending_permission_tool(session), 'Bash')

    def test_live_probe_empty_string_returns_empty(self):
        from kato_webserver.app import _session_pending_permission_tool

        session = SimpleNamespace(
            pending_control_request_tool=lambda: '',
        )
        self.assertEqual(_session_pending_permission_tool(session), '')

    def test_live_probe_raises_falls_back_to_empty_string(self):
        from kato_webserver.app import _session_pending_permission_tool

        def boom():
            raise RuntimeError('probe failed')
        session = SimpleNamespace(
            pending_control_request_tool=boom,
        )
        self.assertEqual(_session_pending_permission_tool(session), '')

    def test_history_walk_matches_control_request(self):
        from kato_webserver.app import _session_pending_permission_tool
        from claude_core_lib.claude_core_lib.session.wire_protocol import (
            CLAUDE_EVENT_CONTROL_REQUEST,
        )

        event = SimpleNamespace(
            raw={
                'type': CLAUDE_EVENT_CONTROL_REQUEST,
                'tool_name': 'Edit',
            },
        )
        session = SimpleNamespace(
            recent_events=lambda: [event],
        )
        self.assertEqual(_session_pending_permission_tool(session), 'Edit')

    def test_history_walk_finds_tool_in_nested_request(self):
        from kato_webserver.app import _session_pending_permission_tool
        from claude_core_lib.claude_core_lib.session.wire_protocol import (
            CLAUDE_EVENT_PERMISSION_REQUEST,
        )

        event = SimpleNamespace(
            raw={
                'type': CLAUDE_EVENT_PERMISSION_REQUEST,
                'request': {'tool_name': 'Read'},
            },
        )
        session = SimpleNamespace(
            recent_events=lambda: [event],
        )
        self.assertEqual(_session_pending_permission_tool(session), 'Read')

    def test_history_walk_returns_empty_after_response(self):
        from kato_webserver.app import _session_pending_permission_tool
        from claude_core_lib.claude_core_lib.session.wire_protocol import (
            CLAUDE_EVENT_PERMISSION_RESPONSE,
        )

        event = SimpleNamespace(
            raw={'type': CLAUDE_EVENT_PERMISSION_RESPONSE},
        )
        session = SimpleNamespace(
            recent_events=lambda: [event],
        )
        self.assertEqual(_session_pending_permission_tool(session), '')


# ---------------------------------------------------------------------------
# _live_session_ids / _working_session_ids — error path
# ---------------------------------------------------------------------------


class SessionIdsHelperTests(unittest.TestCase):
    def test_live_session_ids_returns_empty_for_none(self):
        from kato_webserver.app import _live_session_ids
        self.assertEqual(_live_session_ids(None), set())

    def test_live_session_ids_handles_list_records_raising(self):
        from kato_webserver.app import _live_session_ids

        manager = MagicMock()
        manager.list_records.side_effect = RuntimeError('boom')
        self.assertEqual(_live_session_ids(manager), set())

    def test_live_session_ids_handles_get_session_raising_per_record(self):
        from kato_webserver.app import _live_session_ids

        manager = MagicMock()
        manager.list_records.return_value = [_FakeRecord(task_id='T-1')]
        manager.get_session.side_effect = RuntimeError('fail')
        self.assertEqual(_live_session_ids(manager), set())

    def test_working_session_ids_returns_empty_for_none(self):
        from kato_webserver.app import _working_session_ids
        self.assertEqual(_working_session_ids(None), set())

    def test_working_session_ids_handles_list_records_raising(self):
        from kato_webserver.app import _working_session_ids

        manager = MagicMock()
        manager.list_records.side_effect = RuntimeError('boom')
        self.assertEqual(_working_session_ids(manager), set())

    def test_working_session_ids_handles_get_session_raising(self):
        from kato_webserver.app import _working_session_ids

        manager = MagicMock()
        manager.list_records.return_value = [_FakeRecord(task_id='T-1')]
        manager.get_session.side_effect = RuntimeError('boom')
        self.assertEqual(_working_session_ids(manager), set())

    def test_working_session_ids_marks_only_working(self):
        from kato_webserver.app import _working_session_ids

        working_session = SimpleNamespace(is_working=True)
        idle_session = SimpleNamespace(is_working=False)
        manager = MagicMock()
        manager.list_records.return_value = [
            _FakeRecord(task_id='T-1'),
            _FakeRecord(task_id='T-2'),
        ]
        def get_session(task_id):
            if task_id == 'T-1':
                return working_session
            return idle_session
        manager.get_session.side_effect = get_session
        self.assertEqual(_working_session_ids(manager), {'T-1'})

    def test_pending_permission_tool_handles_raises(self):
        from kato_webserver.app import _pending_permission_tool_by_task

        manager = MagicMock()
        manager.list_records.side_effect = RuntimeError('list failed')
        self.assertEqual(_pending_permission_tool_by_task(manager), {})

    def test_pending_permission_tool_skips_session_lookup_errors(self):
        from kato_webserver.app import _pending_permission_tool_by_task

        manager = MagicMock()
        manager.list_records.return_value = [_FakeRecord(task_id='T-1')]
        manager.get_session.side_effect = RuntimeError('boom')
        self.assertEqual(_pending_permission_tool_by_task(manager), {})

    def test_session_ids_by_task_returns_empty_for_none(self):
        from kato_webserver.app import _session_ids_by_task
        self.assertEqual(_session_ids_by_task(None), {})

    def test_session_ids_by_task_handles_raises(self):
        from kato_webserver.app import _session_ids_by_task

        manager = MagicMock()
        manager.list_records.side_effect = RuntimeError('boom')
        self.assertEqual(_session_ids_by_task(manager), {})


# ---------------------------------------------------------------------------
# _chat_resume_context / _chat_additional_dirs error handling
# ---------------------------------------------------------------------------


class ChatResumeContextTests(unittest.TestCase):
    def test_context_handles_session_get_record_raising(self):
        from kato_webserver.app import _chat_resume_context

        session_manager = MagicMock()
        session_manager.get_record.side_effect = RuntimeError('fail')
        cwd, summary = _chat_resume_context(session_manager, None, 'T-1')
        self.assertEqual(cwd, '')
        self.assertEqual(summary, '')

    def test_context_handles_workspace_get_raising(self):
        from kato_webserver.app import _chat_resume_context

        workspace_manager = MagicMock()
        workspace_manager.get.side_effect = RuntimeError('fail')
        cwd, _ = _chat_resume_context(None, workspace_manager, 'T-1')
        self.assertEqual(cwd, '')

    def test_context_falls_back_to_workspace_first_repo(self):
        from kato_webserver.app import _chat_resume_context

        # No session record, workspace exists with cwd empty + repo_ids.
        workspace_manager = MagicMock()
        workspace_manager.get.return_value = SimpleNamespace(
            cwd='', task_summary='', repository_ids=['client'],
        )
        workspace_manager.repository_path.return_value = '/path/to/client'
        cwd, _ = _chat_resume_context(None, workspace_manager, 'T-1')
        self.assertEqual(cwd, '/path/to/client')

    def test_context_workspace_repository_path_raises(self):
        from kato_webserver.app import _chat_resume_context

        workspace_manager = MagicMock()
        workspace_manager.get.return_value = SimpleNamespace(
            cwd='', task_summary='', repository_ids=['client'],
        )
        workspace_manager.repository_path.side_effect = RuntimeError('lookup failed')
        cwd, _ = _chat_resume_context(None, workspace_manager, 'T-1')
        self.assertEqual(cwd, '')


class ChatAdditionalDirsTests(unittest.TestCase):
    def test_returns_empty_when_no_workspace_manager(self):
        from kato_webserver.app import _chat_additional_dirs
        self.assertEqual(_chat_additional_dirs(None, 'T-1', ''), [])

    def test_returns_empty_when_no_task_id(self):
        from kato_webserver.app import _chat_additional_dirs
        ws = _FakeWorkspaceManager()
        self.assertEqual(_chat_additional_dirs(ws, '', ''), [])

    def test_returns_empty_when_get_raises(self):
        from kato_webserver.app import _chat_additional_dirs

        workspace_manager = MagicMock()
        workspace_manager.get.side_effect = RuntimeError('fail')
        self.assertEqual(_chat_additional_dirs(workspace_manager, 'T-1', ''), [])

    def test_returns_empty_when_workspace_missing(self):
        from kato_webserver.app import _chat_additional_dirs

        workspace_manager = MagicMock()
        workspace_manager.get.return_value = None
        self.assertEqual(_chat_additional_dirs(workspace_manager, 'T-1', ''), [])

    def test_extras_skip_cwd_and_dedupe(self):
        from kato_webserver.app import _chat_additional_dirs

        workspace_manager = MagicMock()
        workspace_manager.get.return_value = SimpleNamespace(
            repository_ids=['client', 'backend', 'duplicate'],
        )
        paths = {
            'client': '/ws/client',
            'backend': '/ws/backend',
            'duplicate': '/ws/backend',  # collides with backend
        }
        workspace_manager.repository_path.side_effect = lambda t, r: paths[r]
        # cwd matches /ws/client → skipped.
        extras = _chat_additional_dirs(workspace_manager, 'T-1', '/ws/client')
        self.assertEqual(extras, ['/ws/backend'])

    def test_extras_skips_repository_path_errors(self):
        from kato_webserver.app import _chat_additional_dirs

        workspace_manager = MagicMock()
        workspace_manager.get.return_value = SimpleNamespace(
            repository_ids=['client'],
        )
        workspace_manager.repository_path.side_effect = RuntimeError('boom')
        self.assertEqual(_chat_additional_dirs(workspace_manager, 'T-1', ''), [])


# ---------------------------------------------------------------------------
# /api/scan/trigger
# ---------------------------------------------------------------------------


class TriggerScanTests(unittest.TestCase):
    def test_503_when_no_force_event(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post('/api/scan/trigger')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['status'], 'unavailable')

    def test_returns_scanning_when_in_progress(self):
        import threading
        force_event = threading.Event()
        in_progress = threading.Event()
        in_progress.set()
        app = create_app(
            session_manager=_FakeManager(),
            force_scan_event=force_event,
            scan_in_progress_event=in_progress,
        )
        response = app.test_client().post('/api/scan/trigger')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'scanning')

    def test_triggers_when_not_in_progress(self):
        import threading
        force_event = threading.Event()
        in_progress = threading.Event()  # not set
        app = create_app(
            session_manager=_FakeManager(),
            force_scan_event=force_event,
            scan_in_progress_event=in_progress,
        )
        response = app.test_client().post('/api/scan/trigger')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'triggered')
        self.assertTrue(force_event.is_set())


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/model — POST error paths
# ---------------------------------------------------------------------------


class SessionModelTests(unittest.TestCase):
    def test_get_returns_empty_when_no_override(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().get('/api/sessions/T-1/model')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'model': ''})

    def test_post_sets_override(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post(
            '/api/sessions/T-1/model', json={'model': 'claude-opus-4-7'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(), {'model': 'claude-opus-4-7'},
        )

    def test_post_clears_override_with_empty(self):
        app = create_app(session_manager=_FakeManager())
        # Set then clear.
        app.test_client().post(
            '/api/sessions/T-1/model', json={'model': 'claude-opus-4-7'},
        )
        response = app.test_client().post(
            '/api/sessions/T-1/model', json={'model': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'model': ''})

    def test_post_503_when_no_overrides_dict(self):
        # Force the overrides dict to None — the route falls into 503.
        app = create_app(session_manager=_FakeManager())
        app.config['TASK_MODEL_OVERRIDES'] = None
        response = app.test_client().post(
            '/api/sessions/T-1/model', json={'model': 'x'},
        )
        self.assertEqual(response.status_code, 503)


# ---------------------------------------------------------------------------
# /api/sessions/<task_id>/adopt-agent-session — error / 400 / 409 paths
# ---------------------------------------------------------------------------


class AdoptAgentSessionTests(unittest.TestCase):
    def test_400_when_session_id_missing(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post(
            '/api/sessions/T-1/adopt-agent-session', json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('required', response.get_json()['error'])

    def test_409_when_live_session_running(self):
        live_session = SimpleNamespace(is_alive=True)
        manager = _FakeManager()
        manager.get_session = lambda task_id: live_session
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/adopt-agent-session',
            json={'agent_session_id': 'sess-1'},
        )
        self.assertEqual(response.status_code, 409)

    def test_400_when_adopt_raises_value_error(self):
        manager = MagicMock()
        manager.get_session.return_value = None
        manager.adopt_session_id.side_effect = ValueError('not a uuid')
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/adopt-agent-session',
            json={'agent_session_id': 'sess-1'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('not a uuid', response.get_json()['error'])

    def test_409_when_adopt_raises_runtime_error(self):
        manager = MagicMock()
        manager.get_session.return_value = None
        manager.adopt_session_id.side_effect = RuntimeError('conflict')
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/T-1/adopt-agent-session',
            json={'agent_session_id': 'sess-1'},
        )
        self.assertEqual(response.status_code, 409)


if __name__ == '__main__':
    unittest.main()
