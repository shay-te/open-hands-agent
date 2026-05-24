"""Flow #7 — False-success guard: agent ran but made no changes.

A-Z scenario:

    1. kato picks up task T1, clones the repo, spawns the agent.
    2. Agent runs, exits cleanly with no commits in any repo.
    3. ``publish_task_execution`` is called with an "ok" execution dict.
    4. ``_create_pull_requests`` finds every repo unchanged.
    5. The guard fires: ``_no_changes_publish_result`` is called.
    6. Result: status ``NO_CHANGES``, NO PR opened, task NOT moved to review.
    7. Task remains in its current state so a human can re-triage.

Why this guard exists (the pain it prevents):
    Before the guard, kato would `move_task_to_review()` even when the
    agent produced nothing — creating empty PRs and moving tickets to
    "In Review" with no work attached. Operator-trust catastrophic:
    the ticket looks done but isn't.

Test surface: ``TaskPublisher.publish_task_execution`` and the path
through ``_no_changes_publish_result``. Each test pins ONE invariant
that the guard must hold.
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import Mock

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields, PullRequestFields, StatusFields,
)
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError, RepositoryService,
)
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from tests.utils import build_task


class FlowFalseSuccessGuardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_service.add_comment = Mock()
        self.task_state_service = Mock(spec=TaskStateService)
        self.task_state_service.move_task_to_review = Mock()
        self.repository_service = Mock(spec=RepositoryService)
        self.notification_service = Mock(spec=NotificationService)
        self.state_registry = Mock(spec=AgentStateRegistry)
        self.failure_handler = Mock(spec=TaskFailureHandler)
        self.publisher = TaskPublisher(
            self.task_service, self.task_state_service,
            self.repository_service, self.notification_service,
            self.state_registry, self.failure_handler,
            publish_max_retries=0,
        )

    def _make_prepared(self, repo_ids):
        return types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id=rid, destination_branch='master')
                for rid in repo_ids
            ],
            repository_branches={
                rid: f'feature/proj-1/{rid}' for rid in repo_ids
            },
        )

    def _make_execution(self):
        return {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'sess-1',
            ImplementationFields.MESSAGE: 'ran but no changes',
        }

    # -----------------------------------------------------------------
    # End-to-end A-Z: the operator-visible invariants.
    # -----------------------------------------------------------------

    def test_flow_false_success_no_changes_returns_no_changes_status(self) -> None:
        # The smoking-gun guarantee: when no repos changed, the
        # returned status is NO_CHANGES — not READY_FOR_REVIEW.
        task = build_task()
        prepared = self._make_prepared(['client'])
        # Repository service signals "no changes" via the dedicated exception.
        self.repository_service.create_pull_request.side_effect = (
            RepositoryHasNoChangesError('client', 'no commits to push')
        )

        result = self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.assertEqual(
            result[StatusFields.STATUS], StatusFields.NO_CHANGES,
            'status was not NO_CHANGES — task may have moved to review with no PR',
        )

    def test_flow_false_success_no_changes_does_not_move_to_review(self) -> None:
        # The catastrophic regression mode this guard exists for.
        # If THIS test fails, kato is moving empty tickets to "In Review."
        task = build_task()
        prepared = self._make_prepared(['client'])
        self.repository_service.create_pull_request.side_effect = (
            RepositoryHasNoChangesError('client', 'no commits to push')
        )

        self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.task_state_service.move_task_to_review.assert_not_called()

    def test_flow_false_success_no_changes_returns_empty_pull_request_list(self) -> None:
        task = build_task()
        prepared = self._make_prepared(['client'])
        self.repository_service.create_pull_request.side_effect = (
            RepositoryHasNoChangesError('client', 'no commits to push')
        )

        result = self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.assertEqual(result[PullRequestFields.PULL_REQUESTS], [])

    def test_flow_false_success_no_changes_calls_failure_handler(self) -> None:
        # Operator-trust path: when the agent ran-but-did-nothing, kato
        # MUST route through the failure handler so the ticket carries
        # a visible "no commits" reason. Otherwise the operator sees a
        # silent stall.
        task = build_task()
        prepared = self._make_prepared(['client'])
        self.repository_service.create_pull_request.side_effect = (
            RepositoryHasNoChangesError('client', 'no commits to push')
        )

        self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.failure_handler.handle_started_task_failure.assert_called_once()
        # The exception passed should explain "produced no changes".
        call_args = self.failure_handler.handle_started_task_failure.call_args
        exc = call_args.args[1]
        self.assertIn('no changes', str(exc).lower())

    def test_flow_false_success_does_not_mark_processed(self) -> None:
        # state_registry.mark_task_processed is the dedupe-prevention
        # signal — if we mark a no-changes task as processed, kato will
        # never retry it on the next scan, hiding the failure forever.
        task = build_task()
        prepared = self._make_prepared(['client'])
        self.repository_service.create_pull_request.side_effect = (
            RepositoryHasNoChangesError('client', 'no commits to push')
        )

        self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.state_registry.mark_task_processed.assert_not_called()

    # -----------------------------------------------------------------
    # Adversarial neighbors: partial changes, multi-repo edges.
    # -----------------------------------------------------------------

    def test_flow_false_success_one_of_two_repos_changed_still_publishes(self) -> None:
        # Adversarial edge: ONE repo unchanged, ONE repo with real
        # changes. This is NOT a false-success — the task DID make
        # real progress. Status should be READY_FOR_REVIEW and the
        # unchanged repo just gets listed in the summary.
        task = build_task()
        prepared = self._make_prepared(['client', 'backend'])
        self.repository_service.create_pull_request.side_effect = [
            RepositoryHasNoChangesError('client', 'no commits to push'),
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                PullRequestFields.ID: '99',
                PullRequestFields.TITLE: 'PROJ-1: backend update',
                PullRequestFields.URL: 'https://example/pr/99',
            },
        ]

        result = self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.assertEqual(
            result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW,
            'mixed result (1 changed + 1 unchanged) was misclassified as NO_CHANGES — '
            'real work would be dropped',
        )
        self.task_state_service.move_task_to_review.assert_called_once()

    def test_flow_false_success_all_three_repos_unchanged_still_blocks_review(self) -> None:
        # Multi-repo task where EVERY repo is unchanged. The guard
        # must fire even when there are many repos, not only when
        # there's one.
        task = build_task()
        prepared = self._make_prepared(['client', 'backend', 'shared'])
        self.repository_service.create_pull_request.side_effect = [
            RepositoryHasNoChangesError('client', 'no commits to push'),
            RepositoryHasNoChangesError('backend', 'no commits to push'),
            RepositoryHasNoChangesError('shared', 'no commits to push'),
        ]

        result = self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.assertEqual(result[StatusFields.STATUS], StatusFields.NO_CHANGES)
        self.task_state_service.move_task_to_review.assert_not_called()

    def test_flow_false_success_failure_handler_names_unchanged_repos(self) -> None:
        # Operator-visible diagnostics: the no-changes path skips the
        # summary comment (no PRs to summarize) but MUST route through
        # the failure handler with an exception that names which repos
        # produced no changes — so the operator can re-triage without
        # digging through logs.
        task = build_task()
        prepared = self._make_prepared(['client', 'backend'])
        self.repository_service.create_pull_request.side_effect = [
            RepositoryHasNoChangesError('client', 'no commits to push'),
            RepositoryHasNoChangesError('backend', 'no commits to push'),
        ]

        self.publisher.publish_task_execution(
            task, prepared, self._make_execution(),
        )

        self.failure_handler.handle_started_task_failure.assert_called_once()
        exc = self.failure_handler.handle_started_task_failure.call_args.args[1]
        msg = str(exc)
        self.assertIn('client', msg)
        self.assertIn('backend', msg)


if __name__ == '__main__':
    unittest.main()
