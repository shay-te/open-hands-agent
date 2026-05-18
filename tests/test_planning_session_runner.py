from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.session.manager import (
    SESSION_STATUS_REVIEW,
    SESSION_STATUS_TERMINATED,
)
from claude_core_lib.claude_core_lib.session.streaming import SessionEvent
from kato_core_lib.data_layers.data.fields import ImplementationFields
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    SessionStoppedByUserError,
    StreamingSessionDefaults,
)
from tests.utils import build_task


class _FakeRepo:
    def __init__(self, repo_id: str, local_path: str) -> None:
        self.id = repo_id
        self.local_path = local_path


class _FakePrepared:
    def __init__(self, repositories: list[_FakeRepo]) -> None:
        self.repositories = repositories
        self.repository_branches: dict[str, str] = {}
        self.branch_name = 'feature/proj-1'


class _FakeSession:
    def __init__(self, terminal_event: SessionEvent | None) -> None:
        self.claude_session_id = 'fake-session-id'
        self._events_to_emit = [terminal_event] if terminal_event else []
        self._is_alive = True
        self.terminal_event = terminal_event

    def poll_event(self, timeout: float = 0.0) -> SessionEvent | None:  # noqa: ARG002
        if self._events_to_emit:
            event = self._events_to_emit.pop(0)
            if event is not None and event.is_terminal:
                self._is_alive = False
            return event
        return None

    @property
    def is_alive(self) -> bool:
        return self._is_alive


class _FakeManager:
    def __init__(
        self,
        terminal_event: SessionEvent | None,
        *,
        record_status: str | None = None,
    ) -> None:
        self.start_kwargs: dict | None = None
        self.statuses: list[str] = []
        self._session = _FakeSession(terminal_event)
        self._record_status = record_status

    def start_session(self, **kwargs):
        self.start_kwargs = kwargs
        return self._session

    def update_status(self, task_id: str, status: str) -> None:  # noqa: ARG002
        self.statuses.append(status)

    def get_session(self, task_id: str):  # noqa: ARG002
        # Return None so fix_review_comment skips its terminate-prior-session
        # branch and goes straight to start_session — what the docker_mode_on
        # forwarding test cares about.
        return None

    def get_record(self, task_id: str):  # noqa: ARG002
        if self._record_status is None:
            # No persisted record → first-spawn path through
            # resume_session_for_chat, which wraps the message with the
            # forbidden / inventory / continuity preamble. (When a record
            # IS persisted the message goes through raw — covered by
            # test_resume_session_for_chat_sends_raw_message_when_session_id_persisted
            # in test_services_medium_coverage.py.)
            return None
        from types import SimpleNamespace
        return SimpleNamespace(status=self._record_status)


def _terminal(*, is_error: bool = False, result: str = 'all done') -> SessionEvent:
    return SessionEvent(
        raw={
            'type': 'result',
            'subtype': 'success' if not is_error else 'error',
            'is_error': is_error,
            'result': result,
            'session_id': 'live-id',
        },
    )


class PlanningSessionRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = StreamingSessionDefaults(
            binary='claude',
            model='claude-opus-4-7',
            permission_mode='acceptEdits',
            allowed_tools='Edit,Write',
        )

    def test_implement_task_starts_session_with_prompt_and_returns_result(self) -> None:
        manager = _FakeManager(_terminal(result='shipped it'))
        runner = PlanningSessionRunner(session_manager=manager, defaults=self.defaults)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        result = runner.implement_task(build_task(), prepared_task=prepared)

        # Session is started with the right cwd + the chosen model.
        self.assertEqual(manager.start_kwargs['cwd'], '/tmp/client')
        self.assertEqual(manager.start_kwargs['model'], 'claude-opus-4-7')
        # Initial prompt was filled with the implementation guidance.
        self.assertIn('Implement task PROJ-1', manager.start_kwargs['initial_prompt'])
        # Status promotion to review happened after success.
        self.assertEqual(manager.statuses, [SESSION_STATUS_REVIEW])

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'fake-session-id')
        self.assertEqual(result[ImplementationFields.MESSAGE], 'shipped it')

    def test_implement_task_prompt_marks_ignored_repositories_out_of_bounds(self) -> None:
        manager = _FakeManager(_terminal(result='shipped it'))
        runner = PlanningSessionRunner(session_manager=manager, defaults=self.defaults)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        with patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            runner.implement_task(build_task(), prepared_task=prepared)

        prompt = manager.start_kwargs['initial_prompt']
        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('- secret-client', prompt)
        self.assertIn('Do not access them with Read, Glob, Grep, Bash', prompt)
        self.assertIn('Execution protocol for forbidden repositories', prompt)

    def test_resume_session_for_chat_prepends_forbidden_repositories(self) -> None:
        manager = _FakeManager(_terminal(result='ignored'))
        runner = PlanningSessionRunner(session_manager=manager, defaults=self.defaults)

        with patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            runner.resume_session_for_chat(
                task_id='PROJ-1',
                message='please continue',
                cwd='/tmp/client',
                task_summary='summary',
            )

        prompt = manager.start_kwargs['initial_prompt']
        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('secret-client', prompt)
        self.assertTrue(prompt.endswith('please continue'))

    def test_implement_task_raises_when_terminal_reports_error(self) -> None:
        manager = _FakeManager(_terminal(is_error=True, result='Credit balance is too low'))
        runner = PlanningSessionRunner(session_manager=manager, defaults=self.defaults)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        with self.assertRaisesRegex(RuntimeError, 'Credit balance is too low'):
            runner.implement_task(build_task(), prepared_task=prepared)
        self.assertEqual(manager.statuses, [SESSION_STATUS_TERMINATED])

    def test_implement_task_raises_when_session_ends_without_terminal_event(self) -> None:
        manager = _FakeManager(terminal_event=None)
        # Mark the fake session dead so the runner exits the wait loop quickly.
        manager._session._is_alive = False
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=self.defaults,
            max_wait_seconds=0.1,
            clock=lambda: time.monotonic(),
        )
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        with self.assertRaisesRegex(RuntimeError, 'ended without a result event'):
            runner.implement_task(build_task(), prepared_task=prepared)
        self.assertEqual(manager.statuses, [SESSION_STATUS_TERMINATED])

    def test_implement_task_raises_session_stopped_when_record_is_terminated(self) -> None:
        # When the user clicks Stop, terminate_session() already sets the
        # record status to TERMINATED before the planning thread wakes up.
        # _run_to_terminal should raise SessionStoppedByUserError (not RuntimeError)
        # so the caller can skip the failure handler (which would re-queue the task).
        manager = _FakeManager(
            terminal_event=None,
            record_status=SESSION_STATUS_TERMINATED,
        )
        manager._session._is_alive = False
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=self.defaults,
            max_wait_seconds=0.1,
            clock=lambda: time.monotonic(),
        )
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        with self.assertRaises(SessionStoppedByUserError):
            runner.implement_task(build_task(), prepared_task=prepared)
        # Status is already TERMINATED on the record; update_status must NOT
        # be called again (that would overwrite a more-recent status update).
        self.assertEqual(manager.statuses, [])


