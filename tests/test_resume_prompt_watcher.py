"""Tests for the ``ResumePromptWatcher`` polling service.

The watcher iterates live sessions every tick, detects new
``result`` events in ``session.recent_events()``, and rewrites
``<workspace>/resume_prompt.md`` for those tasks. All stand-ins
here are concrete classes — no MagicMock — so the test pins the
exact contract the watcher consumes.
"""
from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.session_id_utils import AGENT_SESSION_ID
from kato_core_lib.data_layers.service.resume_prompt_watcher import (
    ResumePromptWatcher,
)
from kato_core_lib.helpers.resume_prompt_writer import RESUME_PROMPT_FILENAME


class _Event(object):
    """Concrete stand-in for ``SessionEvent`` — just the two attrs the watcher reads."""

    def __init__(self, event_type: str, raw: dict | None = None) -> None:
        self.event_type = event_type
        self.raw = raw or {}


class _FakeSession(object):
    """Minimal session: provides recent_events() returning a mutable list."""

    def __init__(self, events: list | None = None) -> None:
        self._events = list(events or [])

    def recent_events(self) -> list:
        return list(self._events)

    def append(self, event: _Event) -> None:
        self._events.append(event)


class _FakeRecord(object):
    def __init__(self, task_id: str, **kwargs) -> None:
        self.task_id = task_id
        self.task_summary = kwargs.get('task_summary', '')
        self.expected_branch = kwargs.get('expected_branch', '')
        self.agent_session_id = kwargs.get(AGENT_SESSION_ID, '')


class _FakeSessionManager(object):
    """Holds a dict of {task_id: session} and exposes the manager surface the watcher uses."""

    def __init__(self) -> None:
        self._sessions: dict[str, _FakeSession] = {}
        self._records: dict[str, _FakeRecord] = {}

    def add(self, task_id: str, session: _FakeSession, **record_kwargs) -> None:
        self._sessions[task_id] = session
        self._records[task_id] = _FakeRecord(task_id, **record_kwargs)

    def list_records(self) -> list:
        return list(self._records.values())

    def get_session(self, task_id: str):
        return self._sessions.get(task_id)


class _FakeWorkspace(object):
    def __init__(self, repository_ids: list) -> None:
        self.repository_ids = list(repository_ids)


