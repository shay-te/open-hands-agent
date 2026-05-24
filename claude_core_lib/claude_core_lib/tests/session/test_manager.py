from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.session.manager import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_DONE,
    ClaudeSessionManager,
    PlanningSessionRecord,
)


class _FakeStreamingSession:
    """Stand-in for StreamingClaudeSession used by the manager tests."""

    def __init__(self, **kwargs) -> None:
        self.task_id = kwargs['task_id']
        self.resume_session_id = kwargs.get('resume_session_id', '')
        self._cwd = kwargs.get('cwd', '/tmp/repo') or '/tmp/repo'
        self._agent_session_id = (
            self.resume_session_id or 'fake-session-' + self.task_id
        )
        self._alive = True
        self.start_calls: list[str] = []
        self.terminate_calls = 0

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def agent_session_id(self) -> str:
        return self._agent_session_id

    @property
    def is_alive(self) -> bool:
        return self._alive

    def start(self, initial_prompt: str = '') -> None:
        self.start_calls.append(initial_prompt)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._alive = False


class ClaudeSessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name)
        self._fakes: list[_FakeStreamingSession] = []

        def factory(**kwargs):
            session = _FakeStreamingSession(**kwargs)
            self._fakes.append(session)
            return session

        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )

    def test_start_session_creates_record_and_persists_to_disk(self) -> None:
        session = self.manager.start_session(
            task_id='PROJ-1',
            task_summary='profile page user section',
            initial_prompt='plan the change',
        )

        # session was started exactly once
        self.assertEqual(session.start_calls, ['plan the change'])

        # in-memory record visible
        record = self.manager.get_record('PROJ-1')
        self.assertIsNotNone(record)
        self.assertEqual(record.task_summary, 'profile page user section')
        self.assertEqual(record.status, SESSION_STATUS_ACTIVE)
        self.assertEqual(record.agent_session_id, session.agent_session_id)

        # persisted as JSON next to the manager
        persisted = json.loads((self.state_dir / 'PROJ-1.json').read_text())
        self.assertEqual(persisted['task_id'], 'PROJ-1')
        self.assertEqual(persisted['agent_session_id'], session.agent_session_id)
        self.assertEqual(persisted['status'], SESSION_STATUS_ACTIVE)

    def test_start_session_normalizes_session_id_before_persisting(self) -> None:
        def factory(**kwargs):
            session = _FakeStreamingSession(**kwargs)
            session._agent_session_id = '  generated-id\n'
            return session

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )

        manager.start_session(task_id='PROJ-TRIM')

        record = manager.get_record('PROJ-TRIM')
        persisted = json.loads((self.state_dir / 'PROJ-TRIM.json').read_text())
        self.assertEqual(record.agent_session_id, 'generated-id')
        self.assertEqual(persisted['agent_session_id'], 'generated-id')

    def test_start_session_returns_existing_live_session(self) -> None:
        first = self.manager.start_session(task_id='PROJ-1')
        second = self.manager.start_session(task_id='PROJ-1')

        self.assertIs(first, second)
        self.assertEqual(len(self._fakes), 1)

    def test_restart_resumes_the_persisted_agent_session_id(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        # Mark dead and replace by restart-equivalent: drop in-memory state
        # and rebuild a fresh manager pointed at the same state dir.
        self.manager.terminate_session('PROJ-1')

        new_fakes: list[_FakeStreamingSession] = []

        def factory(**kwargs):
            session = _FakeStreamingSession(**kwargs)
            new_fakes.append(session)
            return session

        rebooted = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        # The persisted record is still there, but with terminated status.
        record = rebooted.get_record('PROJ-1')
        self.assertIsNotNone(record)

        rebooted.start_session(task_id='PROJ-1')
        self.assertEqual(len(new_fakes), 1)
        self.assertEqual(
            new_fakes[0].resume_session_id,
            record.agent_session_id,
        )

    def test_update_status_persists(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        self.manager.update_status('PROJ-1', SESSION_STATUS_DONE)

        record = self.manager.get_record('PROJ-1')
        self.assertEqual(record.status, SESSION_STATUS_DONE)
        persisted = json.loads((self.state_dir / 'PROJ-1.json').read_text())
        self.assertEqual(persisted['status'], SESSION_STATUS_DONE)

    def test_update_status_rejects_unknown(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        with self.assertRaisesRegex(ValueError, 'unknown session status'):
            self.manager.update_status('PROJ-1', 'whatever')

    def test_terminate_session_kills_subprocess_and_keeps_record_by_default(self) -> None:
        session = self.manager.start_session(task_id='PROJ-1')
        self.manager.terminate_session('PROJ-1')

        self.assertEqual(session.terminate_calls, 1)
        self.assertIsNone(self.manager.get_session('PROJ-1'))
        self.assertIsNotNone(self.manager.get_record('PROJ-1'))
        self.assertEqual(
            self.manager.get_record('PROJ-1').status,
            'terminated',
        )

    def test_terminate_session_with_remove_record_clears_disk(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        self.manager.terminate_session('PROJ-1', remove_record=True)

        self.assertIsNone(self.manager.get_record('PROJ-1'))
        self.assertFalse((self.state_dir / 'PROJ-1.json').exists())

    def test_remove_record_deletes_legacy_uppercase_filename(self) -> None:
        # Regression: records written before _record_path lowercased
        # live under the original-case filename (``UNA-1201.json``).
        # The canonical path is ``una-1201.json``; deleting only that
        # left the legacy file on disk and the blanket glob in
        # _load_persisted_records resurrected the tab on every
        # restart ("task is back after restart"). The delete must
        # remove ANY case-variant filename for the task.
        legacy = self.state_dir / 'UNA-1201.json'
        legacy.write_text(json.dumps({
            'task_id': 'UNA-1201', 'status': 'active',
            'agent_session_id': '', 'cwd': '',
        }), encoding='utf-8')
        self.manager.terminate_session('UNA-1201', remove_record=True)
        self.assertFalse(
            legacy.exists(),
            'legacy uppercase record must be deleted, not just the '
            'canonical lowercase path',
        )

    def test_remove_record_deletes_both_case_variants(self) -> None:
        # Belt-and-braces: if BOTH casings somehow exist, both go.
        upper = self.state_dir / 'UNA-99.json'
        lower = self.state_dir / 'una-99.json'
        for p in (upper, lower):
            p.write_text(json.dumps({'task_id': 'UNA-99'}), encoding='utf-8')
        self.manager.terminate_session('UNA-99', remove_record=True)
        self.assertFalse(upper.exists())
        self.assertFalse(lower.exists())

    def test_legacy_record_does_not_resurrect_after_delete(self) -> None:
        # The end-to-end guarantee: delete the legacy file, rebuild a
        # manager against the same state dir (≈ a kato restart), and
        # the task must NOT come back.
        legacy = self.state_dir / 'UNA-1201.json'
        legacy.write_text(json.dumps({
            'task_id': 'UNA-1201', 'status': 'active',
        }), encoding='utf-8')
        self.manager.terminate_session('UNA-1201', remove_record=True)
        rebooted = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        self.assertIsNone(rebooted.get_record('UNA-1201'))
        self.assertEqual(rebooted.list_records(), [])

    def _seed_claude_transcript(self, projects_root, session_id):
        path = Path(projects_root) / 'enc-cwd' / f'{session_id}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({'type': 'user', 'sessionId': session_id}) + '\n',
            encoding='utf-8',
        )
        return path

    def test_remove_record_also_deletes_the_claude_transcript(self) -> None:
        # Task done → workspace + kato record gone → the Claude CLI
        # transcript under ~/.claude/projects/ must go too, not
        # accumulate forever.
        self.manager.start_session(task_id='PROJ-1')
        sid = self.manager.get_record('PROJ-1').agent_session_id
        with tempfile.TemporaryDirectory() as proj_root:
            transcript = self._seed_claude_transcript(proj_root, sid)
            self.assertTrue(transcript.is_file())
            with patch.dict(
                os.environ, {'KATO_CLAUDE_SESSIONS_ROOT': proj_root},
            ):
                self.manager.terminate_session('PROJ-1', remove_record=True)
            self.assertFalse(
                transcript.is_file(),
                'transcript should be deleted when the task is forgotten',
            )

    def test_terminate_without_remove_record_keeps_transcript(self) -> None:
        # Plain terminate (e.g. webserver /stop) only kills the
        # subprocess + marks the record terminated — the transcript
        # MUST survive so the operator can resume / replay it.
        self.manager.start_session(task_id='PROJ-1')
        sid = self.manager.get_record('PROJ-1').agent_session_id
        with tempfile.TemporaryDirectory() as proj_root:
            transcript = self._seed_claude_transcript(proj_root, sid)
            with patch.dict(
                os.environ, {'KATO_CLAUDE_SESSIONS_ROOT': proj_root},
            ):
                self.manager.terminate_session('PROJ-1')
            self.assertTrue(transcript.is_file())

    def test_remove_record_with_no_transcript_is_safe(self) -> None:
        # No transcript on disk (never resumed / already cleaned) —
        # terminate_session must still succeed without raising.
        self.manager.start_session(task_id='PROJ-1')
        with tempfile.TemporaryDirectory() as proj_root:
            with patch.dict(
                os.environ, {'KATO_CLAUDE_SESSIONS_ROOT': proj_root},
            ):
                self.manager.terminate_session('PROJ-1', remove_record=True)
        self.assertIsNone(self.manager.get_record('PROJ-1'))

    def test_list_records_returns_all_known_tasks(self) -> None:
        self.manager.start_session(task_id='PROJ-1', task_summary='a')
        self.manager.start_session(task_id='PROJ-2', task_summary='b')

        records = self.manager.list_records()
        ids = sorted(record.task_id for record in records)
        self.assertEqual(ids, ['PROJ-1', 'PROJ-2'])

    def test_load_persisted_records_skips_unreadable_files(self) -> None:
        (self.state_dir / 'corrupt.json').write_text('{not json')
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kwargs: _FakeStreamingSession(**kwargs),
        )
        # Should not raise; corrupt file is silently ignored.
        self.assertEqual(manager.list_records(), [])

    def test_shutdown_terminates_every_live_session(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        self.manager.start_session(task_id='PROJ-2')
        self.manager.shutdown()
        self.assertTrue(all(fake.terminate_calls == 1 for fake in self._fakes))

    def test_start_session_forwards_docker_mode_on_to_factory(self) -> None:
        captured: dict = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return _FakeStreamingSession(**kwargs)

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        manager.start_session(task_id='PROJ-9', docker_mode_on=True)

        self.assertIs(captured['docker_mode_on'], True)

    def test_start_session_default_docker_mode_is_off(self) -> None:
        captured: dict = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return _FakeStreamingSession(**kwargs)

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        manager.start_session(task_id='PROJ-10')

        self.assertIs(captured['docker_mode_on'], False)

    def test_resume_copies_jsonl_into_target_cwd_project_dir(self) -> None:
        # One-session-per-task invariant: when kato spawns at a cwd
        # different from where the session's JSONL currently lives,
        # the manager copies the JSONL into the new cwd's project dir
        # so ``claude --resume`` finds it. Without this the resume
        # fails silently and a new session id is created — that's the
        # "kato keeps switching sessions" bug.
        import os
        sessions_root = self.state_dir / 'claude-sessions'
        old_cwd_project_dir = sessions_root / '-tmp-old-repo'
        old_cwd_project_dir.mkdir(parents=True)
        session_id = 'old-session-uuid'
        old_jsonl = old_cwd_project_dir / f'{session_id}.jsonl'
        old_jsonl.write_text('{"type": "user"}\n', encoding='utf-8')
        # Persist a record pointing at the old session id.
        record = PlanningSessionRecord(
            task_id='PROJ-77',
            agent_session_id=session_id,
            status='terminated',
            cwd='/tmp/old/repo',
        )
        self.manager._records[self.manager._lookup_key('PROJ-77')] = record
        self.manager._persist_record(record)

        os.environ['KATO_CLAUDE_SESSIONS_ROOT'] = str(sessions_root)
        self.addCleanup(
            os.environ.pop, 'KATO_CLAUDE_SESSIONS_ROOT', None,
        )
        try:
            self.manager.start_session(
                task_id='PROJ-77',
                cwd='/tmp/new/repo',
            )
        finally:
            pass

        new_cwd_project_dir = sessions_root / '-tmp-new-repo'
        self.assertTrue(
            (new_cwd_project_dir / f'{session_id}.jsonl').is_file(),
            'JSONL should have been copied into the new cwd project dir',
        )


class PlanningSessionRecordTests(unittest.TestCase):
    def test_round_trips_through_dict(self) -> None:
        original = PlanningSessionRecord(
            task_id='PROJ-1',
            task_summary='do the thing',
            agent_session_id='abc',
            status='review',
            created_at_epoch=100.0,
            updated_at_epoch=200.0,
            cwd='/tmp/x',
        )
        round_tripped = PlanningSessionRecord.from_dict(original.to_dict())
        self.assertEqual(round_tripped, original)

    def test_from_dict_trims_persisted_session_fields(self) -> None:
        restored = PlanningSessionRecord.from_dict({
            'task_id': '  PROJ-1  ',
            'agent_session_id': '  sess-1\n',
            'previous_agent_session_id': '  old-sess  ',
            'cwd': '  /tmp/repo  ',
        })

        self.assertEqual(restored.task_id, 'PROJ-1')
        self.assertEqual(restored.agent_session_id, 'sess-1')
        self.assertEqual(restored.previous_agent_session_id, 'old-sess')
        self.assertEqual(restored.cwd, '/tmp/repo')


class _StaleResumeFakeSession(_FakeStreamingSession):
    """Fake session that simulates the Claude CLI rejecting a resume id.

    Captures whether stderr should contain the "No conversation found with
    session ID" marker — that's how the manager detects the stale-resume
    case in production.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._stale_marker_active = False

    def start(self, initial_prompt: str = '') -> None:
        super().start(initial_prompt)
        if self._stale_marker_active and self.resume_session_id:
            # Simulate Claude exiting almost immediately when --resume
            # references a missing session.
            self._alive = False

    def stderr_snapshot(self) -> list[str]:
        if self._stale_marker_active and self.resume_session_id:
            return [
                f'No conversation found with session ID: {self.resume_session_id}',
            ]
        return []


class StaleResumeIdStrictPreservationTests(unittest.TestCase):
    """Stale resume failures must not replace the active session id."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name)
        self._fakes: list[_StaleResumeFakeSession] = []

    def _build_manager_with_stale_marker(self, mark_stale: bool):
        def factory(**kwargs):
            session = _StaleResumeFakeSession(**kwargs)
            session._stale_marker_active = mark_stale
            self._fakes.append(session)
            return session

        return ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )

    def test_died_with_stale_resume_id_detects_stderr_marker(self) -> None:
        session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='dead-id',
        )
        session._stale_marker_active = True
        session._alive = False
        self.assertTrue(
            ClaudeSessionManager._died_with_stale_resume_id(
                session, 'dead-id',
            )
        )

    def test_died_with_stale_resume_id_false_for_healthy_session(self) -> None:
        session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='some-id',
        )
        # No marker, no terminal event → not a stale-id death.
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(
                session, 'some-id',
            )
        )

    def test_died_with_stale_resume_id_handles_stderr_exception(self) -> None:
        # If stderr_snapshot() blows up, the check must still return
        # False rather than propagating (the manager treats that as
        # "healthy" — safer to keep the session than infinite-restart).
        session = SimpleNamespace_session = type(
            'BrokenSession', (), {
                'stderr_snapshot': lambda self: (_ for _ in ()).throw(RuntimeError('boom')),
                'terminal_event': None,
            },
        )()
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(
                session, 'any-id',
            )
        )

    def test_resume_id_kept_when_previous_session_died_with_stale_id(self) -> None:
        # When Claude died because --resume referenced a missing session,
        # keep retrying that id. Silent fresh-session drift is worse
        # than a loud failure.
        manager = self._build_manager_with_stale_marker(False)
        previous_record = PlanningSessionRecord(
            task_id='PROJ-1',
            agent_session_id='dead-session-uuid',
        )
        dead_session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='dead-session-uuid',
        )
        dead_session._stale_marker_active = True
        dead_session._alive = False

        # Persist the record so _persist_record path works.
        manager._records[manager._lookup_key('PROJ-1')] = previous_record
        manager._persist_record(previous_record)

        resume_id = manager._resume_id_for_spawn(
            'PROJ-1', previous_record, dead_session,
        )
        self.assertEqual(resume_id, 'dead-session-uuid')
        self.assertEqual(previous_record.agent_session_id, 'dead-session-uuid')

    def test_resume_id_kept_when_previous_session_is_healthy(self) -> None:
        # Healthy session → resume id should pass through unchanged.
        manager = self._build_manager_with_stale_marker(False)
        manager.start_session(task_id='PROJ-1')
        original_id = self._fakes[0].agent_session_id
        record = manager.get_record('PROJ-1')
        resume_id = manager._resume_id_for_spawn(
            'PROJ-1', record, self._fakes[0],
        )
        self.assertEqual(resume_id, original_id)

    def test_resume_id_for_spawn_no_previous_record_returns_empty(self) -> None:
        manager = self._build_manager_with_stale_marker(False)
        self.assertEqual(
            manager._resume_id_for_spawn('PROJ-1', None, None), '',
        )

    def test_resume_id_for_spawn_normalizes_whitespace_id(self) -> None:
        manager = self._build_manager_with_stale_marker(False)
        record = PlanningSessionRecord(task_id='PROJ-1', agent_session_id='   ')
        manager._records[manager._lookup_key('PROJ-1')] = record
        manager._persist_record(record)

        resume_id = manager._resume_id_for_spawn('PROJ-1', record, None)

        self.assertEqual(resume_id, '')
        self.assertEqual(record.agent_session_id, '')

    def test_resume_id_for_spawn_no_existing_session_returns_persisted(self) -> None:
        # First boot after restart: no existing session yet, but a
        # persisted record exists. The persisted id should be returned.
        manager = self._build_manager_with_stale_marker(False)
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='persisted-id',
        )
        self.assertEqual(
            manager._resume_id_for_spawn('PROJ-1', record, None),
            'persisted-id',
        )

    def test_wait_for_stale_resume_failure_returns_false_on_timeout(self) -> None:
        # Healthy session that never dies → must return False after the
        # configured wait. We pass max_wait_seconds=0 so the loop exits
        # immediately without sleeping.
        session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='any-id',
        )
        self.assertFalse(
            ClaudeSessionManager._wait_for_stale_resume_failure(
                session, 'any-id',
                max_wait_seconds=0,
                poll_interval_seconds=0,
            )
        )

    def test_wait_for_stale_resume_failure_detects_already_dead_session(self) -> None:
        session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='dead-id',
        )
        session._stale_marker_active = True
        session._alive = False  # already died before we polled
        self.assertTrue(
            ClaudeSessionManager._wait_for_stale_resume_failure(
                session, 'dead-id',
                max_wait_seconds=1,
                poll_interval_seconds=0,
            )
        )

    def test_stale_resume_rejection_keeps_original_session_id_on_record(self) -> None:
        # Operator invariant: after stop + restart, Claude rejecting
        # --resume must fail loud instead of replacing the session id.
        manager = self._build_manager_with_stale_marker(True)
        manager._records[manager._lookup_key('PROJ-1')] = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='original-uuid',
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
        self.assertEqual(record.agent_session_id, 'original-uuid')
        self.assertEqual(record.previous_agent_session_id, '')

    def test_self_heal_preserved_id_survives_persist_roundtrip(self) -> None:
        # The ``previous_agent_session_id`` field must round-trip
        # through to_dict / from_dict so it survives a kato restart.
        # Otherwise the operator-recovery path silently breaks across
        # process boundaries.
        record = PlanningSessionRecord(
            task_id='PROJ-1',
            agent_session_id='fresh-uuid',
            previous_agent_session_id='original-uuid',
        )
        restored = PlanningSessionRecord.from_dict(record.to_dict())
        self.assertEqual(restored.previous_agent_session_id, 'original-uuid')

    def test_successful_resume_inherits_preserved_previous_id(self) -> None:
        # A SUCCESSFUL resume must NOT wipe a
        # previously-preserved ``previous_agent_session_id``. Without
        # this, one healthy resume after old drift recovery would silently
        # erase the operator's recovery handle.
        manager = self._build_manager_with_stale_marker(False)
        manager._records[manager._lookup_key('PROJ-1')] = PlanningSessionRecord(
            task_id='PROJ-1',
            agent_session_id='current-uuid',
            previous_agent_session_id='old-original-uuid',
        )
        manager._persist_record(
            manager._records[manager._lookup_key('PROJ-1')]
        )

        manager.start_session(task_id='PROJ-1')

        record = manager.get_record('PROJ-1')
        # Preserved id survives a healthy resume.
        self.assertEqual(
            record.previous_agent_session_id, 'old-original-uuid',
        )


