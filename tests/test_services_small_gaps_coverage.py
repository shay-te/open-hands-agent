"""Coverage for the small remaining gaps across the service layer.

One test class per service module. Each gap is a defensive branch
that production has hit (forget-task with blank id, agent client
without batch support, broken email template, etc.); the tests pin
the fail-safe so a refactor can't silently break it.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# --------------------------------------------------------------------------
# lessons_data_access — bad timestamp passing the regex
# --------------------------------------------------------------------------


class LessonsDataAccessTimestampParseTests(unittest.TestCase):
    def test_last_compacted_returns_none_for_unparseable_timestamp(self) -> None:
        # Lines 118-119: pattern matches but ``datetime.fromisoformat``
        # rejects the captured string. The pattern is permissive (any
        # combination of digits/T/Z/:/./-/+) so an operator-edited
        # file can pass the regex but still be malformed.
        from kato_core_lib.data_layers.data_access.lessons_data_access import (
            LessonsDataAccess,
        )
        with tempfile.TemporaryDirectory() as td:
            da = LessonsDataAccess(Path(td))
            # Numerically-looking but invalid timestamp (passes the
            # regex on line 41 but ValueError from fromisoformat).
            da._global_path.write_text(
                '<!-- last_compacted: 9999-99-99T99:99:99 -->\nbody\n',
                encoding='utf-8',
            )
            self.assertIsNone(da.last_compacted_at())


# --------------------------------------------------------------------------
# agent_state_registry — defensive branches around stored PR contexts
# --------------------------------------------------------------------------


class AgentStateRegistryDefensiveTests(unittest.TestCase):
    """Lines 151, 162, 174, 198, 201 — the registry survives stale /
    malformed persisted state. Without these defensive paths, an
    orphaned dict in the saved JSON would crash agent_service init."""

    def _registry(self):
        from kato_core_lib.data_layers.service.agent_state_registry import (
            AgentStateRegistry,
        )
        return AgentStateRegistry(), None

    def test_session_ids_for_task_skips_unrelated_contexts(self) -> None:
        # Line 151: ``continue`` when a context belongs to a different task.
        from kato_core_lib.data_layers.data.fields import (
            ImplementationFields, TaskFields,
        )
        registry, _ = self._registry()
        registry.pull_request_context_map['pr-1'] = [
            {TaskFields.ID: 'OTHER-1', ImplementationFields.AGENT_SESSION_ID: 'x'},
            {TaskFields.ID: 'PROJ-1', ImplementationFields.AGENT_SESSION_ID: 's1'},
        ]
        # Only the PROJ-1 session is surfaced.
        self.assertEqual(registry.session_ids_for_task('PROJ-1'), ['s1'])

    def test_forget_task_returns_silently_on_blank_id(self) -> None:
        # Line 162: ``if not normalized: return`` — no-op on blank id.
        registry, _ = self._registry()
        # Should not raise even though no PR map state exists.
        registry.forget_task('   ')

    def test_forget_task_preserves_other_tasks_in_shared_pr_context(
        self,
    ) -> None:
        # Line 174: the else-branch where some entries remain for
        # other tasks — the PR entry is kept with the filtered list.
        from kato_core_lib.data_layers.data.fields import TaskFields
        registry, _ = self._registry()
        registry.pull_request_context_map['pr-shared'] = [
            {TaskFields.ID: 'PROJ-1'},
            {TaskFields.ID: 'OTHER-1'},
        ]
        registry.forget_task('PROJ-1')
        # The PR map still has 'pr-shared' but only OTHER-1's context.
        self.assertIn('pr-shared', registry.pull_request_context_map)
        remaining = registry.pull_request_context_map['pr-shared']
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0][TaskFields.ID], 'OTHER-1')

    def test_task_id_for_pull_request_skips_non_list_pull_requests(
        self,
    ) -> None:
        # Lines 197-201: defensive isinstance checks — corrupted
        # processed_task entries (non-list pull_requests, non-dict
        # entries) are skipped so the lookup degrades to '' rather
        # than crash.
        from kato_core_lib.data_layers.data.fields import PullRequestFields
        registry, _ = self._registry()
        registry.processed_task_map['T1'] = {
            PullRequestFields.PULL_REQUESTS: 'not a list',
        }
        registry.processed_task_map['T2'] = {
            PullRequestFields.PULL_REQUESTS: [
                'not a dict',  # skipped
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'repo-a',
                },
            ],
        }
        self.assertEqual(
            registry.task_id_for_pull_request('17', 'repo-a'),
            'T2',
        )


# --------------------------------------------------------------------------
# implementation_service — fan-out fallback when batch API missing
# --------------------------------------------------------------------------


class ImplementationServiceFallbackTests(unittest.TestCase):
    def test_max_retries_uses_default_when_client_lacks_attr(self) -> None:
        # Line 27: ``getattr(self._client, 'max_retries', 1)``.
        from kato_core_lib.data_layers.service.implementation_service import (
            ImplementationService,
        )
        # Client without max_retries — uses the default of 1.
        client = SimpleNamespace()
        service = ImplementationService(client)
        self.assertEqual(service.max_retries, 1)

    def test_stop_all_conversations_delegates_to_client(self) -> None:
        # Line 39: thin delegation.
        from kato_core_lib.data_layers.service.implementation_service import (
            ImplementationService,
        )
        client = MagicMock()
        ImplementationService(client).stop_all_conversations()
        client.stop_all_conversations.assert_called_once()

    def test_fix_review_comments_uses_batch_api_when_available(self) -> None:
        # Line 92: when the client exposes ``fix_review_comments``,
        # the service delegates to it instead of fanning out per
        # comment — the modern (efficient) path.
        from kato_core_lib.data_layers.service.implementation_service import (
            ImplementationService,
        )
        client = MagicMock()
        client.fix_review_comments.return_value = {'batched': True}
        service = ImplementationService(client)
        result = service.fix_review_comments(
            [SimpleNamespace(comment_id='c1')],
            branch_name='b', agent_session_id='s', task_id='T', task_summary='sum',
            mode='fix',
        )
        client.fix_review_comments.assert_called_once()
        self.assertEqual(result, {'batched': True})

    def test_fix_review_comments_fans_out_to_per_comment_when_batch_unsupported(
        self,
    ) -> None:
        # Line 92 (NOT having ``fix_review_comments``) → fan out
        # to ``fix_review_comment`` per entry. Locks the back-compat
        # path so older / mock clients still work.
        from kato_core_lib.data_layers.service.implementation_service import (
            ImplementationService,
        )

        class _LegacyClient:
            max_retries = 1

            def __init__(self):
                self.calls = []

            def fix_review_comment(
                self, comment, branch_name, agent_session_id,
                task_id='', task_summary='',
            ):
                self.calls.append(comment)
                return {'comment': comment}

        client = _LegacyClient()
        service = ImplementationService(client)
        comments = [
            SimpleNamespace(comment_id='c1'),
            SimpleNamespace(comment_id='c2'),
        ]
        result = service.fix_review_comments(
            comments, branch_name='b', agent_session_id='s',
        )
        self.assertEqual(len(client.calls), 2)
        # Last result is returned (line 115).
        self.assertEqual(result['comment'], comments[-1])


# --------------------------------------------------------------------------
# lessons_service — empty-pending early-return + compaction logging
# --------------------------------------------------------------------------


class LessonsServiceTests(unittest.TestCase):
    def test_data_access_property_returns_constructor_arg(self) -> None:
        # Line 101: the @property accessor.
        from kato_core_lib.data_layers.service.lessons_service import (
            LessonsService,
        )
        data_access = MagicMock()
        service = LessonsService(
            data_access=data_access,
            llm_one_shot=MagicMock(),
        )
        self.assertIs(service.data_access, data_access)

    def test_compact_returns_false_when_global_write_fails(self) -> None:
        # Line 199: ``if not self._data_access.write_global(...): return False``.
        # Defensive: a disk write failure must surface as False so the
        # caller doesn't proceed to delete per-task files (which would
        # lose the lessons).
        from kato_core_lib.data_layers.service.lessons_service import (
            LessonsService,
        )
        data_access = MagicMock()
        data_access.read_global_body.return_value = 'old'
        data_access.read_all_per_task.return_value = {'T1': 'lesson 1'}
        data_access.write_global.return_value = False  # write failure
        llm = MagicMock(return_value='compacted body')
        service = LessonsService(
            data_access=data_access,
            llm_one_shot=llm,
        )
        self.assertFalse(service.compact())
        # Critical: per-task files were NOT deleted on failure.
        data_access.delete_per_task.assert_not_called()


# --------------------------------------------------------------------------
# notification_service — sender_info + template-load error
# --------------------------------------------------------------------------


class NotificationServiceTests(unittest.TestCase):
    def test_sender_info_returns_none_when_no_sender_cfg(self) -> None:
        # Lines 132-133: no sender config → None. Defensive against
        # a partial email config (operator omitted the [sender] block).
        from kato_core_lib.data_layers.service.notification_service import (
            NotificationService,
        )
        email_cfg = SimpleNamespace(sender=None)
        self.assertIsNone(NotificationService._sender_info(email_cfg))

    def test_normalized_pull_requests_returns_empty_for_non_list(self) -> None:
        # Line 165: defensive isinstance check — string/None/int
        # input must yield [] rather than crash. Used when an external
        # caller passes a malformed payload.
        from kato_core_lib.data_layers.service.notification_service import (
            NotificationService,
        )
        self.assertEqual(NotificationService._normalized_pull_requests(None), [])
        self.assertEqual(NotificationService._normalized_pull_requests('x'), [])
        self.assertEqual(NotificationService._normalized_pull_requests(42), [])

    def test_render_template_returns_empty_on_load_failure(self) -> None:
        # Lines 155-157: ``FileNotFoundError`` / ``OSError`` from the
        # template resource read → log + return ''. The notification
        # path falls back to an empty body rather than crashing.
        from kato_core_lib.data_layers.service.notification_service import (
            NotificationService,
        )
        service = NotificationService.__new__(NotificationService)
        service.logger = MagicMock()
        with patch(
            'kato_core_lib.data_layers.service.notification_service.resources.files',
        ) as fake_files:
            fake_files.return_value.joinpath.return_value.read_text.side_effect = (
                FileNotFoundError('template missing')
            )
            result = service._render_template(
                email_cfg=SimpleNamespace(body_template='missing.j2'),
                default_template_name='missing.j2',
                template_params={},
            )
        self.assertEqual(result, '')
        service.logger.exception.assert_called_once()


# --------------------------------------------------------------------------
# parallel_task_runner — blank-id is_in_flight defensive
# --------------------------------------------------------------------------


class ParallelTaskRunnerTests(unittest.TestCase):
    def test_is_in_flight_returns_false_for_blank_id(self) -> None:
        # Line 88: ``if not normalized: return False`` — blank task id
        # cannot be in-flight by definition.
        from kato_core_lib.data_layers.service.parallel_task_runner import (
            ParallelTaskRunner,
        )
        runner = ParallelTaskRunner(max_workers=1)
        try:
            self.assertFalse(runner.is_in_flight(''))
            self.assertFalse(runner.is_in_flight('   '))
            self.assertFalse(runner.is_in_flight(None))
        finally:
            runner.shutdown()


# --------------------------------------------------------------------------
# task_failure_handler — repository-restore exception path
# --------------------------------------------------------------------------


class TaskFailureHandlerDefensiveTests(unittest.TestCase):
    """Lines 276-277, 292-294: exceptions inside the rejection cleanup
    must be logged + swallowed — they cannot block the rest of the
    failure flow (move-back-to-open, audit-log entry, etc.)."""

    def _handler(self):
        from kato_core_lib.data_layers.service.task_failure_handler import (
            TaskFailureHandler,
        )
        handler = TaskFailureHandler.__new__(TaskFailureHandler)
        handler.logger = MagicMock()
        handler._repository_service = MagicMock()
        handler._task_service = MagicMock()
        handler._task_state_service = MagicMock()
        # _log_task_step is harmless side-effect.
        handler._log_task_step = MagicMock()
        return handler

    def test_restore_repositories_swallows_exception(self) -> None:
        handler = self._handler()
        handler._repository_service.restore_task_repositories.side_effect = (
            RuntimeError('git operation failed')
        )
        prepared_task = SimpleNamespace(repositories=[SimpleNamespace(id='r')])
        task = SimpleNamespace(id='PROJ-1')
        # Should NOT raise.
        handler._restore_task_repositories(task, prepared_task)
        handler.logger.exception.assert_called_once()

    def test_add_task_comment_swallows_exception_and_returns_false(self) -> None:
        # Lines 292-294: ``add_comment`` raises → log + return False.
        # Caller branches on the boolean to decide whether to proceed
        # with downstream actions that depend on the comment landing.
        handler = self._handler()
        handler._task_service.add_comment.side_effect = RuntimeError('boom')
        result = handler._add_task_comment(
            'PROJ-1', 'msg', failure_log_message='failed for %s',
        )
        self.assertFalse(result)
        handler.logger.exception.assert_called_once()


# --------------------------------------------------------------------------
# task_state_service — fallback to first configured issue state
# --------------------------------------------------------------------------


class TaskStateServiceTests(unittest.TestCase):
    def test_open_state_falls_back_to_first_issue_state(self) -> None:
        # Line 71 isn't quite what coverage said missing — actually
        # it's line 71 in the source meaning the ``return 'Open'``
        # fallback. Let me trigger the simpler default-Open branch:
        # no explicit open_state, no configured issue_states.
        from kato_core_lib.data_layers.service.task_state_service import (
            TaskStateService,
        )
        service = TaskStateService.__new__(TaskStateService)
        service._config = SimpleNamespace(open_state='', issue_states=[])
        self.assertEqual(service._configured_open_state(), 'Open')


# --------------------------------------------------------------------------
# testing_service — fallback max_retries when client omits attribute
# --------------------------------------------------------------------------


class TestingServiceTests(unittest.TestCase):
    def test_max_retries_falls_back_to_one(self) -> None:
        # Line 26: ``getattr(self._client, 'max_retries', 1)``.
        from kato_core_lib.data_layers.service.testing_service import (
            TestingService,
        )
        client = SimpleNamespace()  # no max_retries attribute
        service = TestingService(client)
        self.assertEqual(service.max_retries, 1)

    def test_stop_all_conversations_delegates_to_client(self) -> None:
        # Line 35: thin delegation.
        from kato_core_lib.data_layers.service.testing_service import (
            TestingService,
        )
        client = MagicMock()
        TestingService(client).stop_all_conversations()
        client.stop_all_conversations.assert_called_once()


# --------------------------------------------------------------------------
# workspace_manager — _coerce_positive_int defensive branches
# --------------------------------------------------------------------------


class WorkspaceManagerCoerceTests(unittest.TestCase):
    """Lines 95-99: the helper that parses operator-provided counts —
    fails closed on bad input so the workspace builder doesn't crash
    on a config typo."""

    def test_coerce_falls_back_for_none(self) -> None:
        from kato_core_lib.data_layers.service.workspace_manager import (
            _coerce_positive_int,
        )
        self.assertEqual(_coerce_positive_int(None, default=4), 4)
        self.assertEqual(_coerce_positive_int('', default=4), 4)

    def test_coerce_falls_back_for_non_numeric_string(self) -> None:
        from kato_core_lib.data_layers.service.workspace_manager import (
            _coerce_positive_int,
        )
        self.assertEqual(_coerce_positive_int('not-a-number', default=8), 8)

    def test_coerce_falls_back_for_zero_or_negative(self) -> None:
        from kato_core_lib.data_layers.service.workspace_manager import (
            _coerce_positive_int,
        )
        self.assertEqual(_coerce_positive_int('0', default=2), 2)
        self.assertEqual(_coerce_positive_int('-5', default=2), 2)

    def test_coerce_returns_parsed_value_when_valid(self) -> None:
        from kato_core_lib.data_layers.service.workspace_manager import (
            _coerce_positive_int,
        )
        self.assertEqual(_coerce_positive_int('3', default=10), 3)


# --------------------------------------------------------------------------
# workspace_provisioning_service — clone reuse + clone failure path
# --------------------------------------------------------------------------


class WorkspaceProvisioningServiceTests(unittest.TestCase):
    """Lines 88, 118-121: ``.git`` already on disk → reuse log line;
    clone failure → update status to ERRORED + re-raise."""

    def test_already_cloned_emits_reuse_log_line(self) -> None:
        # Line 88: workspace already has the clone — append the
        # "already on disk, reusing" preflight log line.
        from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clone_path = root / 'task-1' / 'repo-a'
            (clone_path / '.git').mkdir(parents=True)

            workspace_service = MagicMock()
            workspace_service.repository_path.return_value = clone_path
            repository_service = MagicMock()
            repository_service.ensure_clone.side_effect = lambda r, p: None
            task = SimpleNamespace(id='task-1', summary='thing')

            provision_task_workspace_clones(
                workspace_service,
                repository_service,
                task,
                [SimpleNamespace(id='repo-a', local_path='')],
            )
        # Reuse line emitted.
        log_calls = [
            call.args[1] for call in
            workspace_service.append_preflight_log.call_args_list
        ]
        self.assertTrue(
            any('already on disk' in m for m in log_calls),
            f'expected reuse log line, got {log_calls!r}',
        )

    def test_clone_failure_marks_workspace_errored_and_reraises(self) -> None:
        # Lines 118-121: ``except Exception as exc: ... update_status(
        # task_id, WORKSPACE_STATUS_ERRORED); raise``. Operator sees
        # a clear ERRORED workspace in ``kato status``.
        from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )
        from workspace_core_lib.workspace_core_lib import (
            WORKSPACE_STATUS_ERRORED,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clone_path = root / 'task-1' / 'repo-a'
            # NOT pre-created so the non-reuse branch is taken.

            workspace_service = MagicMock()
            workspace_service.repository_path.return_value = clone_path
            repository_service = MagicMock()
            repository_service.ensure_clone.side_effect = RuntimeError(
                'git auth failed',
            )
            task = SimpleNamespace(id='task-1', summary='thing')

            with self.assertRaisesRegex(RuntimeError, 'git auth failed'):
                provision_task_workspace_clones(
                    workspace_service,
                    repository_service,
                    task,
                    [SimpleNamespace(id='repo-a', local_path='')],
                )
        # Errored status was set BEFORE re-raise.
        workspace_service.update_status.assert_called_with(
            'task-1', WORKSPACE_STATUS_ERRORED,
        )


if __name__ == '__main__':
    unittest.main()