class _FakeWorkspaceManager(object):
    """Workspace manager rooted at a real tempdir so writes land on disk."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._workspaces: dict[str, _FakeWorkspace] = {}

    def add(self, task_id: str, repository_ids: list) -> Path:
        self._workspaces[task_id] = _FakeWorkspace(repository_ids)
        path = self.workspace_path(task_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace_path(self, task_id: str) -> Path:
        return self._root / task_id

    def repository_path(self, task_id: str, repository_id: str) -> Path:
        return self.workspace_path(task_id) / repository_id

    def get(self, task_id: str):
        return self._workspaces.get(task_id)


class ResumePromptWatcherTickTests(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.sessions = _FakeSessionManager()
        self.workspaces = _FakeWorkspaceManager(self.root)
        self.watcher = ResumePromptWatcher(
            session_manager=self.sessions,
            workspace_manager=self.workspaces,
            tick_seconds=1.0,
        )

    def _resume_path(self, task_id: str) -> Path:
        return self.workspaces.workspace_path(task_id) / RESUME_PROMPT_FILENAME

    def test_tick_writes_nothing_when_no_sessions(self) -> None:
        self.assertEqual(self.watcher.tick(), 0)

    def test_tick_writes_nothing_when_session_has_no_result_event(self) -> None:
        # Session has events but no terminal ``result`` yet — turn is
        # still in flight. The file should NOT be rewritten until the
        # turn ends.
        session = _FakeSession([
            _Event('user', {'message': {
                'role': 'user', 'content': 'fix it',
            }}),
            _Event('assistant', {'message': {
                'content': [{'type': 'text', 'text': 'working on it'}],
            }}),
        ])
        self.sessions.add('T1', session)
        self.workspaces.add('T1', ['client'])
        self.assertEqual(self.watcher.tick(), 0)
        self.assertFalse(self._resume_path('T1').exists())

    def test_tick_writes_file_when_result_event_appears(self) -> None:
        # Mid-turn snapshot: no result yet.
        session = _FakeSession([
            _Event('user', {'message': {'role': 'user', 'content': 'fix it'}}),
            _Event('assistant', {'message': {
                'content': [{'type': 'text', 'text': 'edited line 12'}],
            }}),
        ])
        self.sessions.add('T1', session, task_summary='Fix typo',
                          expected_branch='feature/t1')
        self.workspaces.add('T1', ['client'])
        self.assertEqual(self.watcher.tick(), 0)
        # Turn ends → result event lands.
        session.append(_Event('result', {'is_error': False, 'result': 'done'}))
        self.assertEqual(self.watcher.tick(), 1)
        path = self._resume_path('T1')
        self.assertTrue(path.is_file())
        body = path.read_text()
        self.assertIn('# Resume prompt for T1', body)
        self.assertIn('Fix typo', body)
        self.assertIn('feature/t1', body)
        self.assertIn('edited line 12', body)

    def test_second_tick_after_same_turn_does_not_rewrite(self) -> None:
        # ``mtime`` would refresh on a rewrite — pin first-tick mtime
        # and prove the second tick leaves the file untouched.
        session = _FakeSession([
            _Event('assistant', {'message': {
                'content': [{'type': 'text', 'text': 'done'}],
            }}),
            _Event('result', {'is_error': False, 'result': 'done'}),
        ])
        self.sessions.add('T1', session)
        self.workspaces.add('T1', ['client'])
        self.assertEqual(self.watcher.tick(), 1)
        path = self._resume_path('T1')
        first_mtime = path.stat().st_mtime_ns
        self.assertEqual(self.watcher.tick(), 0)
        self.assertEqual(path.stat().st_mtime_ns, first_mtime)

    def test_new_result_event_triggers_rewrite(self) -> None:
        session = _FakeSession([
            _Event('assistant', {'message': {
                'content': [{'type': 'text', 'text': 'first turn'}],
            }}),
            _Event('result', {'is_error': False, 'result': 'done'}),
        ])
        self.sessions.add('T1', session)
        self.workspaces.add('T1', ['client'])
        self.assertEqual(self.watcher.tick(), 1)
        path = self._resume_path('T1')
        body_first = path.read_text()
        # Second turn comes in.
        session.append(_Event('assistant', {'message': {
            'content': [{'type': 'text', 'text': 'second turn'}],
        }}))
        session.append(_Event('result', {'is_error': False, 'result': 'done'}))
        self.assertEqual(self.watcher.tick(), 1)
        body_second = path.read_text()
        self.assertNotEqual(body_first, body_second)
        self.assertIn('second turn', body_second)

    def test_multi_session_writes_separate_files(self) -> None:
        for task_id in ('T1', 'T2'):
            session = _FakeSession([
                _Event('assistant', {'message': {
                    'content': [{'type': 'text', 'text': f'{task_id} done'}],
                }}),
                _Event('result', {'is_error': False, 'result': 'done'}),
            ])
            self.sessions.add(task_id, session)
            self.workspaces.add(task_id, ['client'])
        self.assertEqual(self.watcher.tick(), 2)
        self.assertIn('T1 done', self._resume_path('T1').read_text())
        self.assertIn('T2 done', self._resume_path('T2').read_text())

    def test_tick_handles_missing_session_gracefully(self) -> None:
        # Record present but session is None (e.g. subprocess died,
        # record persists). Must not crash.
        self.sessions.add('T1', None)
        self.workspaces.add('T1', ['client'])
        self.assertEqual(self.watcher.tick(), 0)

    def test_tick_handles_session_exception_in_recent_events(self) -> None:
        class _BoomSession(object):
            def recent_events(self):
                raise RuntimeError('snapshot blew up')
        self.sessions.add('T1', _BoomSession())
        self.workspaces.add('T1', ['client'])
        # No crash, no write.
        self.assertEqual(self.watcher.tick(), 0)

    def test_tick_with_no_workspace_manager_is_safe(self) -> None:
        watcher = ResumePromptWatcher(
            session_manager=self.sessions,
            workspace_manager=None,
            tick_seconds=1.0,
        )
        session = _FakeSession([
            _Event('result', {'is_error': False, 'result': 'done'}),
        ])
        self.sessions.add('T1', session)
        self.assertEqual(watcher.tick(), 0)

    def test_tick_with_no_session_manager_is_safe(self) -> None:
        watcher = ResumePromptWatcher(
            session_manager=None,
            workspace_manager=self.workspaces,
            tick_seconds=1.0,
        )
        self.assertEqual(watcher.tick(), 0)


class ResumePromptWatcherLifecycleTests(unittest.TestCase):
    """``start()`` / ``stop()`` thread management."""

    def test_start_and_stop_cycle_does_not_raise(self) -> None:
        sessions = _FakeSessionManager()
        with tempfile.TemporaryDirectory() as td:
            watcher = ResumePromptWatcher(
                session_manager=sessions,
                workspace_manager=_FakeWorkspaceManager(Path(td)),
                tick_seconds=0.5,
            )
            watcher.start()
            self.assertIsNotNone(watcher._thread)
            self.assertTrue(watcher._thread.is_alive())
            watcher.stop(timeout=1.0)
            self.assertIsNone(watcher._thread)

    def test_start_is_idempotent(self) -> None:
        # Double-start should not spawn a second thread.
        sessions = _FakeSessionManager()
        with tempfile.TemporaryDirectory() as td:
            watcher = ResumePromptWatcher(
                session_manager=sessions,
                workspace_manager=_FakeWorkspaceManager(Path(td)),
                tick_seconds=0.5,
            )
            watcher.start()
            first_thread = watcher._thread
            watcher.start()
            self.assertIs(watcher._thread, first_thread)
            watcher.stop(timeout=1.0)

    def test_stop_without_start_is_safe(self) -> None:
        watcher = ResumePromptWatcher(
            session_manager=_FakeSessionManager(),
        )
        # Doesn't raise.
        watcher.stop(timeout=0.1)


class ResumePromptWatcherDefensivePathsTests(unittest.TestCase):
    """Coverage for the defensive guards in ``_list_sessions``,
    ``_records_by_task``, ``_workspace_path_for``, ``_repository_paths``
    and the ``_run_loop`` exception swallow. These guard against the
    many ways a partially-initialized manager / workspace_manager can
    show up in tests, headless boots, and during shutdown."""

    def _make_watcher(self, *, session_manager=None, workspace_manager=None):
        return ResumePromptWatcher(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            tick_seconds=1.0,
        )

    def test_list_sessions_returns_empty_when_manager_lacks_methods(self) -> None:
        # Manager is non-None but missing ``list_records`` / ``get_session``
        # → list_sessions returns [].
        class _Stub(object):
            pass
        watcher = self._make_watcher(session_manager=_Stub())
        self.assertEqual(watcher._list_sessions(), [])

    def test_list_sessions_swallows_list_records_exception(self) -> None:
        class _Boom(object):
            def list_records(self):
                raise RuntimeError('boom')
            def get_session(self, task_id):
                return None
        watcher = self._make_watcher(session_manager=_Boom())
        self.assertEqual(watcher._list_sessions(), [])

    def test_list_sessions_skips_blank_task_ids(self) -> None:
        class _Mgr(object):
            def list_records(self):
                return [SimpleNamespace(task_id=''),
                        SimpleNamespace(task_id='T1')]
            def get_session(self, task_id):
                return SimpleNamespace(name=f'sess-{task_id}')
        watcher = self._make_watcher(session_manager=_Mgr())
        sessions = watcher._list_sessions()
        # Blank task_id dropped; only T1 remains.
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0][0], 'T1')

    def test_list_sessions_swallows_get_session_exception(self) -> None:
        class _Mgr(object):
            def list_records(self):
                return [SimpleNamespace(task_id='T1')]
            def get_session(self, task_id):
                raise RuntimeError('get_session failed')
        watcher = self._make_watcher(session_manager=_Mgr())
        sessions = watcher._list_sessions()
        # Entry still added with session=None.
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0][0], 'T1')
        self.assertIsNone(sessions[0][1])

    def test_records_by_task_returns_empty_when_manager_is_none(self) -> None:
        watcher = self._make_watcher(session_manager=None)
        self.assertEqual(watcher._records_by_task(), {})

    def test_records_by_task_returns_empty_when_list_records_missing(self) -> None:
        class _Stub(object):
            pass
        watcher = self._make_watcher(session_manager=_Stub())
        self.assertEqual(watcher._records_by_task(), {})

    def test_records_by_task_swallows_list_records_exception(self) -> None:
        class _Boom(object):
            def list_records(self):
                raise RuntimeError('boom')
        watcher = self._make_watcher(session_manager=_Boom())
        self.assertEqual(watcher._records_by_task(), {})

    def test_workspace_path_for_returns_none_when_wm_missing(self) -> None:
        watcher = self._make_watcher(workspace_manager=None)
        self.assertIsNone(watcher._workspace_path_for('T1'))

    def test_workspace_path_for_returns_none_when_callable_missing(self) -> None:
        # workspace_manager exists but has no ``workspace_path`` attribute.
        class _Stub(object):
            pass
        watcher = self._make_watcher(workspace_manager=_Stub())
        self.assertIsNone(watcher._workspace_path_for('T1'))

    def test_workspace_path_for_swallows_exception(self) -> None:
        class _BoomWM(object):
            def workspace_path(self, task_id):
                raise RuntimeError('blew up')
        watcher = self._make_watcher(workspace_manager=_BoomWM())
        self.assertIsNone(watcher._workspace_path_for('T1'))

    def test_repository_paths_empty_when_wm_none(self) -> None:
        watcher = self._make_watcher(workspace_manager=None)
        self.assertEqual(watcher._repository_paths('T1'), [])

    def test_repository_paths_empty_when_callables_missing(self) -> None:
        class _Stub(object):
            pass
        watcher = self._make_watcher(workspace_manager=_Stub())
        self.assertEqual(watcher._repository_paths('T1'), [])

    def test_repository_paths_swallows_get_workspace_exception(self) -> None:
        class _BoomWM(object):
            def get(self, task_id):
                raise RuntimeError('get failed')
            def repository_path(self, task_id, repo_id):
                return Path(f'/x/{task_id}/{repo_id}')
        watcher = self._make_watcher(workspace_manager=_BoomWM())
        self.assertEqual(watcher._repository_paths('T1'), [])

    def test_repository_paths_empty_when_workspace_none(self) -> None:
        # ``get`` returns None for an unknown task → empty list.
        class _WM(object):
            def get(self, task_id):
                return None
            def repository_path(self, task_id, repo_id):
                return Path(f'/x/{task_id}/{repo_id}')
        watcher = self._make_watcher(workspace_manager=_WM())
        self.assertEqual(watcher._repository_paths('T1'), [])

    def test_repository_paths_swallows_repo_path_exception(self) -> None:
        # ``get`` returns a workspace with repo_ids, but repository_path
        # blows up for one of them → that one is skipped, the rest are kept.
        class _WS(object):
            repository_ids = ['good', 'bad']

        class _WM(object):
            def get(self, task_id):
                return _WS()

            def repository_path(self, task_id, repo_id):
                if repo_id == 'bad':
                    raise RuntimeError('repo path failed')
                return Path(f'/x/{task_id}/{repo_id}')

        watcher = self._make_watcher(workspace_manager=_WM())
        paths = watcher._repository_paths('T1')
        self.assertEqual(len(paths), 1)
        self.assertIn('good', paths[0])

    def test_run_loop_swallows_tick_exception(self) -> None:
        # Lines 95-98: tick() raises → ``self.logger.exception`` is
        # called and the loop continues until the stop_event fires.
        watcher = self._make_watcher(session_manager=None)
        watcher._tick_seconds = 0.01

        with unittest.mock.patch.object(
            watcher, 'tick', side_effect=RuntimeError('tick boom'),
        ), unittest.mock.patch.object(
            watcher, 'logger',
        ) as mock_logger:
            # Drive the loop manually: ask it to stop almost immediately,
            # then run the loop on the calling thread.
            import threading as _th
            stop_thread = _th.Thread(
                target=lambda: (
                    _th.Event().wait(0.05) or watcher._stop_event.set()
                ),
            )
            stop_thread.start()
            watcher._run_loop()
            stop_thread.join(timeout=1.0)
            mock_logger.exception.assert_called()


if __name__ == '__main__':
    unittest.main()