class WorkspaceSeedingTests(unittest.TestCase):
    """``_seed_records_from_workspaces`` runs on attach.

    If a workspace exposes ``agent_session_id`` (the modern name) or the
    legacy ``agent_session_id`` attribute, the manager imports it into
    its own state so a chat tab opened against that workspace finds the
    right id on the first ``start_session`` call.
    """

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name)

        def factory(**kwargs):
            return _FakeStreamingSession(**kwargs)

        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )

    def test_seeds_from_modern_agent_session_id_attribute(self) -> None:
        ws = SimpleNamespace(
            task_id='PROJ-A',
            task_summary='from workspace',
            agent_session_id='ws-session-id',
            cwd='/wks/A',
        )
        workspace_manager = SimpleNamespace(
            list_workspaces=lambda: [ws],
            update_agent_session=lambda *a, **kw: None,
        )
        self.manager.attach_workspace_manager(workspace_manager)
        record = self.manager.get_record('PROJ-A')
        self.assertIsNotNone(record)
        self.assertEqual(record.agent_session_id, 'ws-session-id')
        self.assertEqual(record.cwd, '/wks/A')

    def test_seeds_from_legacy_agent_session_id_attribute(self) -> None:
        # Older workspaces still surface ``agent_session_id``.
        ws = SimpleNamespace(
            task_id='PROJ-B',
            task_summary='legacy',
            agent_session_id='legacy-id',
            cwd='/wks/B',
        )
        workspace_manager = SimpleNamespace(
            list_workspaces=lambda: [ws],
            update_agent_session=lambda *a, **kw: None,
        )
        self.manager.attach_workspace_manager(workspace_manager)
        record = self.manager.get_record('PROJ-B')
        self.assertEqual(record.agent_session_id, 'legacy-id')

    def test_skips_workspace_without_session_id(self) -> None:
        ws = SimpleNamespace(
            task_id='PROJ-C', task_summary='', cwd='/wks/C',
        )
        workspace_manager = SimpleNamespace(
            list_workspaces=lambda: [ws],
            update_agent_session=lambda *a, **kw: None,
        )
        self.manager.attach_workspace_manager(workspace_manager)
        self.assertIsNone(self.manager.get_record('PROJ-C'))

    def test_does_not_overwrite_existing_session_id(self) -> None:
        # If the manager already has a session id from a prior start_session
        # call, the workspace seed must not clobber it (live state wins).
        self.manager.start_session(task_id='PROJ-D')
        original_id = self.manager.get_record('PROJ-D').agent_session_id
        ws = SimpleNamespace(
            task_id='PROJ-D', agent_session_id='workspace-id', cwd='',
        )
        workspace_manager = SimpleNamespace(
            list_workspaces=lambda: [ws],
            update_agent_session=lambda *a, **kw: None,
        )
        self.manager.attach_workspace_manager(workspace_manager)
        self.assertEqual(
            self.manager.get_record('PROJ-D').agent_session_id,
            original_id,
        )

    def test_workspace_seed_replaces_whitespace_only_existing_id(self) -> None:
        self.manager._records[self.manager._lookup_key('PROJ-W')] = (
            PlanningSessionRecord(
                task_id='PROJ-W',
                agent_session_id='   ',
                cwd='',
            )
        )
        ws = SimpleNamespace(
            task_id='PROJ-W', agent_session_id='workspace-good-id', cwd='/wks/W',
        )
        workspace_manager = SimpleNamespace(
            list_workspaces=lambda: [ws],
            update_agent_session=lambda *a, **kw: None,
        )

        self.manager.attach_workspace_manager(workspace_manager)

        record = self.manager.get_record('PROJ-W')
        self.assertEqual(record.agent_session_id, 'workspace-good-id')
        self.assertEqual(record.cwd, '/wks/W')

    def test_handles_list_workspaces_exception(self) -> None:
        def broken_list():
            raise RuntimeError('workspace manager dead')

        workspace_manager = SimpleNamespace(
            list_workspaces=broken_list,
            update_agent_session=lambda *a, **kw: None,
        )
        # Must not raise — manager logs and moves on.
        self.manager.attach_workspace_manager(workspace_manager)
        self.assertEqual(self.manager.list_records(), [])


