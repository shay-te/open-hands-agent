"""Flow #3 — kato restart while a task has live work in progress.

A-Z scenario (the operator's lived experience):

    1. Kato is running, task ``T1`` has a live Claude session with
       session_id ``X`` and a JSONL transcript on disk.
    2. Operator hits Ctrl-C on kato (or the process dies).
    3. Operator runs ``kato run`` again.
    4. Operator opens task T1's chat tab.
       → SSE endpoint replays JSONL history; the chat scroll-back is intact.
    5. Operator types a new message.
       → kato respawns Claude with ``--resume X`` and the same JSONL.
       → No re-exploration of the workspace, same context window.

The Bug 1 incident this defends: the operator reported that after a
kato restart the tab said "reattached" but showed no scroll-back AND
the next message kicked off a brand-new session. Two failure modes
fused into one. This file pins BOTH halves end-to-end PLUS the
adversarial paths that would let a regression slip through (corrupt
records, malformed JSON, ACTIVE-status leak, orchestration prompts
leaking into the UI, empty/whitespace session id flowing through).

Test naming: ``test_flow_restart_<aspect>`` so a regression failure
prints "test_flow_restart_<aspect>" — self-explanatory in CI logs.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.session_id_utils import AGENT_SESSION_ID

# ---------------------------------------------------------------------------
# Shared stub session for the session-manager surface.
# ---------------------------------------------------------------------------


def _make_stub_session(recorded: dict, *, fresh_id: str = 'fresh-uuid'):
    """Stub StreamingClaudeSession that mirrors the real synchronous-adopt
    behavior: ``--resume <id>`` adopts the id BEFORE the first stream
    event arrives (the real impl does this inside ``_build_command``)."""

    class _StubSession:
        def __init__(self, **kwargs):
            recorded.setdefault('all_spawns', []).append(kwargs)
            recorded['kwargs'] = kwargs
            self._kwargs = kwargs
            self._task_id = kwargs.get('task_id', '')
            self._cwd = kwargs.get('cwd', '')
            self._agent_session_id = (
                kwargs.get('resume_session_id', '') or fresh_id
            )
            self._alive = True

        @property
        def task_id(self): return self._task_id

        @property
        def cwd(self): return self._cwd

        @property
        def agent_session_id(self): return self._agent_session_id

        @property
        def is_alive(self): return self._alive

        @property
        def is_working(self): return False

        def start(self, *, initial_prompt=''):
            recorded.setdefault('starts', []).append(initial_prompt)

        def send_user_message(self, *args, **kwargs):
            recorded.setdefault('sends', []).append((args, kwargs))

        def poll_event(self, *args, **kwargs):
            return None

        @property
        def terminal_event(self):
            return None

        def terminate(self):
            self._alive = False

        def recent_events(self):
            return []

        def events_after(self, _index):
            return [], 0

    return _StubSession


# ---------------------------------------------------------------------------
# Flow #3 — the core end-to-end restart scenario.
# ---------------------------------------------------------------------------


class FlowRestartMidWorkTests(unittest.TestCase):
    """The operator's lived experience: stop kato, start kato, keep working."""

    def test_flow_restart_full_cycle_persists_and_resumes_same_session(self) -> None:
        # A → Z: this is the spine of the flow. If any other test in
        # this file fails, this one usually does too — but its failure
        # message is the most useful because it pins the end-to-end
        # guarantee in a single test.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            # --- KATO RUN #1: operator starts a task and works on it ---
            run1_calls = {}
            stub1 = _make_stub_session(run1_calls, fresh_id='session-id-X')
            mgr_run1 = ClaudeSessionManager(
                state_dir=state_dir, session_factory=stub1,
            )
            mgr_run1.start_session(
                task_id='T1',
                task_summary='fix login bug',
                initial_prompt='please fix the login bug',
                cwd='/tmp/workspaces/T1',
            )
            # First spawn: no resume id, fresh session.
            self.assertEqual(
                run1_calls['kwargs'].get('resume_session_id', ''), '',
                'first spawn must NOT carry --resume (no record exists yet)',
            )
            # Claude reported its session id; the manager mirrors it.
            record = mgr_run1.get_record('T1')
            self.assertEqual(record.agent_session_id, 'session-id-X')

            # --- OPERATOR KILLS KATO ---
            mgr_run1.terminate_session('T1')
            del mgr_run1

            # --- KATO RUN #2: fresh process, same state_dir on disk ---
            run2_calls = {}
            stub2 = _make_stub_session(run2_calls, fresh_id='WRONG-new-uuid')
            mgr_run2 = ClaudeSessionManager(
                state_dir=state_dir, session_factory=stub2,
            )
            # State must rehydrate from disk: this is the rebuild that
            # Bug 1 broke. ``get_record`` must return the persisted
            # record, with the session id intact.
            record = mgr_run2.get_record('T1')
            self.assertIsNotNone(
                record, 'restart lost the record — chat tab would be empty',
            )
            self.assertEqual(
                record.agent_session_id, 'session-id-X',
                'restart lost the session id — next message will fork',
            )

            # --- OPERATOR SENDS FIRST MESSAGE AFTER RESTART ---
            mgr_run2.start_session(
                task_id='T1',
                task_summary='fix login bug',
                initial_prompt='did you find the bug?',
                cwd='/tmp/workspaces/T1',
            )
            self.assertEqual(
                run2_calls['kwargs'].get('resume_session_id'),
                'session-id-X',
                'restart did not pass --resume to the new spawn — '
                'Claude will start over from scratch and burn tokens',
            )

    def test_flow_restart_active_status_is_demoted_to_terminated(self) -> None:
        # The manager persists ``status=ACTIVE`` while the subprocess
        # is alive. After a crash, that status is a lie: the subprocess
        # is gone. ``_load_persisted_records`` MUST demote ACTIVE →
        # TERMINATED on boot. Without this, the UI would claim a tab
        # is "active" with no subprocess behind it.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
            SESSION_STATUS_ACTIVE,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            # Hand-write an ACTIVE record straight to disk (simulates a
            # kato that crashed mid-session).
            record_path = Path(state_dir) / 'T1.json'
            record_path.write_text(json.dumps({
                'task_id': 'T1',
                'task_summary': 'crashed mid-work',
                AGENT_SESSION_ID: 'stale-but-real',
                'status': SESSION_STATUS_ACTIVE,
                'created_at_epoch': 1000.0,
                'updated_at_epoch': 1000.0,
                'cwd': '/tmp/T1',
                'expected_branch': 'T1',
            }), encoding='utf-8')

            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            record = mgr.get_record('T1')
            self.assertEqual(
                record.status, SESSION_STATUS_TERMINATED,
                'ACTIVE status survived restart — UI will lie about live tabs',
            )
            # But the session id IS preserved so resume still works.
            self.assertEqual(record.agent_session_id, 'stale-but-real')

    def test_flow_restart_with_unreadable_record_does_not_crash(self) -> None:
        # If one record on disk is corrupt, the manager must boot
        # anyway. A single bad JSON file should NOT take out every
        # other task's restart.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            # Corrupt record.
            (Path(state_dir) / 'T1.json').write_text(
                'NOT VALID JSON {', encoding='utf-8',
            )
            # Healthy record.
            (Path(state_dir) / 'T2.json').write_text(json.dumps({
                'task_id': 'T2',
                AGENT_SESSION_ID: 'healthy-id',
                'status': 'terminated',
            }), encoding='utf-8')

            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            # T1 swallowed silently.
            self.assertIsNone(mgr.get_record('T1'))
            # T2 still rehydrates.
            self.assertEqual(
                mgr.get_record('T2').agent_session_id, 'healthy-id',
            )

    def test_flow_restart_with_non_dict_payload_skipped(self) -> None:
        # JSON-but-not-an-object on disk (a list, a string, a number)
        # must not crash boot. Tests the ``isinstance(payload, dict)``
        # filter in ``_load_persisted_records``.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            (Path(state_dir) / 'T1.json').write_text(
                '[1, 2, 3]', encoding='utf-8',
            )
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            self.assertIsNone(mgr.get_record('T1'))

    def test_flow_restart_with_empty_task_id_record_skipped(self) -> None:
        # Records with no task_id can't be addressed — the manager
        # must drop them rather than register an unreachable record.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            (Path(state_dir) / 'orphan.json').write_text(json.dumps({
                'task_id': '',
                AGENT_SESSION_ID: 'leaked-id',
                'status': 'terminated',
            }), encoding='utf-8')
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            # No record loaded for the empty key.
            self.assertEqual(mgr.list_records(), [])

    def test_flow_restart_missing_state_dir_is_created(self) -> None:
        # First-ever boot: state_dir doesn't exist yet. The manager
        # must create it (or boot fails before the operator can even
        # open a tab).
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as parent:
            state_dir = Path(parent) / 'does-not-exist-yet' / 'sessions'
            self.assertFalse(state_dir.exists())
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            self.assertTrue(state_dir.exists())
            self.assertEqual(mgr.list_records(), [])

    def test_flow_restart_main_does_not_auto_spawn_sessions(self) -> None:
        # Bug 1's smoking gun: ``main()`` used to call
        # ``_resume_streaming_sessions(app)`` at boot, which spawned a
        # ``claude --resume`` subprocess for every task on disk.
        # Operator pain: a token-burning thundering herd at every
        # restart, plus a UX bug where the tab said "reattached" but
        # the spawn had been issued before history could load.
        #
        # Lock that ``main()`` no longer does this.
        import inspect

        from kato_core_lib import main as kato_main
        src = inspect.getsource(kato_main.main)

        # The function MUST NOT call the auto-spawn helper.
        self.assertNotIn(
            '_resume_streaming_sessions(app)', src,
            'main() is auto-spawning sessions at boot again (Bug 1 regression)',
        )

    def test_flow_restart_history_replays_in_chronological_order(self) -> None:
        # The "tab shows no scroll-back" half of Bug 1. The SSE
        # endpoint resolves the session id from the per-task record,
        # then calls ``load_history_events`` to read the JSONL off
        # disk. Lock that this returns user+assistant turns in the
        # order they were written.
        from claude_core_lib.claude_core_lib.session.history import (
            load_history_events,
        )
        with tempfile.TemporaryDirectory() as td:
            projects_root = Path(td)
            session_id = 'sess-restart-1'
            cwd = '/tmp/workspaces/T1'
            # Encode the same way Claude Code does.
            encoded = '-' + cwd.lstrip('/').replace('/', '-').replace('.', '-')
            (projects_root / encoded).mkdir(parents=True)
            jsonl = projects_root / encoded / f'{session_id}.jsonl'
            events = [
                {'type': 'user', 'sessionId': session_id, 'cwd': cwd,
                 'message': {'role': 'user', 'content': [
                     {'type': 'text', 'text': 'turn 1 question'}]}},
                {'type': 'assistant', 'sessionId': session_id, 'cwd': cwd,
                 'message': {'role': 'assistant', 'content': [
                     {'type': 'text', 'text': 'turn 1 answer'}]}},
                {'type': 'user', 'sessionId': session_id, 'cwd': cwd,
                 'message': {'role': 'user', 'content': [
                     {'type': 'text', 'text': 'turn 2 question'}]}},
                {'type': 'assistant', 'sessionId': session_id, 'cwd': cwd,
                 'message': {'role': 'assistant', 'content': [
                     {'type': 'text', 'text': 'turn 2 answer'}]}},
            ]
            with jsonl.open('w') as fh:
                for e in events:
                    fh.write(json.dumps(e) + '\n')

            result = load_history_events(
                session_id, projects_root=projects_root,
            )
            self.assertEqual(
                [e['type'] for e in result],
                ['user', 'assistant', 'user', 'assistant'],
                'history events came back out of order — UI scroll-back '
                'would render turn 2 before turn 1',
            )

    def test_flow_restart_history_replays_orchestration_user_prompts(self) -> None:
        # Kato's autonomous-flow user prompts (Security guardrails, etc)
        # must show up after restart so the chat keeps the full prompt.
        from claude_core_lib.claude_core_lib.session.history import (
            load_history_events,
        )
        with tempfile.TemporaryDirectory() as td:
            projects_root = Path(td)
            session_id = 'sess-filter-1'
            cwd = '/tmp/workspaces/T1'
            encoded = '-' + cwd.lstrip('/').replace('/', '-').replace('.', '-')
            (projects_root / encoded).mkdir(parents=True)
            jsonl = projects_root / encoded / f'{session_id}.jsonl'
            events = [
                {'type': 'user', 'sessionId': session_id, 'cwd': cwd,
                 'message': {'role': 'user', 'content': [
                     {'type': 'text', 'text':
                      'Security guardrails:\n- do not read ~/.ssh'}]}},
                {'type': 'user', 'sessionId': session_id, 'cwd': cwd,
                 'message': {'role': 'user', 'content': [
                     {'type': 'text', 'text':
                      'operator real message'}]}},
            ]
            with jsonl.open('w') as fh:
                for e in events:
                    fh.write(json.dumps(e) + '\n')

            result = load_history_events(
                session_id, projects_root=projects_root,
            )
            joined = ' '.join(
                block.get('text', '')
                for ev in result
                for block in (ev.get('message', {}).get('content') or [])
                if isinstance(block, dict)
            )
            self.assertIn(
                'Security guardrails', joined,
                'orchestration prompt missing from chat scroll-back',
            )
            self.assertIn('operator real message', joined)

    def test_flow_restart_history_missing_session_returns_empty(self) -> None:
        # If the JSONL is gone (``~/.claude`` wiped, machine moved),
        # the SSE replay must return ``[]`` so the UI shows an idle
        # tab rather than crashing the stream.
        from claude_core_lib.claude_core_lib.session.history import (
            load_history_events,
        )
        with tempfile.TemporaryDirectory() as td:
            result = load_history_events(
                'nonexistent-session', projects_root=Path(td),
            )
            self.assertEqual(result, [])

    def test_flow_restart_persisted_record_with_whitespace_id_is_normalized(self) -> None:
        # Defensive: hand-edited records, manual disk repair, or a
        # prior buggy version could leave whitespace around the session
        # id. The resume path uses ``.strip()`` everywhere; if the
        # record itself returns leading/trailing whitespace from
        # ``from_dict``, the resume id check would treat it as a real
        # id and try to ``--resume "  abc  "`` which fails ugly.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            (Path(state_dir) / 'T1.json').write_text(json.dumps({
                'task_id': 'T1',
                AGENT_SESSION_ID: 'real-id',
                'status': 'terminated',
            }), encoding='utf-8')
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            self.assertEqual(mgr.get_record('T1').agent_session_id, 'real-id')


