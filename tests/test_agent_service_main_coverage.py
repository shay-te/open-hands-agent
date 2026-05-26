"""Coverage for the major method clusters in ``AgentService``.

Focuses on:
- Constructor validation (required-argument refusals)
- shutdown cleanup (per-step exception swallow)
- ``process_assigned_task`` short-circuits (triage, wait-planning, preflight)
- Comment-store thin wrappers (add/resolve/mark/reopen/delete/list)
- ``approve_push`` / ``is_awaiting_push_approval``
- Cleanup of stale planning sessions + workspace cleanup
- ``_task_pull_request_id`` lookup paths
"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.agent_service import AgentService


def _kwargs(**overrides):
    """Build minimum valid kwargs for AgentService(...)."""
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


class ConstructorValidationTests(unittest.TestCase):
    def test_rejects_missing_task_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'task_service is required'):
            AgentService(**_kwargs(task_service=None))

    def test_rejects_missing_task_state_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'task_state_service is required'):
            AgentService(**_kwargs(task_state_service=None))

    def test_rejects_missing_implementation_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'implementation_service is required'):
            AgentService(**_kwargs(implementation_service=None))

    def test_rejects_missing_testing_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'testing_service is required'):
            AgentService(**_kwargs(testing_service=None))

    def test_rejects_missing_repository_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'repository_service is required'):
            AgentService(**_kwargs(repository_service=None))

    def test_rejects_missing_notification_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'notification_service is required'):
            AgentService(**_kwargs(notification_service=None))

    def test_rejects_state_registry_mismatch(self) -> None:
        # ``state_registry must match review_comment_service.state_registry``.
        review = MagicMock()
        review.state_registry = MagicMock()  # one identity
        wrong = MagicMock()  # different
        with self.assertRaisesRegex(ValueError, 'must match'):
            AgentService(**_kwargs(
                review_comment_service=review,
                state_registry=wrong,
            ))

    def test_uses_review_comment_state_registry_when_provided(self) -> None:
        review = MagicMock()
        registry = MagicMock()
        review.state_registry = registry
        service = AgentService(**_kwargs(review_comment_service=review))
        self.assertIs(service._state_registry, registry)


class NotificationServicePropertyTests(unittest.TestCase):
    def test_returns_constructor_arg(self) -> None:
        notification = MagicMock()
        service = AgentService(**_kwargs(notification_service=notification))
        self.assertIs(service.notification_service, notification)


class ShutdownTests(unittest.TestCase):
    def test_swallows_parallel_runner_shutdown_exception(self) -> None:
        runner = MagicMock()
        runner.shutdown.side_effect = RuntimeError('runner fail')
        service = AgentService(**_kwargs(parallel_task_runner=runner))
        service.logger = MagicMock()
        service.shutdown()
        service.logger.exception.assert_called()

    def test_swallows_implementation_stop_exception(self) -> None:
        impl = MagicMock()
        impl.stop_all_conversations.side_effect = RuntimeError('stop fail')
        service = AgentService(**_kwargs(implementation_service=impl))
        service.logger = MagicMock()
        service.shutdown()
        service.logger.exception.assert_called()

    def test_swallows_testing_stop_exception(self) -> None:
        testing = MagicMock()
        testing.stop_all_conversations.side_effect = RuntimeError('stop fail')
        service = AgentService(**_kwargs(testing_service=testing))
        service.logger = MagicMock()
        service.shutdown()
        service.logger.exception.assert_called()

    def test_swallows_session_manager_shutdown_exception(self) -> None:
        session = MagicMock()
        session.shutdown.side_effect = RuntimeError('session fail')
        service = AgentService(**_kwargs(session_manager=session))
        service.logger = MagicMock()
        service.shutdown()
        service.logger.exception.assert_called()


class GetAssignedTasksTests(unittest.TestCase):
    def test_delegates_to_task_service(self) -> None:
        task_service = MagicMock()
        task_service.get_assigned_tasks.return_value = ['task-a']
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertEqual(service.get_assigned_tasks(), ['task-a'])


class ParallelTaskRunnerPropertyTests(unittest.TestCase):
    def test_returns_constructor_arg(self) -> None:
        runner = MagicMock()
        service = AgentService(**_kwargs(parallel_task_runner=runner))
        self.assertIs(service.parallel_task_runner, runner)


class WarmUpRepositoryInventoryTests(unittest.TestCase):
    def test_spawns_warmup_thread_silently_on_exception(self) -> None:
        repo = MagicMock()
        repo._ensure_repositories.side_effect = RuntimeError('boom')
        service = AgentService(**_kwargs(repository_service=repo))
        # Must not raise; thread swallows internally.
        service.warm_up_repository_inventory()
        # Give the background thread a chance to run.
        import time
        time.sleep(0.05)


class GetNewPullRequestCommentsTests(unittest.TestCase):
    def test_runs_cleanup_then_delegates(self) -> None:
        review = MagicMock()
        review.state_registry = MagicMock()
        review.get_new_pull_request_comments.return_value = ['c1']
        service = AgentService(**_kwargs(review_comment_service=review))
        with patch.object(service, '_cleanup_done_task_conversations') as cleanup:
            result = service.get_new_pull_request_comments()
        cleanup.assert_called_once()
        self.assertEqual(result, ['c1'])


class NoAutoDeletePolicyTests(unittest.TestCase):
    """Operator policy invariant — kato NEVER auto-deletes on disk.

    The next person who tries to wire an auto-delete back into the
    cleanup paths trips these. The only legitimate path that wipes a
    workspace clone or session record is the operator's explicit
    ``DELETE /api/sessions/<task_id>/workspace`` route in the
    webserver. The boot prune (called from ``cleanup_done_tasks``)
    and the scan-tick prune (called via ``get_new_pull_request_comments``)
    must only flip workspace status to ``done`` so the UI can grey
    out the tab's status circle.
    """

    def _service_with_one_stale_task(self):
        # Builds a service where exactly one record is stale: the
        # platform has nothing assigned or in review, but there's a
        # session record + workspace on disk for ``UNA-STALE``.
        # ``updated_at_epoch`` is set FAR in the past so the
        # active/provisioning "freshness" grace doesn't accidentally
        # protect it from the cleanup path under test.
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
        )
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = []
        task_service.get_assigned_tasks.return_value = []
        session = MagicMock()
        session.list_records.return_value = [
            SimpleNamespace(task_id='UNA-STALE'),
        ]
        workspace = MagicMock()
        workspace.list_workspaces.return_value = [
            SimpleNamespace(
                task_id='UNA-STALE',
                status=WORKSPACE_STATUS_ACTIVE,
                updated_at_epoch=1.0,  # ancient → past the TTL grace
            ),
        ]
        registry = MagicMock()
        registry.tracked_task_ids.return_value = set()
        review_svc = MagicMock()
        review_svc.state_registry = registry
        service = AgentService(**_kwargs(
            task_service=task_service,
            session_manager=session,
            workspace_manager=workspace,
            review_comment_service=review_svc,
        ))
        # Make sure the TTL grace is shorter than ``now - 1.0`` so the
        # workspace falls into the 'stale' bucket; default config
        # might leave TTL at 0 (=disabled) which protects everything.
        service._review_workspace_ttl_seconds = 60
        service.logger = MagicMock()
        # The live-session check would treat a MagicMock session as
        # alive (truthy + ``is_alive`` truthy by default), keeping
        # the workspace 'protected'. Force it to look gone.
        session.get_session.return_value = None
        return service, session, workspace

    def test_boot_cleanup_never_deletes_workspace(self) -> None:
        service, _session, workspace = self._service_with_one_stale_task()
        service.cleanup_done_tasks()
        workspace.delete.assert_not_called()

    def test_boot_cleanup_never_removes_session_record(self) -> None:
        service, session, _workspace = self._service_with_one_stale_task()
        service.cleanup_done_tasks()
        # ``terminate_session`` would kill the live subprocess; with
        # ``remove_record=True`` it ALSO wipes the on-disk record so
        # the tab vanishes. Neither variant is allowed from a boot /
        # scan-tick auto-cleanup path.
        session.terminate_session.assert_not_called()

    def test_scan_tick_cleanup_never_deletes_workspace(self) -> None:
        # ``_cleanup_done_task_conversations`` is what runs every
        # 30s/180s scan tick (called from ``get_new_pull_request_comments``).
        # Same no-delete invariant must hold.
        service, _session, workspace = self._service_with_one_stale_task()
        service._cleanup_done_task_conversations()
        workspace.delete.assert_not_called()

    def test_scan_tick_cleanup_never_removes_session_record(self) -> None:
        service, session, _workspace = self._service_with_one_stale_task()
        service._cleanup_done_task_conversations()
        session.terminate_session.assert_not_called()

    def test_stale_workspace_gets_marked_done_for_grey_circle(self) -> None:
        # Positive assertion: instead of deletion, the status flip
        # (``done``) is what the UI uses to grey out the tab dot.
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_DONE,
        )
        service, _session, workspace = self._service_with_one_stale_task()
        service.cleanup_done_tasks()
        workspace.update_status.assert_called_with(
            'UNA-STALE', WORKSPACE_STATUS_DONE,
        )

    def test_delete_workspace_silent_is_a_noop(self) -> None:
        # The old auto-delete helper is kept only as a deprecated
        # no-op. Calling it must NOT reach the workspace manager —
        # no matter what the manager would do if invoked.
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._delete_workspace_silent('T1')
        workspace.delete.assert_not_called()

    def test_terminate_session_silent_is_a_noop(self) -> None:
        # Same for the session-record auto-removal helper.
        session = MagicMock()
        service = AgentService(**_kwargs(session_manager=session))
        service._terminate_session_silent('T1')
        session.terminate_session.assert_not_called()


class CleanupDoneTasksBootEntrypointTests(unittest.TestCase):
    def test_public_cleanup_delegates_to_internal(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_cleanup_done_task_conversations') as c:
            service.cleanup_done_tasks()
        c.assert_called_once_with()

    def test_done_task_with_stale_session_record_is_marked_done_not_deleted(self) -> None:
        # Operator policy change: NEVER auto-delete a session record.
        # Mirrors UNA-1201's exact on-disk shape (record left behind
        # for a ticket no longer assigned or in review). The boot
        # prune must NOT terminate the session, NOT remove the
        # record, and NOT touch the workspace folder — it only flips
        # the workspace status to ``done`` so the UI dims the tab's
        # status circle. Operator wipes via the explicit DELETE
        # endpoint when (and if) they want to.
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_DONE,
        )
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = []
        task_service.get_assigned_tasks.return_value = []
        session = MagicMock()
        session.list_records.return_value = [
            SimpleNamespace(task_id='UNA-1201', status='active'),
        ]
        workspace = MagicMock()
        workspace.list_workspaces.return_value = []
        registry = MagicMock()
        registry.tracked_task_ids.return_value = set()
        review_svc = MagicMock()
        review_svc.state_registry = registry
        service = AgentService(**_kwargs(
            task_service=task_service,
            session_manager=session,
            workspace_manager=workspace,
            review_comment_service=review_svc,
        ))
        service.logger = MagicMock()
        service.cleanup_done_tasks()
        session.terminate_session.assert_not_called()
        workspace.delete.assert_not_called()
        workspace.update_status.assert_called_with(
            'UNA-1201', WORKSPACE_STATUS_DONE,
        )


class CleanupDoneTaskConversationsTests(unittest.TestCase):
    def test_swallows_review_tasks_exception(self) -> None:
        task_service = MagicMock()
        task_service.get_review_tasks.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(task_service=task_service))
        service.logger = MagicMock()
        service._cleanup_done_task_conversations()
        service.logger.warning.assert_called()

    def test_swallows_delete_conversation_exception(self) -> None:
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = []
        registry = MagicMock()
        registry.tracked_task_ids.return_value = {'T1'}
        registry.session_ids_for_task.return_value = ['s1']
        impl = MagicMock()
        impl.delete_conversation.side_effect = RuntimeError('delete fail')
        review = MagicMock()
        review.state_registry = registry
        service = AgentService(**_kwargs(
            task_service=task_service,
            implementation_service=impl,
            review_comment_service=review,
        ))
        service.logger = MagicMock()
        service._cleanup_done_task_conversations()
        # The conversation-delete failure is logged at WARNING.
        service.logger.warning.assert_called()


class CleanupDonePlanningSessionsTests(unittest.TestCase):
    def test_returns_early_when_no_managers(self) -> None:
        service = AgentService(**_kwargs())
        # No raise.
        service._cleanup_done_planning_sessions({'T1'})

    def test_swallows_assigned_tasks_exception(self) -> None:
        task_service = MagicMock()
        task_service.get_assigned_tasks.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(
            task_service=task_service,
            session_manager=MagicMock(),
        ))
        service.logger = MagicMock()
        service._cleanup_done_planning_sessions({'T1'})
        service.logger.warning.assert_called()

    def test_marks_stale_workspaces_done_without_deleting(self) -> None:
        # Policy: NEVER auto-delete. A workspace whose ticket has
        # left both assigned and review buckets is flipped to
        # ``done`` (the UI greys out its status circle) but the
        # disk clone, the session record, and the tab all stay.
        # Operator wipes via the explicit DELETE endpoint only.
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_DONE,
        )
        task_service = MagicMock()
        task_service.get_assigned_tasks.return_value = []
        session = MagicMock()
        session.list_records.return_value = [
            SimpleNamespace(task_id='STALE-1'),
        ]
        workspace = MagicMock()
        workspace.list_workspaces.return_value = []
        service = AgentService(**_kwargs(
            task_service=task_service,
            session_manager=session,
            workspace_manager=workspace,
        ))
        service.logger = MagicMock()
        service._cleanup_done_planning_sessions(set())
        # No delete, no record removal, no subprocess kill.
        session.terminate_session.assert_not_called()
        workspace.delete.assert_not_called()
        # Just the status flip so the UI dims the circle.
        workspace.update_status.assert_called_with(
            'STALE-1', WORKSPACE_STATUS_DONE,
        )


class StalePlanningTaskIdsTests(unittest.TestCase):
    def test_swallows_session_list_records_exception(self) -> None:
        session = MagicMock()
        session.list_records.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(session_manager=session))
        service.logger = MagicMock()
        result = service._stale_planning_task_ids({'T1'})
        self.assertEqual(result, set())
        service.logger.exception.assert_called()

    def test_swallows_workspace_list_exception(self) -> None:
        workspace = MagicMock()
        workspace.list_workspaces.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service.logger = MagicMock()
        result = service._stale_planning_task_ids({'T1'})
        # No raise; just an empty result.
        self.assertEqual(result, set())

    def test_protects_active_and_provisioning_workspaces_from_cleanup(
        self,
    ) -> None:
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
        )
        workspace = MagicMock()
        workspace.list_workspaces.return_value = [
            SimpleNamespace(
                task_id='ACTIVE-1', status=WORKSPACE_STATUS_ACTIVE,
                updated_at_epoch=0.0,
            ),
            SimpleNamespace(
                task_id='STALE-1', status='done',
                updated_at_epoch=0.0,
            ),
        ]
        service = AgentService(**_kwargs(workspace_manager=workspace))
        # live_task_ids is empty — STALE-1 is fully eligible.
        result = service._stale_planning_task_ids(set())
        self.assertIn('STALE-1', result)
        self.assertNotIn('ACTIVE-1', result)

    def test_aged_review_workspace_is_protected_not_stale(self) -> None:
        """Regression for the UNA-232 "disappeared while on verify" bug.

        A review-state clone, however old, must NEVER be swept — the
        operator may still be verifying it. (Previously a review clone
        older than the TTL was force-cleaned, which deleted the clone
        and made the task vanish from the UI mid-review.)
        """
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_REVIEW,
        )
        import time
        workspace = MagicMock()
        old_epoch = time.time() - 7200  # 2 hours ago, well past TTL
        workspace.list_workspaces.return_value = [
            SimpleNamespace(
                task_id='OLD-REVIEW', status=WORKSPACE_STATUS_REVIEW,
                updated_at_epoch=old_epoch,
            ),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=workspace,
            review_workspace_ttl_seconds=3600.0,  # 1 hour
        ))
        # Even when the ticket is absent from this scan's fetch, the
        # review clone on disk must stay protected by status.
        result = service._stale_planning_task_ids(set())
        self.assertNotIn('OLD-REVIEW', result)


class ColdActiveWorkspaceCleanupTests(unittest.TestCase):
    """Regression: a finished task's workspace stays ``active`` forever
    (nothing reliably resets it). The old unconditional "active ⇒
    never clean" guard shielded done tasks permanently — the tab
    never disappeared even after restart. Now active/provisioning is
    protected only when plausibly still being driven (live session
    OR updated within the grace window).
    """

    TTL = 3600.0  # 1h grace

    def _ws(self, **overrides):
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
        )
        base = dict(
            task_id='UNA-1201', status=WORKSPACE_STATUS_ACTIVE,
            updated_at_epoch=time.time() - (self.TTL + 600),  # cold
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _service(self, ws_records, *, live_session_for=None):
        workspace = MagicMock()
        workspace.list_workspaces.return_value = ws_records
        session = MagicMock()
        session.list_records.return_value = []

        def get_session(task_id):
            if live_session_for and task_id == live_session_for:
                return SimpleNamespace(is_alive=True)
            return None
        session.get_session.side_effect = get_session
        return AgentService(**_kwargs(
            workspace_manager=workspace,
            session_manager=session,
            review_workspace_ttl_seconds=self.TTL,
        ))

    def test_cold_active_done_task_is_cleaned(self) -> None:
        # The UNA-1201 bug: active, ticket not live, no live session,
        # last touched > grace ago → MUST be stale now.
        svc = self._service([self._ws()])
        result = svc._stale_planning_task_ids(set())  # ticket not live
        self.assertIn('UNA-1201', result)

    def test_fresh_active_task_is_protected(self) -> None:
        # kato just flipped the ticket to In Progress and is driving
        # it — workspace updated seconds ago. Must NOT be cleaned
        # even though it's momentarily not in assigned/review.
        svc = self._service([self._ws(updated_at_epoch=time.time() - 5)])
        result = svc._stale_planning_task_ids(set())
        self.assertNotIn('UNA-1201', result)

    def test_active_with_live_session_is_protected_even_when_cold(self) -> None:
        # A long autonomous run: workspace timestamp is old but a
        # live subprocess proves kato is on it. Keep it.
        svc = self._service(
            [self._ws()], live_session_for='UNA-1201',
        )
        result = svc._stale_planning_task_ids(set())
        self.assertNotIn('UNA-1201', result)

    def test_ttl_zero_keeps_legacy_protect_all_active(self) -> None:
        # Operator disabled age-based cleanup (TTL=0): every
        # active/provisioning workspace stays protected regardless
        # of age, as before.
        workspace = MagicMock()
        workspace.list_workspaces.return_value = [self._ws()]
        session = MagicMock()
        session.list_records.return_value = []
        session.get_session.return_value = None
        svc = AgentService(**_kwargs(
            workspace_manager=workspace,
            session_manager=session,
            review_workspace_ttl_seconds=0.0,
        ))
        result = svc._stale_planning_task_ids(set())
        self.assertNotIn('UNA-1201', result)

    def test_cold_active_but_ticket_still_live_is_kept(self) -> None:
        # Even cold + no session: if the ticket IS still in the
        # assigned/review bucket, the live-norm subtraction keeps it.
        svc = self._service([self._ws()])
        result = svc._stale_planning_task_ids({svc._norm_task_id('UNA-1201')})
        self.assertNotIn('UNA-1201', result)

    def test_missing_timestamp_active_is_protected(self) -> None:
        # No recorded updated_at (legacy record) → treated as fresh
        # so we never nuke a timestamp-less active workspace.
        svc = self._service([self._ws(updated_at_epoch=0.0)])
        result = svc._stale_planning_task_ids(set())
        self.assertNotIn('UNA-1201', result)

    def test_get_session_raising_is_treated_as_no_live_session(self) -> None:
        # session_manager.get_session blowing up must NOT crash the
        # sweep or falsely protect the workspace: _has_live_session
        # swallows it and reports "no live session", so a cold active
        # leftover whose ticket isn't live is still cleaned.
        svc = self._service([self._ws()])  # cold active, ticket not live
        svc._session_manager.get_session.side_effect = RuntimeError('boom')
        result = svc._stale_planning_task_ids(set())
        self.assertIn('UNA-1201', result)


class TerminateSessionSilentTests(unittest.TestCase):
    # ``_terminate_session_silent`` is now a deprecated no-op (operator
    # policy: NEVER auto-delete a session record). The previous tests
    # exercised the old terminate-and-remove behaviour; with the noop
    # we only assert that no session method is invoked on any path.

    def test_does_not_touch_session_manager_even_when_present(self) -> None:
        session = MagicMock()
        service = AgentService(**_kwargs(session_manager=session))
        service.logger = MagicMock()
        service._terminate_session_silent('T1')
        # Whole point of the no-op: nothing happens to the live
        # session. The tab + record stay so the UI can grey them out.
        session.terminate_session.assert_not_called()
        service.logger.exception.assert_not_called()


class DeleteWorkspaceSilentTests(unittest.TestCase):
    # ``_delete_workspace_silent`` is now a deprecated no-op (operator
    # policy: NEVER auto-delete a workspace folder). Callers wanting
    # to flag a workspace as done use ``_mark_workspace_done_silent``;
    # the explicit DELETE endpoint stays for operator-triggered wipes.

    def test_does_not_touch_workspace_manager_even_when_present(self) -> None:
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service.logger = MagicMock()
        service._delete_workspace_silent('T1')
        workspace.delete.assert_not_called()
        service.logger.exception.assert_not_called()


class MarkWorkspaceDoneSilentTests(unittest.TestCase):
    def test_noop_when_workspace_manager_none(self) -> None:
        service = AgentService(**_kwargs())
        service._mark_workspace_done_silent('T1')  # no raise

    def test_flips_status_to_done(self) -> None:
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_DONE,
        )
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._mark_workspace_done_silent('T1')
        workspace.update_status.assert_called_with('T1', WORKSPACE_STATUS_DONE)

    def test_swallows_update_status_exception(self) -> None:
        workspace = MagicMock()
        workspace.update_status.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service.logger = MagicMock()
        service._mark_workspace_done_silent('T1')
        service.logger.exception.assert_called()

    def test_skips_when_manager_has_no_update_status(self) -> None:
        # Legacy / partial managers (e.g. test doubles without the
        # method) must not blow up — silent no-op.
        workspace = SimpleNamespace()  # no ``update_status`` attr
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._mark_workspace_done_silent('T1')  # no raise


class UpdateWorkspaceStatusAfterPublishTests(unittest.TestCase):
    def test_noop_when_workspace_manager_none(self) -> None:
        service = AgentService(**_kwargs())
        service._update_workspace_status_after_publish('T1', {'status': 'x'})

    def test_noop_when_publish_result_blank(self) -> None:
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._update_workspace_status_after_publish('T1', None)
        workspace.update_status.assert_not_called()

    def test_updates_to_review_on_ready_for_review(self) -> None:
        from kato_core_lib.data_layers.data.fields import StatusFields
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_REVIEW,
        )
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._update_workspace_status_after_publish(
            'T1', {StatusFields.STATUS: StatusFields.READY_FOR_REVIEW},
        )
        workspace.update_status.assert_called_with('T1', WORKSPACE_STATUS_REVIEW)

    def test_updates_to_errored_on_partial_failure(self) -> None:
        from kato_core_lib.data_layers.data.fields import StatusFields
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ERRORED,
        )
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._update_workspace_status_after_publish(
            'T1', {StatusFields.STATUS: StatusFields.PARTIAL_FAILURE},
        )
        workspace.update_status.assert_called_with('T1', WORKSPACE_STATUS_ERRORED)

    def test_swallows_update_exception(self) -> None:
        from kato_core_lib.data_layers.data.fields import StatusFields
        workspace = MagicMock()
        workspace.update_status.side_effect = RuntimeError('update fail')
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service.logger = MagicMock()
        service._update_workspace_status_after_publish(
            'T1', {StatusFields.STATUS: StatusFields.READY_FOR_REVIEW},
        )
        service.logger.exception.assert_called()


class ThinDelegatesTests(unittest.TestCase):
    def test_handle_pull_request_comment_delegates(self) -> None:
        review = MagicMock()
        review.state_registry = MagicMock()
        review.handle_pull_request_comment.return_value = {'ok': True}
        service = AgentService(**_kwargs(review_comment_service=review))
        self.assertEqual(
            service.handle_pull_request_comment({'p': 1}), {'ok': True},
        )

    def test_process_review_comment_delegates(self) -> None:
        review = MagicMock()
        review.state_registry = MagicMock()
        review.process_review_comment.return_value = {'ok': True}
        service = AgentService(**_kwargs(review_comment_service=review))
        self.assertEqual(
            service.process_review_comment(SimpleNamespace()), {'ok': True},
        )

    def test_process_review_comment_batch_delegates(self) -> None:
        review = MagicMock()
        review.state_registry = MagicMock()
        review.process_review_comment_batch.return_value = [{'ok': True}]
        service = AgentService(**_kwargs(review_comment_service=review))
        self.assertEqual(
            service.process_review_comment_batch([SimpleNamespace()]),
            [{'ok': True}],
        )

    def test_task_id_for_review_comment_delegates(self) -> None:
        review = MagicMock()
        review.state_registry = MagicMock()
        review.task_id_for_comment.return_value = 'PROJ-1'
        service = AgentService(**_kwargs(review_comment_service=review))
        self.assertEqual(
            service.task_id_for_review_comment(SimpleNamespace()), 'PROJ-1',
        )


class ProcessAssignedTaskShortCircuitsTests(unittest.TestCase):
    def test_triage_short_circuit_returns_result(self) -> None:
        triage = MagicMock()
        triage.handle_task.return_value = {'status': 'triaged'}
        service = AgentService(**_kwargs(triage_service=triage))
        from kato_core_lib.data_layers.data.task import Task
        result = service.process_assigned_task(Task(id='PROJ-1'))
        self.assertEqual(result, {'status': 'triaged'})

    def test_wait_planning_short_circuit_returns_result(self) -> None:
        wait = MagicMock()
        wait.handle_task.return_value = {'status': 'planning'}
        service = AgentService(**_kwargs(wait_planning_service=wait))
        from kato_core_lib.data_layers.data.task import Task
        result = service.process_assigned_task(Task(id='PROJ-1'))
        self.assertEqual(result, {'status': 'planning'})

    def test_returns_when_preflight_skips_task(self) -> None:
        preflight = MagicMock()
        preflight.prepare_task_execution_context.return_value = None
        service = AgentService(**_kwargs(task_preflight_service=preflight))
        from kato_core_lib.data_layers.data.task import Task
        result = service.process_assigned_task(Task(id='PROJ-1'))
        self.assertIsNone(result)

    def test_returns_dict_when_preflight_returns_dict(self) -> None:
        preflight = MagicMock()
        preflight.prepare_task_execution_context.return_value = {'status': 'skipped'}
        service = AgentService(**_kwargs(task_preflight_service=preflight))
        from kato_core_lib.data_layers.data.task import Task
        result = service.process_assigned_task(Task(id='PROJ-1'))
        self.assertEqual(result, {'status': 'skipped'})


class TaskHasWaitBeforePushTagTests(unittest.TestCase):
    def test_returns_true_when_tag_present(self) -> None:
        from kato_core_lib.data_layers.data.fields import TaskTags
        from kato_core_lib.data_layers.data.task import Task
        self.assertTrue(AgentService._task_has_wait_before_push_tag(
            Task(id='T1', tags=[TaskTags.WAIT_BEFORE_GIT_PUSH]),
        ))

    def test_returns_false_when_tag_absent(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        self.assertFalse(AgentService._task_has_wait_before_push_tag(
            Task(id='T1', tags=['other-tag']),
        ))


class PauseForPushApprovalTests(unittest.TestCase):
    def test_stashes_pending_publish_and_posts_comment(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

        task_service = MagicMock()
        workspace = MagicMock()
        service = AgentService(**_kwargs(
            task_service=task_service,
            workspace_manager=workspace,
        ))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        result = service._pause_for_push_approval(
            Task(id='T1'), prepared, {'success': True},
        )
        self.assertEqual(result['status'], 'awaiting_push_approval')
        self.assertEqual(result['task_id'], 'T1')
        task_service.add_comment.assert_called_once()
        workspace.update_status.assert_called_once()

    def test_swallows_add_comment_failure(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

        task_service = MagicMock()
        task_service.add_comment.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(task_service=task_service))
        service.logger = MagicMock()
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        service._pause_for_push_approval(
            Task(id='T1'), prepared, {'success': True},
        )
        service.logger.exception.assert_called()

    def test_swallows_workspace_status_update_failure(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

        workspace = MagicMock()
        workspace.update_status.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service.logger = MagicMock()
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        service._pause_for_push_approval(
            Task(id='T1'), prepared, {'success': True},
        )
        service.logger.exception.assert_called()


class ApprovePushTests(unittest.TestCase):
    def test_returns_none_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertIsNone(service.approve_push(''))

    def test_returns_none_when_no_pending_publish(self) -> None:
        service = AgentService(**_kwargs())
        self.assertIsNone(service.approve_push('T1'))

    def test_publishes_pending_task(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

        publisher = MagicMock()
        publisher.publish_task_execution.return_value = {'status': 'published'}
        service = AgentService(**_kwargs(task_publisher=publisher))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        # Pre-stash a pending publish.
        service._pending_publish['T1'] = (
            Task(id='T1'), prepared, {'success': True},
        )
        result = service.approve_push('T1')
        self.assertEqual(result, {'status': 'published'})


class IsAwaitingPushApprovalTests(unittest.TestCase):
    def test_returns_false_for_blank_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.is_awaiting_push_approval(''))

    def test_returns_true_when_pending(self) -> None:
        service = AgentService(**_kwargs())
        service._pending_publish['T1'] = ('task', 'prep', 'exec')
        self.assertTrue(service.is_awaiting_push_approval('T1'))


class CommentStoreForTests(unittest.TestCase):
    def test_returns_none_when_workspace_manager_missing(self) -> None:
        service = AgentService(**_kwargs())
        self.assertIsNone(service._comment_store_for('T1'))

    def test_returns_none_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs(workspace_manager=MagicMock()))
        self.assertIsNone(service._comment_store_for(''))

    def test_returns_none_on_workspace_path_exception(self) -> None:
        workspace = MagicMock()
        workspace.workspace_path.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(workspace_manager=workspace))
        self.assertIsNone(service._comment_store_for('T1'))

    def test_returns_none_when_workspace_dir_missing(self) -> None:
        workspace = MagicMock()
        workspace_dir = MagicMock()
        workspace_dir.is_dir.return_value = False
        workspace.workspace_path.return_value = workspace_dir
        service = AgentService(**_kwargs(workspace_manager=workspace))
        self.assertIsNone(service._comment_store_for('T1'))

    def test_returns_local_comment_store_when_workspace_exists(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            workspace = MagicMock()
            workspace.workspace_path.return_value = Path(td)
            service = AgentService(**_kwargs(workspace_manager=workspace))
            store = service._comment_store_for('T1')
        self.assertIsNotNone(store)


class ListTaskCommentsTests(unittest.TestCase):
    def test_returns_empty_when_store_missing(self) -> None:
        service = AgentService(**_kwargs())
        self.assertEqual(service.list_task_comments('T1'), [])

    def test_returns_per_repo_when_repo_id_given(self) -> None:
        from kato_core_lib.comment_core_lib import CommentRecord

        service = AgentService(**_kwargs())
        store = MagicMock()
        record = CommentRecord(
            id='c1', body='hi', repo_id='r1', author='a', source='local',
        )
        store.list_for_repo.return_value = [record]
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.list_task_comments('T1', 'r1')
        self.assertEqual(len(result), 1)
        store.list_for_repo.assert_called_once_with('r1')

    def test_returns_all_when_no_repo_id(self) -> None:
        from kato_core_lib.comment_core_lib import CommentRecord

        service = AgentService(**_kwargs())
        store = MagicMock()
        record = CommentRecord(
            id='c1', body='hi', repo_id='r1', author='a', source='local',
        )
        store.list.return_value = [record]
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.list_task_comments('T1')
        self.assertEqual(len(result), 1)
        store.list.assert_called_once()


class AddTaskCommentTests(unittest.TestCase):
    def test_returns_error_when_no_workspace(self) -> None:
        service = AgentService(**_kwargs())
        result = service.add_task_comment(
            'T1', repo_id='r1', file_path='f.py', body='comment',
        )
        self.assertFalse(result['ok'])
        self.assertIn('no workspace', result['error'])

    def test_returns_error_when_add_raises_value_error(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.add.side_effect = ValueError('bad body')
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.add_task_comment(
                'T1', repo_id='r1', file_path='f.py', body='',
            )
        self.assertFalse(result['ok'])

    def test_reply_does_not_trigger_kato_run(self) -> None:
        from kato_core_lib.comment_core_lib import CommentRecord

        service = AgentService(**_kwargs())
        store = MagicMock()
        persisted = CommentRecord(
            id='c1', body='reply', repo_id='r1', author='a',
            source='local', parent_id='c0',
        )
        store.add.return_value = persisted
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_maybe_trigger_comment_run') as trigger:
            result = service.add_task_comment(
                'T1', repo_id='r1', file_path='f.py',
                body='reply', parent_id='c0',
            )
        self.assertTrue(result['ok'])
        trigger.assert_not_called()

    def test_top_level_comment_triggers_kato_run(self) -> None:
        from kato_core_lib.comment_core_lib import CommentRecord

        service = AgentService(**_kwargs())
        store = MagicMock()
        persisted = CommentRecord(
            id='c1', body='comment', repo_id='r1', author='a',
            source='local',
        )
        store.add.return_value = persisted
        store.get.return_value = persisted
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_maybe_trigger_comment_run',
                          return_value=True) as trigger:
            result = service.add_task_comment(
                'T1', repo_id='r1', file_path='f.py', body='comment',
            )
        self.assertTrue(result['ok'])
        trigger.assert_called_once()
        self.assertTrue(result['triggered_immediately'])


class ResolveTaskCommentTests(unittest.TestCase):
    def test_returns_error_when_no_workspace(self) -> None:
        service = AgentService(**_kwargs())
        result = service.resolve_task_comment('T1', 'c1')
        self.assertFalse(result['ok'])

    def test_returns_error_when_comment_not_found(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.update_status.return_value = None
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.resolve_task_comment('T1', 'c1')
        self.assertFalse(result['ok'])

    def test_resolves_local_comment_without_remote_sync(self) -> None:
        from kato_core_lib.comment_core_lib import (
            CommentRecord, CommentSource, CommentStatus,
        )
        record = CommentRecord(
            id='c1', body='b', repo_id='r1', author='a',
            source=CommentSource.LOCAL.value,
            status=CommentStatus.RESOLVED.value,
        )
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.update_status.return_value = record
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.resolve_task_comment('T1', 'c1')
        self.assertTrue(result['ok'])
        self.assertFalse(result['remote_sync']['attempted'])


class MarkCommentAddressedTests(unittest.TestCase):
    def test_returns_error_when_no_workspace(self) -> None:
        service = AgentService(**_kwargs())
        result = service.mark_comment_addressed('T1', 'c1')
        self.assertFalse(result['ok'])

    def test_returns_error_when_comment_not_found(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.update_kato_status.return_value = None
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.mark_comment_addressed('T1', 'c1')
        self.assertFalse(result['ok'])


class ReopenTaskCommentTests(unittest.TestCase):
    def test_returns_error_when_no_workspace(self) -> None:
        service = AgentService(**_kwargs())
        result = service.reopen_task_comment('T1', 'c1')
        self.assertFalse(result['ok'])

    def test_returns_error_when_comment_not_found(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.update_status.return_value = None
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.reopen_task_comment('T1', 'c1')
        self.assertFalse(result['ok'])

    def test_reopen_succeeds_when_comment_present(self) -> None:
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            KatoCommentStatus,
        )
        service = AgentService(**_kwargs())
        record = CommentRecord(
            id='c1', body='b', repo_id='r1', author='a', source='local',
        )
        store = MagicMock()
        store.update_status.return_value = record
        store.get.return_value = record
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_maybe_trigger_comment_run',
                          return_value=True) as trigger:
            result = service.reopen_task_comment('T1', 'c1')
        self.assertTrue(result['ok'])
        self.assertTrue(result['triggered_immediately'])
        store.update_kato_status.assert_called_once_with(
            'c1', kato_status=KatoCommentStatus.QUEUED.value,
        )
        trigger.assert_called_once_with('T1', 'c1')

    def test_reopen_reply_does_not_trigger_kato_run(self) -> None:
        from kato_core_lib.comment_core_lib import CommentRecord

        service = AgentService(**_kwargs())
        record = CommentRecord(
            id='c1', body='b', repo_id='r1', author='a', source='local',
            parent_id='c0',
        )
        store = MagicMock()
        store.update_status.return_value = record
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_maybe_trigger_comment_run') as trigger:
            result = service.reopen_task_comment('T1', 'c1')
        self.assertTrue(result['ok'])
        self.assertNotIn('triggered_immediately', result)
        store.update_kato_status.assert_not_called()
        trigger.assert_not_called()


class DeleteTaskCommentTests(unittest.TestCase):
    def test_returns_error_when_no_workspace(self) -> None:
        service = AgentService(**_kwargs())
        result = service.delete_task_comment('T1', 'c1')
        self.assertFalse(result['ok'])

    def test_returns_ok_true_when_delete_succeeds(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.delete.return_value = True
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.delete_task_comment('T1', 'c1')
        self.assertTrue(result['ok'])

    def test_returns_ok_false_when_delete_returns_false(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.delete.return_value = False
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.delete_task_comment('T1', 'c1')
        self.assertFalse(result['ok'])


class TaskHasBusyTurnTests(unittest.TestCase):
    def test_returns_false_when_no_session_manager(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service._task_has_busy_turn('T1'))

    def test_returns_false_on_session_exception(self) -> None:
        session = MagicMock()
        session.get_session.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(session_manager=session))
        self.assertFalse(service._task_has_busy_turn('T1'))

    def test_returns_false_when_session_dead(self) -> None:
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(
            is_alive=False, is_working=True,
        )
        service = AgentService(**_kwargs(session_manager=session))
        self.assertFalse(service._task_has_busy_turn('T1'))

    def test_returns_true_when_session_working(self) -> None:
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(
            is_alive=True, is_working=True,
        )
        service = AgentService(**_kwargs(session_manager=session))
        self.assertTrue(service._task_has_busy_turn('T1'))

    def test_returns_true_when_user_message_sent_but_no_result_yet(self) -> None:
        # Regression: there is a real race window between
        # ``send_user_message`` writing to stdin and Claude emitting
        # its first event for that message. ``is_working`` walks
        # ``_recent_events`` from the back, so during this gap it
        # returns False — the session looks idle even though a turn
        # is queued. A comment dispatched into that gap fired its own
        # ``send_user_message`` on a "false-idle" session, and the
        # PRIOR turn's RESULT then marked the comment ``ADDRESSED``
        # before its work even began (kato's reply quoted prior-turn
        # work and the chat panel was still ``thinking`` on the
        # comment). ``_task_has_busy_turn`` must treat
        # ``user_messages_sent > result_events_received`` as busy so
        # the comment stays QUEUED until the queue drains.
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(
            is_alive=True,
            is_working=False,           # the "false-idle gap"
            user_messages_sent=1,
            result_events_received=0,
        )
        service = AgentService(**_kwargs(session_manager=session))
        self.assertTrue(service._task_has_busy_turn('T1'))

    def test_returns_false_when_sends_match_results(self) -> None:
        # Truly idle: every sent message has been answered with a
        # RESULT and there is no mid-turn activity.
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(
            is_alive=True,
            is_working=False,
            user_messages_sent=3,
            result_events_received=3,
        )
        service = AgentService(**_kwargs(session_manager=session))
        self.assertFalse(service._task_has_busy_turn('T1'))


class TaskPullRequestIdTests(unittest.TestCase):
    def test_returns_empty_for_blank_input(self) -> None:
        service = AgentService(**_kwargs())
        self.assertEqual(service._task_pull_request_id('', 'r'), '')
        self.assertEqual(service._task_pull_request_id('T', ''), '')

    def test_returns_pr_id_from_registry(self) -> None:
        registry = MagicMock()
        registry.list_pull_request_contexts.return_value = [
            {'task_id': 'T1', 'repository_id': 'r1', 'pull_request_id': '17'},
        ]
        review = MagicMock()
        review.state_registry = registry
        service = AgentService(**_kwargs(review_comment_service=review))
        result = service._task_pull_request_id('T1', 'r1')
        self.assertEqual(result, '17')

    def test_swallows_registry_exception(self) -> None:
        registry = MagicMock()
        registry.list_pull_request_contexts.side_effect = RuntimeError('fail')
        review = MagicMock()
        review.state_registry = registry
        repo = MagicMock()
        repo.get_repository.side_effect = RuntimeError('also fail')
        service = AgentService(**_kwargs(
            review_comment_service=review,
            repository_service=repo,
        ))
        result = service._task_pull_request_id('T1', 'r1')
        self.assertEqual(result, '')


class CleanupCaseInsensitivityRegressionTests(unittest.TestCase):
    """Regression: cleanup must compare task ids case-insensitively.

    Two real symptoms this guards:
      * UNA-232 sitting in the "To Verify" (review) column was being
        wiped because its on-disk session record was lower-cased
        (``una-232``) while the platform returned ``UNA-232``.
      * UNA-1201 moved to "Done" was NOT being cleaned for the
        mirror-image casing mismatch.

    Actions must still use the ORIGINAL record id (the managers
    match case-sensitively).
    """

    def test_norm_task_id_canonicalises_case_and_blanks(self) -> None:
        n = AgentService._norm_task_id
        self.assertEqual(n('UNA-232'), n('una-232'))
        self.assertEqual(n('  UNA-232  '), n('una-232'))
        self.assertEqual(n(None), '')
        self.assertEqual(n(''), '')

    def _service(self, *, review_ids, assigned_ids, record_ids):
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = [
            SimpleNamespace(id=i) for i in review_ids
        ]
        task_service.get_assigned_tasks.return_value = [
            SimpleNamespace(id=i) for i in assigned_ids
        ]
        session = MagicMock()
        session.list_records.return_value = [
            SimpleNamespace(task_id=i) for i in record_ids
        ]
        workspace = MagicMock()
        workspace.list_workspaces.return_value = []
        registry = MagicMock()
        registry.tracked_task_ids.return_value = set(record_ids)
        registry.session_ids_for_task.return_value = []
        review_svc = MagicMock()
        review_svc.state_registry = registry
        svc = AgentService(**_kwargs(
            task_service=task_service,
            session_manager=session,
            workspace_manager=workspace,
            review_comment_service=review_svc,
        ))
        svc.logger = MagicMock()
        return svc, session, workspace

    def test_in_review_task_is_NOT_cleaned_despite_case_mismatch(self) -> None:
        # Platform: UNA-232 in review. Record on disk: lower-cased.
        svc, session, workspace = self._service(
            review_ids=['UNA-232'],
            assigned_ids=[],
            record_ids=['una-232'],
        )
        svc._cleanup_done_task_conversations()
        session.terminate_session.assert_not_called()
        workspace.delete.assert_not_called()

    def test_done_task_IS_marked_done_despite_case_mismatch(self) -> None:
        # Policy: NEVER auto-delete. Platform has nothing live; record
        # on disk is the lower-cased ``una-1201``. The cleanup must
        # flip the workspace status to ``done`` (using the ORIGINAL
        # record id so the manager finds it) without terminating the
        # session or deleting the clone.
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_DONE,
        )
        svc, session, workspace = self._service(
            review_ids=[],
            assigned_ids=[],
            record_ids=['una-1201'],
        )
        svc._cleanup_done_task_conversations()
        session.terminate_session.assert_not_called()
        workspace.delete.assert_not_called()
        workspace.update_status.assert_called_with(
            'una-1201', WORKSPACE_STATUS_DONE,
        )

    def test_status_flip_uses_original_record_id_not_normalised(self) -> None:
        # Mixed-case record, no live tasks → stale. The status flip
        # must receive the EXACT stored id, not a lowercased one
        # (managers match case-sensitively).
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_DONE,
        )
        svc, session, workspace = self._service(
            review_ids=[],
            assigned_ids=[],
            record_ids=['UNA-Mixed-99'],
        )
        svc._cleanup_done_task_conversations()
        session.terminate_session.assert_not_called()
        workspace.update_status.assert_called_with(
            'UNA-Mixed-99', WORKSPACE_STATUS_DONE,
        )

    def test_assigned_task_in_review_bucket_case_mix_is_protected(self) -> None:
        # Belt-and-braces: id live via the ASSIGNED set with a case
        # mismatch must also survive.
        svc, session, _ws = self._service(
            review_ids=[],
            assigned_ids=['UNA-555'],
            record_ids=['UNA-555'.lower()],
        )
        svc._cleanup_done_task_conversations()
        session.terminate_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