class WorkspaceSeedingEarlyReturnTests(unittest.TestCase):
    """Line 172: ``_seed_records_from_workspaces`` early-returns when
    no workspace_manager is attached."""

    def test_no_op_when_no_workspace_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ClaudeSessionManager(
                state_dir=Path(tmp),
                session_factory=lambda **kw: _FakeStreamingSession(**kw),
            )
            # Calling the private seed directly with no workspace manager.
            manager._seed_records_from_workspaces()  # must not raise.


class NormalizeTaskIdValidation(unittest.TestCase):
    """Line 653: ``_normalize_task_id`` raises on blank."""

    def test_raises_on_blank(self) -> None:
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            ClaudeSessionManager._normalize_task_id('')
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            ClaudeSessionManager._normalize_task_id('   ')


class WaitForStaleResumeFailurePolling(unittest.TestCase):
    """Line 618: ``time.sleep(poll_interval_seconds)`` in the polling loop."""

    def test_polls_until_session_dies(self) -> None:
        # Healthy session → polling continues. Then it goes "dead" with
        # the stale marker on the second check.
        session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='dead-id',
        )
        session._alive = True

        poll_count = [0]
        original_died = ClaudeSessionManager._died_with_stale_resume_id

        @staticmethod
        def selective_died(s, rid):
            poll_count[0] += 1
            if poll_count[0] < 2:
                return False  # still alive, no marker
            s._alive = False
            s._stale_marker_active = True
            return True

        with patch.object(
            ClaudeSessionManager, '_died_with_stale_resume_id', selective_died,
        ):
            result = ClaudeSessionManager._wait_for_stale_resume_failure(
                session, 'dead-id',
                max_wait_seconds=1,
                poll_interval_seconds=0,  # No real wait
            )
        self.assertTrue(result)
        self.assertGreaterEqual(poll_count[0], 2)