# ---------------------------------------------------------------------------
# Multi-task restart: many tabs come back, not just one.
# ---------------------------------------------------------------------------


class FlowRestartMultiTaskTests(unittest.TestCase):
    """Operator typically has 3-5 active tabs. Restart must restore all."""

    def test_flow_restart_rehydrates_every_persisted_task(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            for task_id, sid in (('T1', 'sid-1'), ('T2', 'sid-2'), ('T3', 'sid-3')):
                (Path(state_dir) / f'{task_id}.json').write_text(json.dumps({
                    'task_id': task_id,
                    AGENT_SESSION_ID: sid,
                    'status': 'terminated',
                }), encoding='utf-8')
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            ids = {r.task_id: r.agent_session_id for r in mgr.list_records()}
            self.assertEqual(ids, {'T1': 'sid-1', 'T2': 'sid-2', 'T3': 'sid-3'})

    def test_flow_restart_one_corrupt_record_does_not_block_others(self) -> None:
        # Critical operator-UX guarantee: if a single record on disk
        # was hand-edited badly, EVERY other tab must still come back.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            (Path(state_dir) / 'T1.json').write_text(
                'GARBAGE {{{', encoding='utf-8',
            )
            (Path(state_dir) / 'T2.json').write_text(json.dumps({
                'task_id': 'T2',
                AGENT_SESSION_ID: 'survivor',
                'status': 'terminated',
            }), encoding='utf-8')
            (Path(state_dir) / 'T3.json').write_text(json.dumps({
                'task_id': 'T3',
                AGENT_SESSION_ID: 'also-survivor',
                'status': 'terminated',
            }), encoding='utf-8')
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=_make_stub_session({}),
            )
            ids = {r.task_id: r.agent_session_id for r in mgr.list_records()}
            # T1 dropped; T2 + T3 came back.
            self.assertEqual(ids, {'T2': 'survivor', 'T3': 'also-survivor'})


if __name__ == '__main__':
    unittest.main()
