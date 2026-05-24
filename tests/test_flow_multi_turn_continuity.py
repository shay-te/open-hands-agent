"""Flow #4 — multiple messages on one task within a single kato run.

A-Z scenario (the operator's lived experience):

    1. Operator sends msg 1 to task T1 → kato spawns Claude fresh.
    2. Claude runs, finishes its turn, exits.
    3. Operator sends msg 2 to the same tab.
       → kato respawns Claude with ``--resume <same-id>``.
       → The follow-up message is delivered RAW (no continuity wrapper).
       → Claude has full conversation context loaded; no re-exploration.

The Bug 2 incident this defends: every follow-up message was kicking
off a new session AND being wrapped in another inventory/continuity
block, so Claude treated each turn as a brand-new task and re-walked
the workspace. Operator pain: token burn + slow turns + Claude
forgetting context from earlier in the conversation.

Two contracts have to hold together:
    A) ``planning_session_runner.resume_session_for_chat`` skips the
       ``prepend_chat_workspace_context`` wrapper when a session id is
       on the record.
    B) ``ClaudeSessionManager.start_session`` looks up the persisted
       session id and threads it into the spawn as ``resume_session_id``.

Either contract failing in isolation = Bug 2 returns. Tests below pin
both AND the wiring between them.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Stub session — same shape used by the other lifecycle tests.
# ---------------------------------------------------------------------------


def _make_stub_session(recorded: dict, *, fresh_id: str = 'fresh-uuid'):
    class _StubSession:
        def __init__(self, **kwargs):
            recorded.setdefault('all_spawns', []).append(kwargs)
            recorded['kwargs'] = kwargs
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
# The full A-Z scenario plus its adversarial neighbors.
# ---------------------------------------------------------------------------


class FlowMultiTurnContinuityTests(unittest.TestCase):
    """Within ONE kato run, every subsequent message must reuse the same
    session id AND be delivered without the workspace-context wrapper."""

    def _make_runner(self, manager):
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        return PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(),
        )

    def _make_manager(self, recorded, fresh_id='session-X'):
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return ClaudeSessionManager(
            state_dir=self._tmp.name,
            session_factory=_make_stub_session(recorded, fresh_id=fresh_id),
        )

    def test_flow_multi_turn_end_to_end_three_messages_share_one_session(self) -> None:
        # A → Z: three operator messages on one tab. Spawns 2 and 3 MUST
        # resume the session id captured during spawn 1.
        recorded = {}
        manager = self._make_manager(recorded, fresh_id='session-X')
        runner = self._make_runner(manager)

        # Message 1 — fresh spawn, no resume id on record yet.
        runner.resume_session_for_chat(
            task_id='T1', message='first message', cwd='/tmp/T1',
        )
        spawn1 = recorded['all_spawns'][0]
        self.assertEqual(
            spawn1.get('resume_session_id', ''), '',
            'first spawn carried a stray resume id',
        )

        # Subprocess exits → next message respawns.
        manager.terminate_session('T1')

        # Message 2 — resume id MUST equal what spawn 1 captured.
        runner.resume_session_for_chat(
            task_id='T1', message='follow-up two', cwd='/tmp/T1',
        )
        spawn2 = recorded['all_spawns'][1]
        self.assertEqual(
            spawn2.get('resume_session_id'), 'session-X',
            'message 2 forked a new session — Claude lost all prior context',
        )

        manager.terminate_session('T1')

        # Message 3 — still the same id (proves it doesn't drift turn-to-turn).
        runner.resume_session_for_chat(
            task_id='T1', message='follow-up three', cwd='/tmp/T1',
        )
        spawn3 = recorded['all_spawns'][2]
        self.assertEqual(
            spawn3.get('resume_session_id'), 'session-X',
            'message 3 drifted off the original session — context lost',
        )

    def test_flow_multi_turn_first_message_gets_workspace_context_wrapper(self) -> None:
        # The wrapper is correct on the FIRST spawn — Claude needs the
        # workspace inventory + continuity block to ground its work.
        # Lock that we still emit it when there's no session id to resume.
        recorded = {}
        manager = self._make_manager(recorded)
        runner = self._make_runner(manager)

        runner.resume_session_for_chat(
            task_id='T1', message='please fix the login bug', cwd='/tmp/T1',
        )

        prompt = recorded['starts'][0]
        # Operator's actual words must be in there.
        self.assertIn('please fix the login bug', prompt)
        # AND the prompt should be longer than the operator's message —
        # that's the wrapper adding inventory/continuity/guardrails.
        self.assertGreater(
            len(prompt), len('please fix the login bug') + 50,
            'first spawn did NOT include the workspace-context wrapper — '
            'Claude has no inventory or guardrails to ground its work',
        )

    def test_flow_multi_turn_second_message_is_raw_no_wrapper(self) -> None:
        # The exact regression case from Bug 2: on the second spawn,
        # the runner MUST NOT re-wrap the message. The CLI's --resume
        # already loaded all prior context, including the inventory.
        recorded = {}
        manager = self._make_manager(recorded, fresh_id='session-X')
        runner = self._make_runner(manager)

        runner.resume_session_for_chat(
            task_id='T1', message='m1', cwd='/tmp/T1',
        )
        manager.terminate_session('T1')

        operator_msg = 'now also add a unit test please'
        runner.resume_session_for_chat(
            task_id='T1', message=operator_msg, cwd='/tmp/T1',
        )
        second_prompt = recorded['starts'][1]

        # The second prompt must equal the operator's text exactly —
        # no inventory block, no continuity block, no forbidden-repos block.
        self.assertEqual(
            second_prompt, operator_msg,
            'follow-up message was re-wrapped with workspace context — '
            'Bug 2 has returned, Claude will re-explore the workspace',
        )

    def test_flow_multi_turn_record_without_session_id_uses_wrapper(self) -> None:
        # Adversarial edge: a record exists for T1 but ``agent_session_id``
        # is empty (e.g., the previous spawn errored before adoption).
        # The runner must treat this as a FIRST SPAWN and wrap the message.
        recorded = {}
        manager = self._make_manager(recorded)
        runner = self._make_runner(manager)

        # Seed a record with no session id.
        from claude_core_lib.claude_core_lib.session.manager import (
            PlanningSessionRecord, SESSION_STATUS_TERMINATED,
        )
        # Manually inject the empty-id record into the manager.
        with manager._lock:
            manager._records[manager._lookup_key('T1')] = PlanningSessionRecord(
                task_id='T1',
                task_summary='hung first spawn',
                agent_session_id='',
                status=SESSION_STATUS_TERMINATED,
            )

        operator_msg = 'try again please'
        runner.resume_session_for_chat(
            task_id='T1', message=operator_msg, cwd='/tmp/T1',
        )
        prompt = recorded['starts'][0]
        # Wrapper applied (prompt is longer than just the operator msg).
        self.assertGreater(
            len(prompt), len(operator_msg) + 50,
            'record-with-empty-session-id was treated as resumable — '
            'wrapper skipped but there is nothing for Claude to resume',
        )

    def test_flow_multi_turn_record_with_whitespace_session_id_uses_wrapper(self) -> None:
        # Adversarial edge: persisted record has session id ``'   '``
        # (hand-edited disk, prior buggy version). The runner strips
        # whitespace; if the check is naïve (``if record.agent_session_id``),
        # it would treat that as truthy and skip the wrapper — and then
        # also pass whitespace as the resume id, which fails ugly.
        recorded = {}
        manager = self._make_manager(recorded)
        runner = self._make_runner(manager)

        from claude_core_lib.claude_core_lib.session.manager import (
            PlanningSessionRecord, SESSION_STATUS_TERMINATED,
        )
        with manager._lock:
            manager._records[manager._lookup_key('T1')] = PlanningSessionRecord(
                task_id='T1',
                agent_session_id='   ',  # whitespace only
                status=SESSION_STATUS_TERMINATED,
            )

        operator_msg = 'are you there'
        runner.resume_session_for_chat(
            task_id='T1', message=operator_msg, cwd='/tmp/T1',
        )
        prompt = recorded['starts'][0]
        spawn = recorded['all_spawns'][0]
        self.assertGreater(
            len(prompt), len(operator_msg) + 50,
            'whitespace-only session id was treated as a real resume id',
        )
        self.assertEqual(spawn.get('resume_session_id'), '')

    def test_flow_multi_turn_rejects_empty_task_id(self) -> None:
        # Defensive: the runner validates inputs. Empty task id =>
        # ValueError. Bug-finder for any future change that drops the
        # ``if not normalized_task_id`` guard.
        recorded = {}
        manager = self._make_manager(recorded)
        runner = self._make_runner(manager)
        with self.assertRaises(ValueError):
            runner.resume_session_for_chat(task_id='', message='hi')

    def test_flow_multi_turn_rejects_empty_message(self) -> None:
        # Empty message after .strip() must reject too — Claude refuses
        # to start a turn with no input, and a silent send would hang
        # the tab.
        recorded = {}
        manager = self._make_manager(recorded)
        runner = self._make_runner(manager)
        with self.assertRaises(ValueError):
            runner.resume_session_for_chat(task_id='T1', message='   ')

    def test_flow_multi_turn_message_with_only_whitespace_after_strip(self) -> None:
        recorded = {}
        manager = self._make_manager(recorded)
        runner = self._make_runner(manager)
        with self.assertRaises(ValueError):
            runner.resume_session_for_chat(task_id='T1', message='\n\t  \n')

    def test_flow_multi_turn_runner_without_session_manager_uses_wrapper(self) -> None:
        # If the runner is constructed without a session manager (e.g.,
        # in a test fixture or a degraded mode), it has no way to look
        # up the persisted record — and must default to the wrapped
        # prompt, NOT skip the wrapper just because lookup is unavailable.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )

        recorded = {}

        class _MgrShim:
            """Bare shim — no get_record, so the runner's lookup yields None."""
            def __init__(self): self._sessions = {}

            def start_session(self, **kwargs):
                recorded['kwargs'] = kwargs
                # Match the manager's return shape: a stub session.
                return SimpleNamespace(
                    agent_session_id='', cwd=kwargs.get('cwd', ''),
                    task_id=kwargs.get('task_id', ''),
                    is_alive=True, is_working=False,
                )

            def get_record(self, _task_id):
                return None

        runner = PlanningSessionRunner(
            session_manager=_MgrShim(),
            defaults=StreamingSessionDefaults(),
        )
        msg = 'starting from scratch'
        runner.resume_session_for_chat(task_id='T1', message=msg, cwd='/tmp/T1')
        # No record → no resume id → wrapper applied.
        prompt = recorded['kwargs']['initial_prompt']
        self.assertGreater(
            len(prompt), len(msg) + 50,
            'runner with no session record skipped the wrapper — '
            'Claude has no inventory to ground its work',
        )