class DiedWithStaleResumeIdAdditionalBranches(unittest.TestCase):
    """Lines 643-647: ``_died_with_stale_resume_id`` terminal-event paths."""

    def test_returns_false_when_no_terminal_event(self) -> None:
        session = _StaleResumeFakeSession(
            task_id='PROJ-1', resume_session_id='dead-id',
        )
        # No stderr_marker active AND no terminal event → False.
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'dead-id')
        )

    def test_returns_false_when_terminal_event_not_an_error(self) -> None:
        session = SimpleNamespace(
            stderr_snapshot=lambda: [],
            terminal_event=SimpleNamespace(raw={'is_error': False, 'result': ''}),
        )
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'any-id')
        )

    def test_returns_true_when_terminal_result_contains_marker(self) -> None:
        session = SimpleNamespace(
            stderr_snapshot=lambda: [],
            terminal_event=SimpleNamespace(raw={
                'is_error': True,
                'result': 'No conversation found with session ID: dead-id',
            }),
        )
        self.assertTrue(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'dead-id')
        )

    def test_returns_false_when_terminal_marker_for_different_session(self) -> None:
        session = SimpleNamespace(
            stderr_snapshot=lambda: [],
            terminal_event=SimpleNamespace(raw={
                'is_error': True,
                'result': 'No conversation found with session ID: other-id',
            }),
        )
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'dead-id')
        )


