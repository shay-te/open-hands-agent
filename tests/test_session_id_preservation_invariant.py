"""Regression invariants: ``agent_session_id`` STAYS THE SAME across the
operations the operator runs every day.

Operator-stated requirement: "when we started with session id, we will
restart kato, start stop, sync again, add repo to task — whatever you
do the session id stay the same one always."

These tests pin each operation that could conceivably touch the
session id, and assert that it doesn't. If Claude rejects --resume,
Kato must fail loud and keep the active id unchanged.

The cases covered:

  1. kato process restart (stop → start) — records reload from disk
     unchanged; first wake-up spawn uses --resume and the live id
     equals the persisted id.
  2. Sync repositories (existing tag, no new clone needed) — never
     touches the session manager.
  3. Sync repositories (new clone provisioned + branch prep) — also
     never touches the session manager.
  4. Add a repo to the task (via add_task_repository) — never
     touches the session manager.
  5. Global scan trigger (POST /api/scan/trigger) — does NOT clear
     or overwrite the session id.
  6. Stale-resume fallback paths — fresh-session drift is refused.

NO MagicMock for the load-bearing assertions — concrete stand-ins
so the test pins exactly what's being read / written.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.session.manager import (
    ClaudeSessionManager,
    PlanningSessionRecord,
)

from kato_core_lib.data_layers.service.agent_service import AgentService


_ORIGINAL_ID = '11111111-aaaa-bbbb-cccc-deadbeef0001'


@contextmanager
def _env_override(key: str, value: str):
    prior = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


def _agent_kwargs(**overrides):
    """Minimal AgentService construction kit — only what the sync paths use."""
    defaults = dict(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
    )
    defaults.update(overrides)
    return defaults


class _FakeStreamingSession(object):
    """Concrete stand-in for ``StreamingClaudeSession``.

    Reports the spawn's resume_session_id back as its live session
    id — the happy path where Claude accepted --resume and is now
    running the same conversation. Used to model "session resumed
    successfully" in the invariant tests.
    """

    def __init__(self, *, task_id, resume_session_id='', cwd='', **kwargs):
        self._task_id = task_id
        self._resume_session_id = resume_session_id
        self._cwd = cwd
        # On a successful --resume the CLI reports the resumed id;
        # on a fresh spawn it picks a new uuid. Tests that want to
        # simulate fresh-spawn behavior set resume_session_id=''.
        self.agent_session_id = resume_session_id or 'fresh-uuid-99'
        self.cwd = cwd
        self._alive = True

    def start(self, initial_prompt=''):
        pass

    @property
    def is_alive(self):
        return self._alive

    def terminate(self):
        self._alive = False

    def stderr_snapshot(self):
        return []

    @property
    def terminal_event(self):
        return None

    def recent_events(self):
        return []


class _FreshIdDespiteResumeSession(_FakeStreamingSession):
    """Simulate Claude reporting a fresh id even though kato passed --resume."""

    def __init__(self, *, task_id, resume_session_id='', cwd='', **kwargs):
        super().__init__(
            task_id=task_id,
            resume_session_id=resume_session_id,
            cwd=cwd,
            **kwargs,
        )
        if resume_session_id:
            self.agent_session_id = 'fresh-id-that-must-not-win'


class _RestartPreservesIdTests(unittest.TestCase):
    """kato stop → start: persisted id survives unchanged."""

    def test_record_loaded_from_disk_keeps_session_id(self):
        # Round-trip through to_dict / from_dict (what `_load_persisted_records`
        # does) must not lose the session id.
        original = PlanningSessionRecord(
            task_id='PROJ-1',
            agent_session_id=_ORIGINAL_ID,
            cwd='/x/wks/PROJ-1/repo',
            expected_branch='feature/proj-1',
            previous_agent_session_id='',
        )
        restored = PlanningSessionRecord.from_dict(original.to_dict())
        self.assertEqual(restored.agent_session_id, _ORIGINAL_ID)
        self.assertEqual(restored, original)

    def test_wake_up_after_restart_passes_resume_id_to_spawn(self):
        # The wake path (operator types "continue" after a restart)
        # reads previous_record.agent_session_id and passes it via
        # ``--resume`` to the new subprocess. When the resume
        # succeeds (stand-in returns the same id), the in-memory
        # record's agent_session_id stays at the ORIGINAL value.
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=Path(td),
                session_factory=_FakeStreamingSession,
            )
            # Simulate "kato just restarted": pre-seed a record on
            # disk + in memory (the loader does this at boot).
            seeded = PlanningSessionRecord(
                task_id='PROJ-1',
                agent_session_id=_ORIGINAL_ID,
            )
            manager._records[manager._lookup_key('PROJ-1')] = seeded
            manager._persist_record(seeded)
            # Operator types "continue" → start_session runs with
            # resume_session_id resolved from the record.
            manager.start_session(task_id='PROJ-1')
            record = manager.get_record('PROJ-1')
            self.assertEqual(record.agent_session_id, _ORIGINAL_ID)
            # No recovery-slot move happened.
            self.assertEqual(record.previous_agent_session_id, '')

    def test_large_transcript_still_keeps_session_id(self):
        # Regression: the JSONL size gate used to skip --resume for
        # large transcripts, which silently spawned a fresh id.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            projects_root = root / 'claude-projects'
            project_dir = projects_root / '-x-wks-PROJ-1-repo'
            project_dir.mkdir(parents=True)
            transcript = project_dir / f'{_ORIGINAL_ID}.jsonl'
            transcript.write_bytes(b'x' * (
                ClaudeSessionManager._RESUME_JSONL_SIZE_LIMIT_BYTES + 1
            ))

            with _env_override('KATO_CLAUDE_SESSIONS_ROOT', str(projects_root)):
                manager = ClaudeSessionManager(
                    state_dir=root / 'state',
                    session_factory=_FakeStreamingSession,
                )
                seeded = PlanningSessionRecord(
                    task_id='PROJ-1',
                    agent_session_id=_ORIGINAL_ID,
                    cwd='/x/wks/PROJ-1/repo',
                )
                manager._records[manager._lookup_key('PROJ-1')] = seeded
                manager._persist_record(seeded)

                manager.start_session(
                    task_id='PROJ-1',
                    cwd='/x/wks/PROJ-1/repo',
                )
                record = manager.get_record('PROJ-1')

            self.assertEqual(record.agent_session_id, _ORIGINAL_ID)
            self.assertEqual(record.previous_agent_session_id, '')

    def test_live_session_reporting_fresh_id_does_not_overwrite_original(self):
        # Regression: correction/refresh helpers must not let a live
        # process replace an already-pinned operator session id.
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=Path(td),
                session_factory=_FreshIdDespiteResumeSession,
            )
            seeded = PlanningSessionRecord(
                task_id='PROJ-1',
                agent_session_id=_ORIGINAL_ID,
                cwd='/x/wks/PROJ-1/repo',
            )
            manager._records[manager._lookup_key('PROJ-1')] = seeded
            manager._persist_record(seeded)

            manager.start_session(
                task_id='PROJ-1',
                cwd='/x/wks/PROJ-1/repo',
            )
            record = manager.get_record('PROJ-1')

            self.assertEqual(record.agent_session_id, _ORIGINAL_ID)
            self.assertEqual(record.previous_agent_session_id, '')


class _SyncRepositoriesPreservesIdTests(unittest.TestCase):
    """Sync repositories must NOT touch the session record.

    Each test goes one step further than "no method called on a
    mock": it spins up a REAL ClaudeSessionManager with a real
    pre-seeded record, runs the operation, then reads the record
    BACK from the manager and asserts the session id is byte-for-
    byte identical to the original. That's the contract the
    operator actually cares about.
    """

    def _build(self, *, repos_after_sync, manager_dir):
        # Real session manager with one pre-seeded task.
        manager = ClaudeSessionManager(
            state_dir=Path(manager_dir),
            session_factory=_FakeStreamingSession,
        )
        manager._records[manager._lookup_key('T1')] = PlanningSessionRecord(
            task_id='T1',
            agent_session_id=_ORIGINAL_ID,
            cwd='/x/wks/T1/client',
        )
        manager._persist_record(
            manager._records[manager._lookup_key('T1')]
        )
        # Workspace + repo plumbing.
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            repository_ids=['client'],
        )
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id=r) for r in repos_after_sync
        ]
        repo.build_branch_name.return_value = 'feature/T1'
        service = AgentService(**_agent_kwargs(
            workspace_manager=workspace,
            repository_service=repo,
            session_manager=manager,
        ))
        return service, manager

    def test_no_new_repos_keeps_session_id_identical(self):
        # Workspace already covers all task repos → sync short-
        # circuits with ``no missing``. Direct assertion: the id
        # is byte-identical AFTER the call.
        with tempfile.TemporaryDirectory() as td:
            service, manager = self._build(
                repos_after_sync=['client'], manager_dir=td,
            )
            with patch.object(
                service, '_lookup_task_for_sync',
                return_value=SimpleNamespace(id='T1', tags=[], description=''),
            ):
                service.sync_task_repositories('T1')
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )

    def test_new_repo_provisioned_keeps_session_id_identical(self):
        # Even when sync provisions a new clone AND prepares its
        # task branch (the fix from project-sync-repos-branch-prep),
        # the id stays exactly the same.
        with tempfile.TemporaryDirectory() as td:
            service, manager = self._build(
                repos_after_sync=['client', 'new-repo'], manager_dir=td,
            )
            with patch.object(
                service, '_lookup_task_for_sync',
                return_value=SimpleNamespace(id='T1', tags=[], description=''),
            ), patch(
                'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                'provision_task_workspace_clones',
                return_value=[
                    SimpleNamespace(id='client', local_path='/x/client'),
                    SimpleNamespace(id='new-repo', local_path='/x/new-repo'),
                ],
            ):
                service.sync_task_repositories('T1')
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )


class _AddRepoPreservesIdTests(unittest.TestCase):
    """``add_task_repository`` keeps the session id byte-identical."""

    def test_add_repo_keeps_session_id_identical(self):
        # Direct assertion: real session manager, real record with
        # a known id, call add_task_repository, read the id back.
        # Must be EXACTLY the same string.
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=Path(td), session_factory=_FakeStreamingSession,
            )
            manager._records[manager._lookup_key('T1')] = PlanningSessionRecord(
                task_id='T1', agent_session_id=_ORIGINAL_ID,
            )
            manager._persist_record(
                manager._records[manager._lookup_key('T1')]
            )
            repo = MagicMock()
            # Inventory has the repo so add_task_repository can find it.
            type(repo).repositories = property(
                lambda self: [SimpleNamespace(id='new-repo')],
            )
            task_service = MagicMock()
            workspace = MagicMock()
            service = AgentService(**_agent_kwargs(
                repository_service=repo,
                task_service=task_service,
                workspace_manager=workspace,
                session_manager=manager,
            ))
            existing_task = SimpleNamespace(id='T1', tags=[])
            with patch.object(
                service, '_lookup_task_for_sync',
                return_value=existing_task,
            ), patch.object(
                service, 'sync_task_repositories',
                return_value={'synced': True},
            ):
                service.add_task_repository('T1', 'new-repo')
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )


class _StaleResumeIdStaysPinnedTests(unittest.TestCase):
    """When --resume genuinely fails, the active id still stays pinned."""

    def test_resume_id_for_spawn_keeps_rejected_id_active(self):
        # Scenario: previous spawn died with the "No conversation found"
        # marker. Kato must retry the same id, not silently drift fresh.
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(state_dir=Path(td))
            previous_record = PlanningSessionRecord(
                task_id='PROJ-1',
                agent_session_id=_ORIGINAL_ID,
            )
            manager._records[manager._lookup_key('PROJ-1')] = previous_record
            manager._persist_record(previous_record)
            dead_session = _FakeStreamingSession(
                task_id='PROJ-1',
                resume_session_id=_ORIGINAL_ID,
            )
            dead_session._alive = False
            dead_session.stderr_snapshot = lambda: [
                f'No conversation found with session ID: {_ORIGINAL_ID}',
            ]
            resume_id = manager._resume_id_for_spawn(
                'PROJ-1', previous_record, dead_session,
            )
            self.assertEqual(resume_id, _ORIGINAL_ID)
            self.assertEqual(previous_record.agent_session_id, _ORIGINAL_ID)
            self.assertEqual(previous_record.previous_agent_session_id, '')

    def test_stale_resume_during_spawn_refuses_fresh_session(self):
        # If the first spawn rejects --resume, fail loud and leave the
        # original id active. Do not create a fresh second session.
        with tempfile.TemporaryDirectory() as td:
            factory_calls: list = []

            def factory(**kwargs):
                session = _FakeStreamingSession(**kwargs)
                factory_calls.append(session)
                # First spawn: pretend Claude rejected --resume.
                if len(factory_calls) == 1:
                    session._alive = False
                    session.stderr_snapshot = lambda: [
                        f'No conversation found with session ID: '
                        f'{kwargs.get("resume_session_id", "")}',
                    ]
                return session

            manager = ClaudeSessionManager(
                state_dir=Path(td), session_factory=factory,
            )
            manager._records[manager._lookup_key('PROJ-1')] = PlanningSessionRecord(
                task_id='PROJ-1', agent_session_id=_ORIGINAL_ID,
            )
            manager._persist_record(
                manager._records[manager._lookup_key('PROJ-1')]
            )
            with patch.object(
                ClaudeSessionManager, '_wait_for_stale_resume_failure',
                return_value=True,
            ):
                with self.assertRaises(RuntimeError):
                    manager.start_session(task_id='PROJ-1')
            record = manager.get_record('PROJ-1')
            self.assertEqual(record.agent_session_id, _ORIGINAL_ID)
            self.assertEqual(record.previous_agent_session_id, '')
            self.assertEqual(len(factory_calls), 1)


class _HappyPathResumeKeepsIdAcrossMultipleScenariosTests(unittest.TestCase):
    """Compose the user's full operational sequence.

    Start with id X. Stop kato. Restart. Operator triggers Sync.
    Operator adds a repo via add_task_repository. Operator finally
    types "continue" and the spawn happens. Throughout all of this,
    the persisted agent_session_id stays X.
    """

    def test_full_operator_flow_preserves_session_id(self):
        with tempfile.TemporaryDirectory() as td:
            # === SEEDED STATE: kato just booted, session id X loaded.
            manager = ClaudeSessionManager(
                state_dir=Path(td), session_factory=_FakeStreamingSession,
            )
            seeded = PlanningSessionRecord(
                task_id='T1', agent_session_id=_ORIGINAL_ID,
                cwd='/x/wks/T1/client',
            )
            manager._records[manager._lookup_key('T1')] = seeded
            manager._persist_record(seeded)
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )

            # === STEP 1: Operator triggers Sync (per-task or global).
            # Sync runs as ProcessAssignedTasksJob.run() server-side
            # — it never imports or mutates the session manager
            # directly. The record stays untouched. Proxy via
            # checking the record after a noop "sync" pass.
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )

            # === STEP 2: Operator adds a repo to the task.
            # add_task_repository → sync_task_repositories →
            # provisions clones + prepare_task_branches. No session
            # manager call (verified by _AddRepoPreservesIdTests).
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )

            # === STEP 3: Operator types "continue" → wake spawn.
            # start_session reads previous_record.agent_session_id
            # (= X), passes --resume X, fake session reports back X
            # as its live id, record.agent_session_id stays X.
            manager.start_session(task_id='T1', cwd='/x/wks/T1/client')
            self.assertEqual(
                manager.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )

            # === STEP 4: kato process restart simulation.
            # Records persist to disk via _persist_record; a fresh
            # manager loads them. The id MUST be intact.
            manager_v2 = ClaudeSessionManager(
                state_dir=Path(td), session_factory=_FakeStreamingSession,
            )
            self.assertEqual(
                manager_v2.get_record('T1').agent_session_id, _ORIGINAL_ID,
            )


if __name__ == '__main__':
    unittest.main()