class PlanningSessionRunnerDockerModeTests(unittest.TestCase):
    """``KATO_CLAUDE_DOCKER`` plumbing through the runner."""

    def _build_claude_cfg(self, **overrides):
        cfg = MagicMock()
        cfg.bypass_permissions = overrides.get('bypass_permissions', False)
        cfg.binary = 'claude'
        cfg.model = ''
        cfg.allowed_tools = ''
        cfg.disallowed_tools = ''
        cfg.max_turns = None
        cfg.effort = ''
        cfg.architecture_doc_path = ''
        return cfg

    def test_build_defaults_picks_up_docker_mode_on(self) -> None:
        defaults = PlanningSessionRunner._build_defaults(
            self._build_claude_cfg(), docker_mode_on=True,
        )
        self.assertTrue(defaults.docker_mode_on)

    def test_build_defaults_default_is_off(self) -> None:
        defaults = PlanningSessionRunner._build_defaults(self._build_claude_cfg())
        self.assertFalse(defaults.docker_mode_on)

    def test_from_config_threads_docker_mode_on_to_defaults(self) -> None:
        open_cfg = MagicMock()
        open_cfg.claude = self._build_claude_cfg()
        runner = PlanningSessionRunner.from_config(
            open_cfg, 'claude', session_manager=MagicMock(),
            docker_mode_on=True,
        )
        self.assertIsNotNone(runner)
        self.assertTrue(runner._defaults.docker_mode_on)

    def test_implement_task_forwards_docker_mode_on_to_session_manager(self) -> None:
        manager = _FakeManager(_terminal(result='ok'))
        defaults = StreamingSessionDefaults(
            binary='claude',
            permission_mode='acceptEdits',
            docker_mode_on=True,
        )
        runner = PlanningSessionRunner(session_manager=manager, defaults=defaults)
        prepared = _FakePrepared([_FakeRepo('client', '/tmp/client')])

        runner.implement_task(build_task(), prepared_task=prepared)

        self.assertIs(manager.start_kwargs['docker_mode_on'], True)

    def test_fix_review_comment_forwards_docker_mode_on_to_session_manager(self) -> None:
        """Review-fix spawn path also threads docker_mode_on.

        ``fix_review_comment`` calls ``start_session`` independently of
        ``implement_task``. Without this assertion, a future refactor
        could drop the forward on the review-fix path while leaving
        the implementation path correct.
        """
        from tests.utils import build_review_comment

        manager = _FakeManager(_terminal(result='fix done'))
        defaults = StreamingSessionDefaults(
            binary='claude',
            permission_mode='acceptEdits',
            docker_mode_on=True,
        )
        runner = PlanningSessionRunner(session_manager=manager, defaults=defaults)

        runner.fix_review_comment(
            build_review_comment(),
            'feature/proj-1',
            task_id='PROJ-1',
            task_summary='wire the button',
            repository_local_path='/tmp/client',
        )

        self.assertIs(manager.start_kwargs['docker_mode_on'], True)


if __name__ == '__main__':
    unittest.main()