class TerminateSessionExceptionPath(unittest.TestCase):
    """Lines 572-573: session.terminate() throws → log + continue."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_logs_when_terminate_raises(self) -> None:
        broken_session = MagicMock()
        broken_session.terminate.side_effect = RuntimeError('term failed')

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        lookup_key = manager._lookup_key('PROJ-1')
        manager._sessions[lookup_key] = broken_session
        manager._records[lookup_key] = PlanningSessionRecord(task_id='PROJ-1')

        with patch.object(manager, 'logger', MagicMock()) as logger:
            # Must not raise — log + continue.
            manager.terminate_session('PROJ-1')
        logger.exception.assert_called_once()


class UpdateStatusNoOpForUnknownTask(unittest.TestCase):
    """Line 560: ``record is None`` → silent return."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_no_op_when_task_id_unknown(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import SESSION_STATUS_DONE
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        # No record exists for this task → silently no-ops.
        manager.update_status('NO-SUCH-TASK', SESSION_STATUS_DONE)


class MirrorEarlyReturnTests(unittest.TestCase):
    """Line 675: ``not record.agent_session_id and not record.cwd`` early return."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_skips_mirror_when_record_has_no_id_or_cwd(self) -> None:
        workspace_manager = MagicMock()
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        manager._workspace_manager = workspace_manager
        empty_record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='', cwd='',
        )
        manager._mirror_to_workspace_metadata(empty_record)
        # Workspace was never touched because the record carries no useful info.
        workspace_manager.update_agent_session.assert_not_called()


class AdoptSessionIdMirrorTests(unittest.TestCase):
    """Line 544: ``adopt_session_id`` mirrors to workspace metadata."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_adopt_mirrors_to_workspace_when_attached(self) -> None:
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = []
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        manager.attach_workspace_manager(workspace_manager)
        manager.adopt_session_id('PROJ-1', agent_session_id='adopted-id')
        # Mirror call was made.
        workspace_manager.update_agent_session.assert_called()


class LoadPersistedRecordsErrorPaths(unittest.TestCase):
    """Lines 703, 715, 718: ``_load_persisted_records`` skip paths."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_unparseable_json_file_is_skipped(self) -> None:
        # Line 703: ``OSError | json.JSONDecodeError`` → log + continue.
        (self.state_dir / 'broken.json').write_text('{not valid json')
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        self.assertEqual(manager.list_records(), [])

    def test_non_dict_payload_is_skipped(self) -> None:
        # Line 715: payload not a dict → skip.
        (self.state_dir / 'list.json').write_text('[1, 2, 3]')
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        self.assertEqual(manager.list_records(), [])

    def test_record_without_task_id_is_skipped(self) -> None:
        # Line 718: record.task_id blank → skip.
        (self.state_dir / 'no_id.json').write_text(
            json.dumps({'task_id': '', 'agent_session_id': 'abc'}),
        )
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        self.assertEqual(manager.list_records(), [])


class WithRefreshedSessionIdTests(unittest.TestCase):
    """``_with_refreshed_session_id`` captures only missing ids.

    The method takes only ``record`` — the session is looked up from
    ``self._sessions`` by task_id.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_returns_none_when_record_is_none(self) -> None:
        self.assertIsNone(self.manager._with_refreshed_session_id(None))

    def test_refreshes_when_record_has_no_session_id(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='',
        )
        lookup_key = self.manager._lookup_key('PROJ-1')
        self.manager._records[lookup_key] = record
        live = SimpleNamespace(agent_session_id='new-id')
        self.manager._sessions[lookup_key] = live

        refreshed = self.manager._with_refreshed_session_id(record)
        self.assertEqual(refreshed.agent_session_id, 'new-id')

    def test_does_not_overwrite_pinned_session_id(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='old-id',
        )
        lookup_key = self.manager._lookup_key('PROJ-1')
        self.manager._records[lookup_key] = record
        self.manager._sessions[lookup_key] = SimpleNamespace(
            agent_session_id='new-id',
            _resume_session_id='old-id',
        )

        refreshed = self.manager._with_refreshed_session_id(record)
        self.assertEqual(refreshed.agent_session_id, 'old-id')

    def test_refresh_does_not_replace_generated_id_without_callback(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='generated-id',
        )
        lookup_key = self.manager._lookup_key('PROJ-1')
        self.manager._records[lookup_key] = record
        self.manager._sessions[lookup_key] = SimpleNamespace(
            agent_session_id='actual-id',
            _resume_session_id='',
        )

        refreshed = self.manager._with_refreshed_session_id(record)
        self.assertEqual(refreshed.agent_session_id, 'generated-id')

    def test_returns_record_unchanged_when_no_live_session(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='persisted',
        )
        # No session in self._sessions for this task.
        refreshed = self.manager._with_refreshed_session_id(record)
        self.assertEqual(refreshed.agent_session_id, 'persisted')

    def test_no_op_when_live_id_matches_persisted(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='same-id',
        )
        self.manager._sessions[self.manager._lookup_key('PROJ-1')] = (
            SimpleNamespace(agent_session_id='same-id')
        )
        refreshed = self.manager._with_refreshed_session_id(record)
        self.assertEqual(refreshed.agent_session_id, 'same-id')


class LiveSessionIdDriftTests(unittest.TestCase):
    """Pinned records win over mismatched live subprocesses."""

    class _WrongLiveSession(object):
        def __init__(self) -> None:
            self.agent_session_id = 'wrong-live-id'
            self.is_alive = True
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1
            self.is_alive = False

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.spawned: list[_FakeStreamingSession] = []
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=self._factory,
        )

    def _factory(self, **kwargs) -> _FakeStreamingSession:
        session = _FakeStreamingSession(**kwargs)
        self.spawned.append(session)
        return session

    def _pin_record_with_wrong_live(self) -> tuple[str, _WrongLiveSession]:
        lookup_key = self.manager._lookup_key('PROJ-1')
        self.manager._records[lookup_key] = PlanningSessionRecord(
            task_id='PROJ-1',
            agent_session_id='pinned-id',
        )
        wrong = self._WrongLiveSession()
        self.manager._sessions[lookup_key] = wrong
        return lookup_key, wrong

    def test_get_session_discards_live_session_with_wrong_id(self) -> None:
        lookup_key, wrong = self._pin_record_with_wrong_live()

        self.assertIsNone(self.manager.get_session('PROJ-1'))

        self.assertEqual(wrong.terminate_calls, 1)
        self.assertNotIn(lookup_key, self.manager._sessions)
        self.assertEqual(
            self.manager.get_record('PROJ-1').agent_session_id,
            'pinned-id',
        )

    def test_start_session_respawns_with_pinned_id_after_live_drift(self) -> None:
        _, wrong = self._pin_record_with_wrong_live()

        session = self.manager.start_session(task_id='PROJ-1')

        self.assertEqual(wrong.terminate_calls, 1)
        self.assertIs(session, self.spawned[0])
        self.assertEqual(session.resume_session_id, 'pinned-id')
        self.assertEqual(
            self.manager.get_record('PROJ-1').agent_session_id,
            'pinned-id',
        )

    def test_list_records_discards_live_session_with_wrong_id(self) -> None:
        lookup_key, wrong = self._pin_record_with_wrong_live()

        records = self.manager.list_records()

        self.assertEqual([record.agent_session_id for record in records], ['pinned-id'])
        self.assertEqual(wrong.terminate_calls, 1)
        self.assertNotIn(lookup_key, self.manager._sessions)