# ---------------------------------------------------------------------------
# The manager-side half of the contract: refresh + persist after first spawn.
# ---------------------------------------------------------------------------


class FlowMultiTurnManagerSidePersistenceTests(unittest.TestCase):
    """Without these, the runner's lookup of ``record.agent_session_id``
    would always return empty and every spawn would be treated as fresh."""

    def _make_manager(self, recorded, fresh_id='session-X'):
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return ClaudeSessionManager(
            state_dir=self._tmp.name,
            session_factory=_make_stub_session(recorded, fresh_id=fresh_id),
        )

    def test_flow_multi_turn_first_spawn_id_is_refreshed_into_record(self) -> None:
        # The kato side calls ``get_record`` before the next spawn —
        # that path runs ``_with_refreshed_session_id`` which mirrors
        # the live session's id onto the record. If THAT chain breaks,
        # the runner sees ``agent_session_id=''`` and Bug 2 returns.
        recorded = {}
        manager = self._make_manager(recorded, fresh_id='session-from-claude')
        manager.start_session(
            task_id='T1', task_summary='go', initial_prompt='go',
            cwd='/tmp/T1',
        )
        record = manager.get_record('T1')
        self.assertEqual(record.agent_session_id, 'session-from-claude')

    def test_flow_multi_turn_session_id_survives_terminate(self) -> None:
        # After ``terminate_session``, the live subprocess is gone but
        # the RECORD (and its session id) must remain so the next spawn
        # can resume. If terminate also wipes the id, every follow-up
        # forks.
        recorded = {}
        manager = self._make_manager(recorded, fresh_id='surviving-id')
        manager.start_session(
            task_id='T1', task_summary='go', initial_prompt='go',
            cwd='/tmp/T1',
        )
        # Refresh so the id is on the record.
        manager.get_record('T1')
        manager.terminate_session('T1')
        record = manager.get_record('T1')
        self.assertIsNotNone(record)
        self.assertEqual(
            record.agent_session_id, 'surviving-id',
            'terminate wiped the session id — next message will fork',
        )


if __name__ == '__main__':
    unittest.main()
