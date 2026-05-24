"""Verify PlanningSessionRunner fires lifecycle hooks at the right edges.

These are wiring tests: they pin *which* hook points fire, *when*,
and *what* the event payload contains. The runner itself is owned
by :mod:`tests.test_hooks_runner`; here we only care that the
planning flow calls into it correctly.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.session.streaming import SessionEvent
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato_core_lib.hooks.config import HookPoint
from tests.utils import build_task


class _FakeRepo:
    def __init__(self, repo_id: str, local_path: str) -> None:
        self.id = repo_id
        self.local_path = local_path


class _FakePrepared:
    def __init__(self, repositories) -> None:
        self.repositories = repositories
        self.repository_branches: dict[str, str] = {}
        self.branch_name = 'feature/proj-1'


class _FakeSession:
    def __init__(self, terminal_event: SessionEvent | None) -> None:
        self.agent_session_id = 'fake-session-id'
        self._events = [terminal_event] if terminal_event else []
        self._is_alive = True
        self.terminal_event = terminal_event

    def poll_event(self, timeout: float = 0.0) -> SessionEvent | None:  # noqa: ARG002
        if self._events:
            event = self._events.pop(0)
            if event is not None and event.is_terminal:
                self._is_alive = False
            return event
        return None

    @property
    def is_alive(self) -> bool:
        return self._is_alive


class _FakeManager:
    def __init__(self, terminal_event: SessionEvent | None) -> None:
        self.statuses: list[str] = []
        self._session = _FakeSession(terminal_event)
        self._existing_session = None

    def start_session(self, **kwargs):  # noqa: ARG002
        return self._session

    def update_status(self, task_id: str, status: str) -> None:  # noqa: ARG002
        self.statuses.append(status)

    def get_session(self, task_id: str):  # noqa: ARG002
        return self._existing_session

    def get_record(self, task_id: str):  # noqa: ARG002
        return None

    def terminate_session(self, task_id: str) -> None:  # noqa: ARG002
        self._existing_session = None


def _success_event(text: str = 'done') -> SessionEvent:
    return SessionEvent(raw={
        'type': 'result', 'subtype': 'success', 'is_error': False,
        'result': text, 'session_id': 'live-id',
    })


def _error_event() -> SessionEvent:
    return SessionEvent(raw={
        'type': 'result', 'subtype': 'error', 'is_error': True,
        'result': 'oops', 'session_id': 'live-id',
    })


def _runner_with(manager, hook_runner):
    return PlanningSessionRunner(
        session_manager=manager,
        defaults=StreamingSessionDefaults(binary='claude'),
        hook_runner=hook_runner,
    )


class HooksWiringResumeChatTests(unittest.TestCase):

    def test_resume_chat_fires_user_prompt_submit_then_session_start(self) -> None:
        manager = _FakeManager(terminal_event=None)
        hook_runner = MagicMock()
        runner = _runner_with(manager, hook_runner)

        runner.resume_session_for_chat(
            task_id='T-1', message='hello', cwd='/work', task_summary='do it',
        )

        # Two calls, in order: user_prompt_submit then session_start.
        fired_points = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertEqual(
            fired_points,
            [HookPoint.USER_PROMPT_SUBMIT, HookPoint.SESSION_START],
        )
        # user_prompt_submit carries the raw message + task id.
        ups_event = hook_runner.fire.call_args_list[0].args[1]
        self.assertEqual(ups_event['task_id'], 'T-1')
        self.assertEqual(ups_event['message'], 'hello')
        self.assertEqual(ups_event['cwd'], '/work')
        self.assertFalse(ups_event['resumed'])
        # session_start carries the claude session id so observers
        # can correlate with on-disk JSONL.
        ss_event = hook_runner.fire.call_args_list[1].args[1]
        self.assertEqual(ss_event['task_id'], 'T-1')
        self.assertEqual(ss_event['agent_session_id'], 'fake-session-id')

    def test_resume_chat_hook_failure_does_not_kill_caller(self) -> None:
        # Defensive: a misbehaving hook runner must not bring down
        # the chat path — the operator's terminal stays usable.
        manager = _FakeManager(terminal_event=None)
        hook_runner = MagicMock()
        hook_runner.fire.side_effect = RuntimeError('bad hook')
        runner = _runner_with(manager, hook_runner)

        session = runner.resume_session_for_chat(
            task_id='T-1', message='hi', cwd='/work',
        )

        # Session still came back despite the hook explosion.
        self.assertIs(session, manager._session)


class HooksWiringRunToTerminalTests(unittest.TestCase):

    def test_implement_task_fires_session_start_then_session_end_completed(self) -> None:
        manager = _FakeManager(_success_event('shipped'))
        hook_runner = MagicMock()
        runner = _runner_with(manager, hook_runner)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        runner.implement_task(build_task(), prepared_task=prepared)

        fired_points = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertEqual(
            fired_points,
            [HookPoint.SESSION_START, HookPoint.SESSION_END],
        )
        end_event = hook_runner.fire.call_args_list[-1].args[1]
        self.assertEqual(end_event['reason'], 'completed')
        self.assertEqual(end_event['agent_session_id'], 'fake-session-id')

    def test_terminal_with_error_fires_session_end_with_reason_error(self) -> None:
        manager = _FakeManager(_error_event())
        hook_runner = MagicMock()
        runner = _runner_with(manager, hook_runner)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        with self.assertRaises(RuntimeError):
            runner.implement_task(build_task(), prepared_task=prepared)

        fired_points = [c.args[0] for c in hook_runner.fire.call_args_list]
        # session_start fires, then session_end with reason=error.
        self.assertEqual(fired_points[0], HookPoint.SESSION_START)
        self.assertEqual(fired_points[-1], HookPoint.SESSION_END)
        end_event = hook_runner.fire.call_args_list[-1].args[1]
        self.assertEqual(end_event['reason'], 'error')

    def test_no_terminal_event_fires_session_end_with_reason_no_terminal(self) -> None:
        # Manager that never returns a terminal event → runner times
        # out via _wait_for_terminal_event and we still get a
        # session_end so observers don't miss "agent died silently".
        manager = _FakeManager(terminal_event=None)
        # Force the wait loop to exit immediately by killing alive.
        manager._session._is_alive = False
        manager._session.terminal_event = None
        hook_runner = MagicMock()
        runner = _runner_with(manager, hook_runner)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        with self.assertRaises(RuntimeError):
            runner.implement_task(build_task(), prepared_task=prepared)

        fired_points = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertIn(HookPoint.SESSION_END, fired_points)
        end_event = [
            c.args[1] for c in hook_runner.fire.call_args_list
            if c.args[0] == HookPoint.SESSION_END
        ][0]
        self.assertEqual(end_event['reason'], 'no_terminal_event')


class HooksWiringReviewFixTerminationTests(unittest.TestCase):

    def test_fix_review_comments_terminates_then_fires_session_end_replaced(self) -> None:
        manager = _FakeManager(_success_event())
        # Existing session → fix_review_comments will terminate it
        # before starting the next subprocess.
        manager._existing_session = object()
        hook_runner = MagicMock()
        runner = _runner_with(manager, hook_runner)

        comment = MagicMock()
        comment.message = 'tighten this loop'
        comment.author = 'reviewer'
        comment.file_path = 'a.py'
        comment.line = 10
        comment.thread_id = 'C-1'

        runner.fix_review_comments(
            [comment], 'feature/x',
            task_id='T-9', repository_local_path='/tmp/client',
        )

        # First fire must be session_end (replaced), then session_start
        # for the new subprocess.
        fired_points = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertEqual(fired_points[0], HookPoint.SESSION_END)
        replaced_event = hook_runner.fire.call_args_list[0].args[1]
        self.assertEqual(replaced_event['reason'], 'replaced')
        self.assertEqual(replaced_event['task_id'], 'T-9')

    def test_no_existing_session_does_not_fire_replaced_session_end(self) -> None:
        # The terminate branch is skipped → no "replaced" event.
        manager = _FakeManager(_success_event())
        manager._existing_session = None
        hook_runner = MagicMock()
        runner = _runner_with(manager, hook_runner)

        comment = MagicMock()
        comment.message = 'fix this'
        comment.author = 'reviewer'
        comment.file_path = 'a.py'
        comment.line = 10
        comment.thread_id = 'C-1'

        runner.fix_review_comments(
            [comment], 'feature/x',
            task_id='T-9', repository_local_path='/tmp/client',
        )

        # No 'replaced' session_end — the first event is session_start.
        fired_points = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertEqual(fired_points[0], HookPoint.SESSION_START)
        self.assertNotIn(
            'replaced',
            [c.args[1].get('reason', '') for c in hook_runner.fire.call_args_list],
        )


class HooksWiringNoRunnerTests(unittest.TestCase):

    def test_no_hook_runner_means_no_crashes_anywhere(self) -> None:
        # The whole flow must work when the kato install has no
        # hooks configured — the runner argument is optional.
        manager = _FakeManager(_success_event())
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(binary='claude'),
            hook_runner=None,
        )
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])
        runner.implement_task(build_task(), prepared_task=prepared)
        # The point of the test is "no exception" — assertion is implicit.


if __name__ == '__main__':
    unittest.main()