class EnsureResumeJsonlBranches(unittest.TestCase):
    """``_ensure_resume_jsonl_at_target_cwd`` recovery paths.

    Each test patches one external collaborator (find_session_file,
    migrate_session_to_workspace) so we can drive the function down its
    individual branches without spinning up real Claude sessions.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_no_op_when_resume_id_blank(self) -> None:
        # Early return before any patch — no exceptions, no work.
        self.manager._ensure_resume_jsonl_at_target_cwd(
            resume_session_id='', target_cwd='/repo',
        )

    def test_no_op_when_target_cwd_blank(self) -> None:
        self.manager._ensure_resume_jsonl_at_target_cwd(
            resume_session_id='abc', target_cwd='',
        )

    def test_silent_when_dynamic_import_fails(self) -> None:
        # Lines 338-339: defensive ImportError handler — kicks in if the
        # session.history / session.index submodules ever go missing.
        # Force it by raising ImportError on the import. Patch ``__import__``
        # to throw for the two submodules the manager pulls in dynamically.
        import builtins
        real_import = builtins.__import__

        def selective(name, *args, **kwargs):
            if name in (
                'claude_core_lib.claude_core_lib.session.history',
                'claude_core_lib.claude_core_lib.session.index',
            ):
                raise ImportError(f'mocked: {name} unavailable')
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', selective):
            # Must return without raising — and crucially, without touching
            # ``find_session_file`` since the import never resolved.
            self.manager._ensure_resume_jsonl_at_target_cwd(
                resume_session_id='abc', target_cwd='/repo',
            )

    def test_logs_when_find_session_file_raises(self) -> None:
        # Lines 342-347: any exception from find_session_file is logged
        # and swallowed — must not propagate up to start_session.
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            side_effect=RuntimeError('disk error'),
        ):
            with patch.object(self.manager, 'logger', MagicMock()) as logger:
                self.manager._ensure_resume_jsonl_at_target_cwd(
                    resume_session_id='abc', target_cwd='/repo',
                )
            logger.exception.assert_called_once()

    def test_no_op_when_source_not_found(self) -> None:
        # Lines 353-355: find_session_file returned None → silent return.
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            return_value=None,
        ):
            # No exception, no log — just returns.
            self.manager._ensure_resume_jsonl_at_target_cwd(
                resume_session_id='abc', target_cwd='/repo',
            )

    def test_logs_when_migrate_raises(self) -> None:
        # Lines 369-370: migrate_session_to_workspace raised; the manager
        # must log via exception() and swallow.
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            return_value=Path('/fake/source.jsonl'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.index.migrate_session_to_workspace',
            side_effect=RuntimeError('copy failed'),
        ):
            with patch.object(self.manager, 'logger', MagicMock()) as logger:
                self.manager._ensure_resume_jsonl_at_target_cwd(
                    resume_session_id='abc', target_cwd='/never/seen',
                )
            logger.exception.assert_called_once()


class SpawnWithResumeStrictPreservationTests(unittest.TestCase):
    """Stale resume rejection fails loud instead of spawning fresh."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_terminates_and_raises_when_first_session_dies_with_stale_id(self) -> None:
        spawn_kwargs: list[dict] = []
        terminate_calls: list[int] = []

        def factory(**kwargs):
            spawn_kwargs.append(dict(kwargs))
            session = _FakeStreamingSession(**kwargs)
            # Plant a terminate hook to confirm cleanup happens.
            original_terminate = session.terminate

            def tracked_terminate():
                terminate_calls.append(1)
                original_terminate()

            session.terminate = tracked_terminate
            return session

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        # First call: stale-id check returns True → retry path.
        # Second call (the respawn): never re-enters the check (empty resume).
        with patch.object(
            ClaudeSessionManager, '_wait_for_stale_resume_failure',
            return_value=True,
        ):
            with self.assertRaises(RuntimeError):
                manager._spawn_with_resume_self_heal(
                    normalized_task_id='PROJ-1',
                    factory_kwargs={'task_id': 'PROJ-1', 'cwd': '/wks'},
                    initial_prompt='',
                    resume_session_id='dead-id',
                )
        # One factory call only: no fresh-session retry is allowed.
        self.assertEqual(len(spawn_kwargs), 1)
        self.assertEqual(spawn_kwargs[0]['resume_session_id'], 'dead-id')
        self.assertEqual(len(terminate_calls), 1)

    def test_first_session_terminate_failure_is_swallowed(self) -> None:
        spawn_count = [0]

        def factory(**kwargs):
            spawn_count[0] += 1
            session = _FakeStreamingSession(**kwargs)
            if spawn_count[0] == 1:
                # First session: terminate() blows up.
                session.terminate = MagicMock(side_effect=RuntimeError('terminate failed'))
            return session

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        with patch.object(
            ClaudeSessionManager, '_wait_for_stale_resume_failure',
            return_value=True,
        ):
            with self.assertRaises(RuntimeError):
                manager._spawn_with_resume_self_heal(
                    normalized_task_id='PROJ-1',
                    factory_kwargs={'task_id': 'PROJ-1', 'cwd': '/wks'},
                    initial_prompt='',
                    resume_session_id='dead-id',
                )
        self.assertEqual(spawn_count[0], 1)


class LoadPersistedRecordsOsErrorTests(unittest.TestCase):
    """Line 703: ``except (OSError, ...)`` covers OSError on read_text."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_oserror_reading_record_file_logs_and_skips(self) -> None:
        # Create a valid record file but make read_text raise.
        path = self.state_dir / 'PROJ-1.json'
        path.write_text(json.dumps({
            'task_id': 'PROJ-1', 'agent_session_id': 'abc',
        }))

        real_read = Path.read_text

        def selective(self_path, *args, **kwargs):
            if self_path.name == 'PROJ-1.json':
                raise PermissionError('locked')
            return real_read(self_path, *args, **kwargs)

        with patch.object(Path, 'read_text', selective):
            manager = ClaudeSessionManager(
                state_dir=self.state_dir,
                session_factory=lambda **kw: _FakeStreamingSession(**kw),
            )
        # Record was skipped without crashing the boot.
        self.assertEqual(manager.list_records(), [])


class EnsureResumeJsonlSourceAtTargetTests(unittest.TestCase):
    """Lines 352-355: when ``source.parent.resolve() == target_dir.resolve()``
    we no-op (already at the right location), and any OSError from resolve()
    is swallowed.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_no_op_when_source_already_at_target_dir(self) -> None:
        # Line 353: source.parent equals target_dir → no migrate call.
        sessions_root = self.state_dir / 'sessions'
        sessions_root.mkdir()
        target_cwd_dir = sessions_root / '-tmp-task'
        target_cwd_dir.mkdir()
        jsonl = target_cwd_dir / 'sess-A.jsonl'
        jsonl.write_text('')

        from claude_core_lib.claude_core_lib.session.index import CLAUDE_SESSIONS_ROOT_ENV_KEY
        with patch.dict(os.environ, {CLAUDE_SESSIONS_ROOT_ENV_KEY: str(sessions_root)}):
            with patch(
                'claude_core_lib.claude_core_lib.session.history.find_session_file',
                return_value=jsonl,
            ):
                # If migrate were called we'd see a copy; we just verify no exception.
                with patch(
                    'claude_core_lib.claude_core_lib.session.index.migrate_session_to_workspace',
                ) as mock_migrate:
                    self.manager._ensure_resume_jsonl_at_target_cwd(
                        resume_session_id='sess-A',
                        target_cwd='/tmp/task',
                    )
                    # The equality check short-circuits before migrate runs.
                    mock_migrate.assert_not_called()

    def test_swallows_oserror_from_resolve_and_continues(self) -> None:
        # Lines 354-355: resolve raises OSError on legacy py → swallow + fall through to migrate.
        from unittest.mock import patch as patch_obj
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            return_value=Path('/some/source.jsonl'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.index.migrate_session_to_workspace',
            return_value=None,  # noop
        ), patch_obj.object(Path, 'resolve', side_effect=OSError('legacy py')):
            # Must not raise.
            self.manager._ensure_resume_jsonl_at_target_cwd(
                resume_session_id='sess', target_cwd='/repo',
            )


class AdoptSessionIdTaskSummaryTests(unittest.TestCase):
    """Line 544: when adopt is called with a non-empty task_summary AND the
    pre-existing record has no summary, the new summary is stored.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def test_task_summary_is_filled_in_when_record_had_none(self) -> None:
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )
        # Existing record with NO task_summary.
        manager._records[manager._lookup_key('PROJ-X')] = PlanningSessionRecord(
            task_id='PROJ-X', agent_session_id='old', task_summary='',
        )
        manager.adopt_session_id(
            'PROJ-X', agent_session_id='old', task_summary='added later',
        )
        self.assertEqual(
            manager.get_record('PROJ-X').task_summary, 'added later',
        )


class AdoptSessionIdSpawnRaceTests(unittest.TestCase):
    """Adoption cannot race a same-task spawn metadata write."""

    def test_adopt_waits_for_same_task_spawn_and_refuses_live_session(self) -> None:
        started = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        class _BlockingSession(_FakeStreamingSession):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self._agent_session_id = 'fresh-race-id'

            def start(self, initial_prompt: str = '') -> None:
                started.set()
                release.wait(timeout=5)
                super().start(initial_prompt=initial_prompt)

        with tempfile.TemporaryDirectory() as state_dir:
            manager = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: _BlockingSession(**kw),
            )

            def start_session() -> None:
                manager.start_session(task_id='PROJ-RACE')

            def adopt_session() -> None:
                try:
                    manager.adopt_session_id(
                        'PROJ-RACE',
                        agent_session_id='adopted-race-id',
                    )
                except BaseException as exc:
                    errors.append(exc)

            start_thread = threading.Thread(target=start_session)
            start_thread.start()
            self.assertTrue(started.wait(timeout=5))

            adopt_thread = threading.Thread(target=adopt_session)
            adopt_thread.start()
            self.assertFalse(errors)

            release.set()
            start_thread.join(timeout=5)
            adopt_thread.join(timeout=5)
            self.assertFalse(start_thread.is_alive())
            self.assertFalse(adopt_thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RuntimeError)
            self.assertIn('live Claude subprocess', str(errors[0]))
            self.assertEqual(
                manager.get_record('PROJ-RACE').agent_session_id,
                'fresh-race-id',
            )


class LoadPersistedRecordsMissingDirTests(unittest.TestCase):
    """Line 703: ``return`` when state_dir does not exist on disk.

    Constructor auto-mkdir's the state_dir, so we hit this by calling
    ``_load_persisted_records`` directly after removing the dir.
    """

    def test_no_op_when_state_dir_was_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / 'mgr_state'
            manager = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: _FakeStreamingSession(**kw),
            )
            # Simulate the dir being deleted out from under us between calls.
            state_dir.rmdir()
            self.assertFalse(state_dir.exists())
            # Must not raise — silent return at line 703.
            manager._load_persisted_records()


class PersistedRecordHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_delete_persisted_record_silently_ignores_missing_file(self) -> None:
        # Calling delete on a never-persisted task must not raise.
        self.manager._delete_persisted_record('nonexistent-task')

    def test_delete_persisted_record_logs_warning_on_oserror(self) -> None:
        # Hit the OSError branch by patching ``Path.unlink``.
        self.manager.start_session(task_id='PROJ-X')
        with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            with patch.object(self.manager, 'logger', MagicMock()) as mock_logger:
                self.manager._delete_persisted_record('PROJ-X')
                mock_logger.warning.assert_called_once()

    def test_mirror_to_workspace_metadata_no_op_when_no_workspace_manager(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='id', cwd='/wks',
        )
        # Default manager has no workspace_manager — must not raise.
        self.manager._mirror_to_workspace_metadata(record)

    def test_mirror_to_workspace_metadata_handles_workspace_exception(self) -> None:
        # Workspace update can fail; manager logs and moves on (does NOT raise).
        workspace_manager = MagicMock()
        workspace_manager.update_agent_session.side_effect = RuntimeError('boom')
        self.manager._workspace_manager = workspace_manager
        record = PlanningSessionRecord(
            task_id='PROJ-1', agent_session_id='id', cwd='/wks',
        )
        with patch.object(self.manager, 'logger', MagicMock()) as mock_logger:
            self.manager._mirror_to_workspace_metadata(record)
            mock_logger.exception.assert_called_once()


class GateResumeByJsonlSizeTests(unittest.TestCase):
    """_gate_resume_by_jsonl_size warns but preserves the resume id."""

    def _make_manager(self):
        with tempfile.TemporaryDirectory() as state_dir:
            return ClaudeSessionManager(state_dir=state_dir)

    def test_returns_id_unchanged_when_no_session_id(self) -> None:
        mgr = self._make_manager()
        self.assertEqual(mgr._gate_resume_by_jsonl_size('TASK-1', ''), '')

    def test_returns_id_unchanged_when_file_not_found(self) -> None:
        mgr = self._make_manager()
        with patch(
            'claude_core_lib.claude_core_lib.session.manager.'
            'ClaudeSessionManager._gate_resume_by_jsonl_size',
            wraps=mgr._gate_resume_by_jsonl_size,
        ):
            with patch(
                'claude_core_lib.claude_core_lib.session.history.find_session_file',
                return_value=None,
            ):
                result = mgr._gate_resume_by_jsonl_size('TASK-1', 'abc-123')
        self.assertEqual(result, 'abc-123')

    def test_returns_id_when_file_small(self) -> None:
        mgr = self._make_manager()
        mock_path = MagicMock()
        mock_path.stat.return_value.st_size = 500_000  # under 1 MB limit
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            return_value=mock_path,
        ):
            result = mgr._gate_resume_by_jsonl_size('TASK-1', 'abc-123')
        self.assertEqual(result, 'abc-123')

    def test_returns_id_when_file_exceeds_limit(self) -> None:
        mgr = self._make_manager()
        mock_path = MagicMock()
        mock_path.stat.return_value.st_size = 2_000_000  # 2 MB, over limit
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            return_value=mock_path,
        ):
            with patch.object(mgr, 'logger', MagicMock()) as mock_logger:
                result = mgr._gate_resume_by_jsonl_size('TASK-1', 'abc-123')
                mock_logger.warning.assert_called_once()
        self.assertEqual(result, 'abc-123')

    def test_returns_id_when_find_raises(self) -> None:
        mgr = self._make_manager()
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            side_effect=Exception('unexpected'),
        ):
            result = mgr._gate_resume_by_jsonl_size('TASK-1', 'abc-123')
        self.assertEqual(result, 'abc-123')

    def test_returns_id_when_stat_raises(self) -> None:
        mgr = self._make_manager()
        mock_path = MagicMock()
        mock_path.stat.side_effect = OSError('disk error')
        with patch(
            'claude_core_lib.claude_core_lib.session.history.find_session_file',
            return_value=mock_path,
        ):
            result = mgr._gate_resume_by_jsonl_size('TASK-1', 'abc-123')
        self.assertEqual(result, 'abc-123')


class CorrectSessionIdInRecordTests(unittest.TestCase):
    """Correction callback records missing ids but does not overwrite
    an already-pinned operator session id."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_blank_actual_id_is_a_noop(self) -> None:
        # Defensive early return — caller passed empty / whitespace.
        # Should not touch the record or call persist.
        key = self.manager._lookup_key('PROJ-A')
        self.manager._records[key] = PlanningSessionRecord(
            task_id='PROJ-A', agent_session_id='original',
        )
        with patch.object(self.manager, '_persist_record') as persist:
            self.manager._correct_session_id_in_record(key, 'PROJ-A', '   ')
        persist.assert_not_called()
        self.assertEqual(
            self.manager._records[key].agent_session_id, 'original',
        )

    def test_unknown_lookup_key_is_a_noop(self) -> None:
        # No record for this task — silently return rather than crash.
        with patch.object(self.manager, '_persist_record') as persist:
            self.manager._correct_session_id_in_record(
                'unknown-key', 'PROJ-MISSING', 'new-id',
            )
        persist.assert_not_called()

    def test_matching_id_is_a_noop(self) -> None:
        # Record already has the same id — no work to do, no persist call.
        key = self.manager._lookup_key('PROJ-B')
        self.manager._records[key] = PlanningSessionRecord(
            task_id='PROJ-B', agent_session_id='same-id',
        )
        with patch.object(self.manager, '_persist_record') as persist:
            self.manager._correct_session_id_in_record(key, 'PROJ-B', 'same-id')
        persist.assert_not_called()

    def test_matching_id_normalizes_persisted_whitespace(self) -> None:
        key = self.manager._lookup_key('PROJ-B')
        self.manager._records[key] = PlanningSessionRecord(
            task_id='PROJ-B', agent_session_id='  same-id\n',
        )

        self.manager._correct_session_id_in_record(key, 'PROJ-B', 'same-id')

        persisted = json.loads((self.state_dir / 'PROJ-B.json').read_text())
        self.assertEqual(self.manager._records[key].agent_session_id, 'same-id')
        self.assertEqual(persisted['agent_session_id'], 'same-id')

    def test_different_id_updates_empty_record_and_persists(self) -> None:
        key = self.manager._lookup_key('PROJ-C')
        self.manager._records[key] = PlanningSessionRecord(
            task_id='PROJ-C', agent_session_id='',
        )
        with patch.object(self.manager, '_persist_record') as persist:
            self.manager._correct_session_id_in_record(key, 'PROJ-C', 'new-id')
        self.assertEqual(
            self.manager._records[key].agent_session_id, 'new-id',
        )
        persist.assert_called_once()

    def test_different_id_does_not_overwrite_pinned_record(self) -> None:
        key = self.manager._lookup_key('PROJ-C')
        self.manager._records[key] = PlanningSessionRecord(
            task_id='PROJ-C', agent_session_id='old-id',
        )
        with patch.object(self.manager, '_persist_record') as persist:
            self.manager._correct_session_id_in_record(key, 'PROJ-C', 'new-id')
        self.assertEqual(
            self.manager._records[key].agent_session_id, 'old-id',
        )
        persist.assert_not_called()

    def test_fresh_spawn_can_replace_expected_generated_id(self) -> None:
        key = self.manager._lookup_key('PROJ-C')
        self.manager._records[key] = PlanningSessionRecord(
            task_id='PROJ-C', agent_session_id='generated-id',
        )
        with patch.object(self.manager, '_persist_record') as persist:
            self.manager._correct_session_id_in_record(
                key, 'PROJ-C', 'actual-id',
                expected_existing_id='generated-id',
                can_replace_existing=True,
            )
        self.assertEqual(
            self.manager._records[key].agent_session_id, 'actual-id',
        )
        persist.assert_called_once()


class AdoptSessionIdRefusesWhenLiveTests(unittest.TestCase):
    """Line 645: refuse adoption when a live subprocess is still
    running for the task — adoption is supposed to swap the resumed
    id before the next spawn, and silently reusing the live session
    would discard the operator's intent."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_raises_when_live_session_present(self) -> None:
        key = self.manager._lookup_key('PROJ-Y')
        # Plant a "live" session under the lookup key.
        live_session = SimpleNamespace(is_alive=True)
        self.manager._sessions[key] = live_session
        with self.assertRaises(RuntimeError) as ctx:
            self.manager.adopt_session_id(
                'PROJ-Y', agent_session_id='external-id',
            )
        msg = str(ctx.exception)
        self.assertIn('PROJ-Y', msg)
        self.assertIn('Terminate', msg)


class ForgetClaudeTranscriptExceptionTests(unittest.TestCase):
    """Lines 866-867: when ``delete_session_file`` itself raises an
    unexpected exception (not just a quiet OSError), ``_forget_claude_transcript``
    logs the exception and returns rather than propagating. The
    terminate path must never bubble a transcript-cleanup failure."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_unexpected_exception_in_delete_is_logged_and_swallowed(self) -> None:
        record = PlanningSessionRecord(
            task_id='PROJ-Z', agent_session_id='sess-z',
        )
        with patch(
            'claude_core_lib.claude_core_lib.session.history.delete_session_file',
            side_effect=RuntimeError('unexpected boom'),
        ), patch.object(self.manager, 'logger', MagicMock()) as mock_logger:
            self.manager._forget_claude_transcript(record, 'PROJ-Z')
        # Exception must be logged but not propagated.
        mock_logger.exception.assert_called_once()
        msg = mock_logger.exception.call_args[0][0]
        self.assertIn('failed deleting', msg)


class DeletePersistedRecordGlobOSErrorTests(unittest.TestCase):
    """Lines 890-893: when ``Path.glob('*.json')`` raises OSError
    (directory listing failed mid-traversal), the helper must fall
    through to the canonical-path-only unlink rather than crash."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kw: _FakeStreamingSession(**kw),
        )

    def test_glob_oserror_falls_back_to_canonical_path(self) -> None:
        # First persist a record so the canonical file exists.
        self.manager.start_session(task_id='PROJ-G')
        canonical = self.manager._record_path('PROJ-G')
        self.assertTrue(canonical.is_file())
        # Now make glob() raise — the fallback should still unlink the
        # canonical file without propagating.
        with patch.object(Path, 'glob', side_effect=OSError('dir listing failed')):
            self.manager._delete_persisted_record('PROJ-G')
        self.assertFalse(canonical.is_file())


if __name__ == '__main__':
    unittest.main()
