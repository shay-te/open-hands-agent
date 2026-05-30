from __future__ import annotations
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping

import copy
import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato_core_lib.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import MissionStepLoggerMixin
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.implementation_service import ImplementationService
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext, session_suffix
from kato_core_lib.helpers.task_lookup_utils import (
    find_task_by_id,
    task_id_matches,
)
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError,
    RepositoryService,
)
from kato_core_lib.data_layers.service.planning_session_runner import (
    SessionStoppedByUserError,
)
from kato_core_lib.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.testing_service import TestingService
from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
)
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    StatusFields,
    TaskTags,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato_core_lib.validation.branch_push import TaskBranchPushValidator
from kato_core_lib.validation.model_access import TaskModelAccessValidator
from kato_core_lib.helpers.task_execution_utils import (
    apply_testing_message,
    implementation_succeeded,
    testing_failed_result,
    testing_succeeded,
)
# ``RepositoryHasNoChangesError`` is the "no work to publish" outcome
# from the publish path. With the per-repo ``branch_needs_push``
# pre-filter in ``push_task`` we shouldn't trip it normally, but a
# concurrent push or a workspace state change can still race us — log
# those one-liners and move on instead of dumping a full stack trace.
_ON_DEMAND_PUSH_EXPECTED_ERRORS = (RepositoryHasNoChangesError,)

# How long a sent-but-unanswered user message may sit before the
# session is judged STALLED (alive, but no longer consuming stdin).
# Must comfortably exceed the normal "message sent, Claude warming
# up" window: a healthy turn flips ``is_working`` True (events
# flowing) or returns a ``result`` well inside this window, so only a
# subprocess that silently stopped reading stdin stays
# ``user_messages_sent > result_events_received`` past it. Read by
# ``_task_session_is_stalled``; the classic trigger is a post-restart
# ``--resume`` respawn that never picks up the piped message.
_COMMENT_SEND_ACK_GRACE_SECONDS = 60.0


@dataclass(frozen=True)
class _PublishTaskLite(object):
    """Minimal Task-shaped object for on-demand push / PR-creation.

    The repository service's ``build_branch_name`` and the publication
    path only read ``.id`` and ``.summary``; carrying a full ``Task``
    (with tags, comments, watchers, etc.) here would require re-fetching
    from the ticket system on every button click.
    """

    id: str
    summary: str = ''


class AgentService(MissionStepLoggerMixin, Service):
    """Orchestrate the end-to-end task workflow and delegate specialized work to collaborators."""
    # NOTE: Task and review coordination state is kept in memory only.
    # It is not durable across process restarts.
    def __init__(
        self,
        task_service: TaskService,
        task_state_service: TaskStateService,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        state_registry: AgentStateRegistry | None = None,
        review_comment_service: ReviewCommentService | None = None,
        task_failure_handler: TaskFailureHandler | None = None,
        task_publisher: TaskPublisher | None = None,
        repository_connections_validator: RepositoryConnectionsValidator | None = None,
        startup_validator: StartupDependencyValidator | None = None,
        task_preflight_service: TaskPreflightService | None = None,
        skip_testing: bool = False,
        planning_session_runner=None,
        session_manager=None,
        workspace_manager=None,
        parallel_task_runner=None,
        wait_planning_service=None,
        triage_service=None,
        review_workspace_ttl_seconds: float = 3600.0,
        lessons_service=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or configure_logger(self.__class__.__name__)
        if task_service is None:
            raise ValueError('task_service is required')
        if task_state_service is None:
            raise ValueError('task_state_service is required')
        if implementation_service is None:
            raise ValueError('implementation_service is required')
        if testing_service is None:
            raise ValueError('testing_service is required')
        if repository_service is None:
            raise ValueError('repository_service is required')
        if notification_service is None:
            raise ValueError('notification_service is required')
        if review_comment_service is not None:
            review_state_registry = review_comment_service.state_registry
            if state_registry is not None and review_state_registry is not state_registry:
                raise ValueError(
                    'state_registry must match review_comment_service.state_registry'
                )
            state_registry = state_registry or review_state_registry
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._skip_testing = bool(skip_testing)
        self._planning_session_runner = planning_session_runner
        self._session_manager = session_manager
        self._workspace_manager = workspace_manager
        self._parallel_task_runner = parallel_task_runner
        self._wait_planning_service = wait_planning_service
        self._triage_service = triage_service
        self._review_workspace_ttl_seconds = max(
            0.0, float(review_workspace_ttl_seconds or 0.0),
        )
        self._lessons_service = lessons_service
        # `kato:wait-before-git-push` plumbing. The dict stashes the
        # (task, prepared_task, execution) tuple after testing so the
        # operator-triggered ``approve_push`` can resume publish without
        # re-running the agent. In-memory only — a kato restart loses
        # pending approvals (the workspace branch + commits survive on
        # disk; operator can re-trigger by removing the tag and letting
        # the next scan re-process the task).
        # ``RLock`` rather than ``Lock`` so a future approve-flow callback
        # that re-enters ``is_awaiting_push_approval`` from inside another
        # critical section won't deadlock.
        import threading as _threading
        self._pending_publish_lock = _threading.RLock()
        self._pending_publish: dict[str, tuple] = {}
        # Per-task lock that serializes ``_maybe_trigger_comment_run``'s
        # busy-check → IN_PROGRESS flip → send_user_message sequence.
        # Without this, two concurrent triggers (scan-tick draining the
        # queue + a fresh POST landing in the same window) can each pass
        # the busy check, each flip THEIR comment to IN_PROGRESS, each
        # call send_user_message. Both comments then ride the same
        # RESULT and ``complete_in_progress_task_comments`` attaches
        # the FIRST one's result text to BOTH (visible symptom: kato's
        # reply to a comment is about a completely unrelated change).
        self._comment_dispatch_locks: dict[str, _threading.Lock] = {}
        self._comment_dispatch_locks_lock = _threading.Lock()
        self._state_registry = state_registry or AgentStateRegistry()
        self._review_comment_service = review_comment_service or ReviewCommentService(
            self._task_service,
            self._implementation_service,
            self._repository_service,
            self._state_registry,
        )
        self._repository_connections_validator = (
            repository_connections_validator
            or RepositoryConnectionsValidator(self._repository_service)
        )
        self._task_failure_handler = task_failure_handler or TaskFailureHandler(
            self._task_service,
            self._task_state_service,
            self._repository_service,
            self._notification_service,
        )
        self._startup_validator = startup_validator or StartupDependencyValidator(
            self._repository_connections_validator,
            self._task_service,
            self._implementation_service,
            self._testing_service,
            self._skip_testing,
        )
        self._task_preflight_service = task_preflight_service or TaskPreflightService(
            task_model_access_validator=TaskModelAccessValidator(
                self._implementation_service,
            ),
            task_service=self._task_service,
            repository_service=self._repository_service,
            task_branch_push_validator=TaskBranchPushValidator(
                self._repository_service,
            ),
            task_branch_publishability_validator=TaskBranchPublishabilityValidator(
                self._repository_service,
            ),
        )
        self._task_publisher = task_publisher or TaskPublisher(
            self._task_service,
            self._task_state_service,
            self._repository_service,
            self._notification_service,
            self._state_registry,
            self._task_failure_handler,
        )

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        self._startup_validator.validate(self.logger)

    def warm_up_repository_inventory(self) -> None:
        """Trigger repository auto-discovery in the background.

        Without this, the disk walk that finds all .git folders under
        REPOSITORY_ROOT_PATH fires lazily on the *first task pickup*,
        blocking that task for however long the walk takes. Calling
        this right after startup means the walk runs in parallel with
        the first scan-interval sleep, so first-task latency is zero
        instead of "however large the project tree is".

        Errors are swallowed — the walk will re-run on first task pickup
        as before, so a transient failure here is non-fatal.
        """
        import threading
        repo_service = self._repository_service

        def _run() -> None:
            try:
                repo_service._ensure_repositories()
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True, name='kato-repo-inventory-warmup')
        t.start()

    def shutdown(self) -> None:
        """Tear down everything kato owns: pool, sessions, conversations.

        Wired into the kato main process's signal handler. Each step is
        guarded so a single failure can't block the rest of the cleanup.
        Idempotent — safe to call twice.
        """
        if self._parallel_task_runner is not None:
            try:
                self._parallel_task_runner.shutdown(wait=True)
            except Exception:
                self.logger.exception('error during parallel-runner shutdown')
        try:
            self._implementation_service.stop_all_conversations()
        except Exception:
            self.logger.exception('error stopping implementation conversations')
        try:
            self._testing_service.stop_all_conversations()
        except Exception:
            self.logger.exception('error stopping testing conversations')
        if self._session_manager is not None:
            try:
                self._session_manager.shutdown()
            except Exception:
                self.logger.exception('error tearing down planning sessions on shutdown')

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_service.get_assigned_tasks()

    @property
    def parallel_task_runner(self):
        """Worker pool used by the scan job to run tasks concurrently.

        ``None`` when the runner wasn't wired (legacy / test setups).
        Callers are expected to fall back to inline execution in that
        case so the same code path works with and without the runner.
        """
        return self._parallel_task_runner

    def get_new_pull_request_comments(self) -> list[ReviewComment]:
        self._cleanup_done_task_conversations()
        return self._review_comment_service.get_new_pull_request_comments()

    def cleanup_done_tasks(self) -> None:
        """Public boot entrypoint for the done-task prune.

        Called once at startup (before the planning webserver starts
        serving tabs) so a restart never resurrects a tab for a task
        whose ticket already moved to done/closed. Without this, a
        stale ``~/.kato/sessions/<id>.json`` left on disk renders as
        a tab on boot and only disappears on the first scan-tick
        cleanup ~30s later — the "task is back after restart" bug.
        Best-effort: the underlying cleanup already swallows its own
        per-source failures.
        """
        self._cleanup_done_task_conversations()

    @staticmethod
    def _norm_task_id(value) -> str:
        """Canonical task-id key for set comparisons.

        Task ids reach the cleanup logic from THREE sources whose
        casing doesn't agree: the ticket platform (``UNA-232``),
        on-disk session records, and workspace folders (some
        ``UNA-…``, some ``una-…`` depending on how the id first
        arrived). A case-sensitive ``candidates - live`` therefore
        either spares a done task (it never matches "live") or, far
        worse, wipes a task that IS still in review (its record case
        differs from the platform's). All cleanup decisions compare
        on this normalized key; the ORIGINAL id is kept for the
        actual delete/forget so the manager finds the right record.
        """
        return str(value or '').strip().lower()

    def _cleanup_done_task_conversations(self) -> None:
        """Delete conversation containers for tasks no longer in the review state.

        When a reviewer merges a PR and moves the task to done, Kato detects
        it is missing from the review-task list and removes the associated
        agent-server container to avoid accumulation.
        """
        try:
            current_review_norm = {
                self._norm_task_id(task.id)
                for task in self._task_service.get_review_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch review tasks for conversation cleanup; skipping'
            )
            return

        stale_task_ids = {
            tid for tid in self._state_registry.tracked_task_ids()
            if self._norm_task_id(tid) not in current_review_norm
        }
        for task_id in stale_task_ids:
            for agent_session_id in self._state_registry.session_ids_for_task(task_id):
                self.logger.info(
                    'task %s is no longer in review; stopping conversation %s',
                    task_id,
                    agent_session_id,
                )
                try:
                    self._implementation_service.delete_conversation(agent_session_id)
                except Exception:
                    self.logger.warning(
                        'failed to stop conversation %s for done task %s',
                        agent_session_id,
                        task_id,
                    )
            self._state_registry.forget_task(task_id)

        self._cleanup_done_planning_sessions(current_review_norm)

    def _cleanup_done_planning_sessions(
        self,
        current_review_norm: set[str],
    ) -> None:
        """Mark planning-UI tabs whose ticket has moved to done/closed.

        Previous behaviour terminated the live subprocess, removed the
        persisted session record, AND deleted the workspace folder when
        a ticket left both Open and Review buckets. Operator policy is
        now NEVER auto-delete anything from disk — the workspace clone,
        the session record, and the tab all stay. Instead, the
        workspace status is flipped to ``done`` so the UI renders the
        status circle greyed-out; the operator decides when (or
        whether) to wipe the clone via the explicit DELETE endpoint.
        """
        if self._session_manager is None and self._workspace_manager is None:
            return
        try:
            assigned_norm = {
                self._norm_task_id(task.id)
                for task in self._task_service.get_assigned_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch assigned tasks for session cleanup; '
                'leaving planning sessions in place this cycle'
            )
            return

        # All three id sources (platform / session records / workspace
        # folders) get normalized to a common case before comparison —
        # see ``_norm_task_id``.
        live_norm = assigned_norm | current_review_norm
        for task_id in self._stale_planning_task_ids(live_norm):
            self.logger.info(
                'task %s is no longer assigned or in review; '
                'marking workspace as done (no delete — operator '
                'must use the explicit DELETE endpoint)',
                task_id,
            )
            self._mark_workspace_done_silent(task_id)

    def _stale_planning_task_ids(self, live_norm: set[str]) -> set[str]:
        """Task ids known to either manager that aren't live anymore.

        The ``active``/``provisioning`` in-flight guard exists for a
        narrow case: kato itself flips a ticket to *In Progress*
        while driving it, so it momentarily vanishes from both
        ``get_assigned_tasks()`` and ``get_review_tasks()``. Without
        a guard the next scan would wipe a workspace kato is mid-run
        on.

        BUT the workspace status is never reliably reset back from
        ``active`` once a task finishes, so an unconditional "active
        ⇒ never clean" rule shields a *done* task's leftover
        workspace forever — the tab never disappears (the
        "task-still-there-after-it's-done" bug). So an
        active/provisioning workspace is protected only when it's
        plausibly still being driven:

          * a live session subprocess exists for it, OR
          * it was updated within the grace window
            (``review_workspace_ttl_seconds``, 1h default — far
            longer than any single task run, operator-tunable).

        An active/provisioning workspace that is BOTH not live AND
        cold (no update within the grace) is a leftover: if its
        ticket isn't live either, it's stale and gets cleaned. When
        the TTL is 0 (operator disabled age-based cleanup) the
        legacy "protect all active/provisioning" behaviour is kept.

        Review-status workspaces are always protected: a ticket in
        the review / "To Verify" bucket with a local clone is work
        the operator may still be verifying, so its clone is kept
        until the ticket actually leaves the review bucket.
        """
        import time as _time

        # norm-id -> ORIGINAL task id (first writer wins; the session
        # record id is preferred since that's the key the session
        # manager stored, so terminate/delete hit the right record).
        candidate_by_norm: dict[str, str] = {}

        def remember(task_id) -> str:
            norm = self._norm_task_id(task_id)
            candidate_by_norm.setdefault(norm, task_id)
            return norm

        if self._session_manager is not None:
            try:
                for record in self._session_manager.list_records():
                    remember(record.task_id)
            except Exception:
                self.logger.exception('failed to list planning session records')

        workspace_records = self._safe_list_workspaces()
        protected_norm: set[str] = set()
        now_epoch = _time.time()
        for record in workspace_records:
            norm = remember(record.task_id)
            bucket = self._classify_workspace_for_cleanup(record, now_epoch)
            if bucket == 'protected':
                protected_norm.add(norm)
            # 'stale' → no protection; falls through to the
            # live-norm subtraction below.
        stale_norm = set(candidate_by_norm) - live_norm - protected_norm
        return {candidate_by_norm[n] for n in stale_norm}

    def _safe_list_workspaces(self) -> list:
        if self._workspace_manager is None:
            return []
        try:
            return list(self._workspace_manager.list_workspaces())
        except Exception:
            self.logger.exception('failed to list workspaces')
            return []

    def _has_live_session(self, task_id) -> bool:
        if self._session_manager is None:
            return False
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return False
        return session is not None and getattr(session, 'is_alive', True)

    def _classify_workspace_for_cleanup(self, record, now_epoch: float) -> str:
        """Bucket one workspace record for the stale sweep.

        Returns one of:
          * ``'protected'`` — keep the clone. Two cases:
              - an active/provisioning workspace that is plausibly
                still being driven (live session OR updated within
                the grace window OR TTL disabled); and
              - ANY review-state workspace. A ticket sitting in the
                review / "To Verify" bucket with a local clone is
                work the operator may still be reviewing — its clone
                is never deleted, regardless of age. (Previously a
                review clone older than the TTL was force-cleaned;
                that wiped clones for tickets the operator was still
                verifying — the "task disappeared while on verify"
                bug. Review clones are now kept until the ticket
                actually leaves the review bucket.)
          * ``'stale'`` — no special protection; the
            ``candidates - live_norm`` subtraction decides. The
            default for done/errored/terminated leftovers and for
            cold active/provisioning leftovers. Matching the
            pre-refactor fall-through: anything not explicitly
            protected here is cleaned iff its ticket isn't live.
        """
        status = getattr(record, 'status', '')
        ttl = self._review_workspace_ttl_seconds
        updated = float(getattr(record, 'updated_at_epoch', 0.0) or 0.0)
        if status in (WORKSPACE_STATUS_ACTIVE, WORKSPACE_STATUS_PROVISIONING):
            fresh = (
                ttl <= 0
                or updated <= 0
                or (now_epoch - updated) <= ttl
            )
            if self._has_live_session(record.task_id) or fresh:
                return 'protected'
            return 'stale'
        if status == WORKSPACE_STATUS_REVIEW:
            return 'protected'
        return 'stale'

    def _mark_workspace_done_silent(self, task_id: str) -> None:
        """Flag a workspace as ``done`` without touching disk.

        Replaces the old ``_delete_workspace_silent`` because the
        operator policy is now NEVER auto-delete a workspace folder.
        The status flip is enough for the UI to render the tab's
        status circle greyed-out; the on-disk clone, the session
        record, and the tab itself all remain. The operator wipes
        the clone explicitly via the DELETE workspace endpoint
        when (and if) they want to.
        """
        if self._workspace_manager is None:
            return
        update = getattr(self._workspace_manager, 'update_status', None)
        if not callable(update):
            return
        try:
            update(task_id, WORKSPACE_STATUS_DONE)
        except Exception:
            self.logger.exception(
                'failed to mark workspace done for task %s', task_id,
            )

    def _delete_workspace_silent(self, _task_id: str) -> None:
        """Deprecated: kept as a no-op for backwards compatibility.

        Operator policy is NEVER auto-delete. Callers should use
        ``_mark_workspace_done_silent`` to flip the status instead.
        Direct callers of ``workspace_manager.delete`` should be
        operator-triggered only (the DELETE workspace endpoint).
        """
        # Intentionally a no-op — see ``_mark_workspace_done_silent``.

    def _update_workspace_status_after_publish(
        self,
        task_id: str,
        publish_result: dict[str, object] | None,
    ) -> None:
        if self._workspace_manager is None or not publish_result:
            return
        status = publish_result.get(StatusFields.STATUS)
        if status == StatusFields.READY_FOR_REVIEW:
            target = WORKSPACE_STATUS_REVIEW
        elif status == StatusFields.PARTIAL_FAILURE:
            target = WORKSPACE_STATUS_ERRORED
        else:
            return
        try:
            self._workspace_manager.update_status(str(task_id), target)
        except Exception:
            self.logger.exception(
                'failed to update workspace status for task %s to %s',
                task_id, target,
            )

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        return self._review_comment_service.handle_pull_request_comment(payload)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        return self._review_comment_service.process_review_comment(comment)

    def process_review_comment_batch(
        self, comments: list[ReviewComment],
    ) -> list[dict[str, str]]:
        return self._review_comment_service.process_review_comment_batch(comments)

    def task_id_for_review_comment(self, comment: ReviewComment) -> str | None:
        return self._review_comment_service.task_id_for_comment(comment)

    def process_assigned_task(self, task: Task) -> dict[str, object] | None:
        # No in-memory "already processed" short-circuit. The ticket system
        # (state + comments) is the single source of truth: successful tasks
        # have already been moved out of the scanned states, and skipped/
        # failed tasks carry comments that the gate and preflight read fresh
        # on every scan. Remove the comment, the task is re-evaluated.

        # `kato:triage:investigate` short-circuits the orchestration too,
        # but for a different reason: instead of registering an
        # interactive chat, kato spends one Claude turn classifying the
        # task and writes back a kato:triage:<level> outcome tag. No
        # implementation, no testing, no PR. Runs *before* wait-planning
        # so a triage task that also carries the wait-planning tag
        # still gets classified rather than opened as a chat tab.
        if self._triage_service is not None:
            triage_result = self._triage_service.handle_task(task)
            if triage_result is not None:
                return triage_result

        # `kato:wait-planning` short-circuits the orchestration: register the
        # planning tab so the human can chat with the agent in the UI, but
        # do *no* implementation, testing, or publishing work. The user
        # controls the conversation; remove the tag whenever they want
        # autonomous execution to take over.
        if self._wait_planning_service is not None:
            planning_only_result = self._wait_planning_service.handle_task(task)
            if planning_only_result is not None:
                return planning_only_result

        prepared_task = self._task_preflight_service.prepare_task_execution_context(
            task,
            task_failure_handler=self._task_failure_handler.handle_task_failure,
            repository_resolution_failure_handler=(
                self._task_failure_handler.handle_repository_resolution_failure
            ),
            repository_preparation_failure_handler=self._task_failure_handler.handle_task_failure,
            task_definition_failure_handler=(
                self._task_failure_handler.handle_task_definition_failure
            ),
            branch_preparation_failure_handler=self._task_failure_handler.handle_task_failure,
            branch_push_failure_handler=self._task_failure_handler.handle_started_task_failure,
        )
        if prepared_task is None or isinstance(prepared_task, dict):
            return prepared_task

        if not self._start_task_processing(task, prepared_task):
            return None
        execution = self._run_task_implementation(task, prepared_task)
        if execution is None:
            return None
        testing_succeeded, testing_result, execution = self._run_task_testing_validation(
            task,
            prepared_task,
            execution,
        )
        if not testing_succeeded:
            return testing_result
        if self._task_has_wait_before_push_tag(task):
            return self._pause_for_push_approval(task, prepared_task, execution)
        publish_result = self._task_publisher.publish_task_execution(
            task,
            prepared_task,
            execution,
        )
        self._update_workspace_status_after_publish(task.id, publish_result)
        return publish_result

    @staticmethod
    def _task_has_wait_before_push_tag(task: Task) -> bool:
        tags = getattr(task, 'tags', None) or []
        target = TaskTags.WAIT_BEFORE_GIT_PUSH.lower()
        for tag in tags:
            if str(tag or '').strip().lower() == target:
                return True
        return False

    def _pause_for_push_approval(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict,
    ) -> dict[str, object]:
        """Stash the post-test execution context and post a "waiting" comment.

        The actual push happens via :meth:`approve_push` (called from the
        planning UI's "Approve push" button). We do NOT post the
        ``Kato completed task`` blocking-comment prefix here — that one
        signals success and would prevent re-processing. Instead we use
        a one-off informational comment that does not interfere with the
        existing comment-driven blocker mechanism.
        """
        task_id = str(task.id)
        with self._pending_publish_lock:
            self._pending_publish[task_id] = (task, prepared_task, execution)
        try:
            self._task_service.add_comment(
                task_id,
                'Kato has finished implementation and testing for this task. '
                'Push and PR creation are paused because '
                f'`{TaskTags.WAIT_BEFORE_GIT_PUSH}` is set. To proceed, '
                'click "Approve push" in the planning UI, or remove the '
                f'`{TaskTags.WAIT_BEFORE_GIT_PUSH}` tag and re-trigger the '
                'task. Kato — not the agent — performs the push.',
            )
        except Exception:
            self.logger.exception(
                'failed to post wait-before-push comment for task %s', task_id,
            )
        if self._workspace_manager is not None:
            try:
                self._workspace_manager.update_status(
                    task_id, WORKSPACE_STATUS_REVIEW,
                )
            except Exception:
                self.logger.exception(
                    'failed to update workspace status for task %s', task_id,
                )
        self.logger.info(
            'task %s implementation complete; awaiting push approval', task_id,
        )
        return {
            StatusFields.STATUS: 'awaiting_push_approval',
            'task_id': task_id,
        }

    def approve_push(self, task_id: str) -> dict[str, object] | None:
        """Operator-triggered push for a task paused on ``kato:wait-before-git-push``.

        Returns the publish result on success, or ``None`` when no pending
        publish exists for the task (e.g. operator clicked the button on a
        task that wasn't paused, or kato restarted and lost the in-memory
        pending state).
        """
        normalized_task_id = str(task_id or '').strip()
        if not normalized_task_id:
            return None
        with self._pending_publish_lock:
            pending = self._pending_publish.pop(normalized_task_id, None)
        if pending is None:
            return None
        task, prepared_task, execution = pending
        self.logger.info(
            'operator approved push for task %s; resuming publish',
            normalized_task_id,
        )
        publish_result = self._task_publisher.publish_task_execution(
            task,
            prepared_task,
            execution,
        )
        self._update_workspace_status_after_publish(task.id, publish_result)
        return publish_result

    def is_awaiting_push_approval(self, task_id: str) -> bool:
        """True when ``approve_push`` has a pending publish for this task."""
        normalized = str(task_id or '').strip()
        if not normalized:
            return False
        with self._pending_publish_lock:
            return normalized in self._pending_publish

    # ----- diff-tab comments (kato-local + remote-synced) -----

    def list_task_comments(
        self, task_id: str, repo_id: str = '',
    ) -> list[dict[str, object]]:
        """Return every comment on a task workspace (optionally per-repo).

        Drives the Changes-tab inline-comment widget. Each entry is
        a ``CommentRecord.to_dict()`` so the UI sees the full set
        of fields (id, body, line, source, status, kato_status,
        author, parent_id for threading).
        """
        store = self._comment_store_for(task_id)
        if store is None:
            return []
        records = (
            store.list_for_repo(repo_id) if repo_id else store.list()
        )
        # Annotate each comment with ``outdated``: its anchor line no
        # longer exists in the current file (the code was rewritten /
        # shrank), so it can't render in the diff. The UI hides outdated
        # comments from the tree badge so a phantom 💬 N never points at a
        # comment that isn't visible anywhere. Line-count lookups are
        # cached per (repo, file) for this call.
        line_counts: dict[tuple[str, str], int | None] = {}
        out: list[dict[str, object]] = []
        for record in records:
            data = record.to_dict()
            data['outdated'] = self._comment_anchor_is_outdated(
                task_id, record, line_counts,
            )
            out.append(data)
        return out

    def _comment_anchor_is_outdated(self, task_id: str, record, cache: dict) -> bool:
        """True when a line-anchored comment points past the current file.

        Only line-anchored comments (``line >= 1``) can go stale this way;
        file-level (``line < 1``) comments always render in the file panel.
        Conservative: if the file can't be read (missing, binary, path
        unresolved) we report NOT outdated so a lookup glitch never hides a
        real comment.
        """
        line = int(getattr(record, 'line', -1) or -1)
        if line < 1:
            return False
        repo_id = str(getattr(record, 'repo_id', '') or '').strip()
        file_path = str(getattr(record, 'file_path', '') or '').strip()
        if not repo_id or not file_path:
            return False
        key = (repo_id, file_path)
        if key not in cache:
            cache[key] = self._file_line_count(task_id, repo_id, file_path)
        count = cache[key]
        return count is not None and line > count

    def _file_line_count(self, task_id: str, repo_id: str, file_path: str) -> int | None:
        """Line count of a workspace file, or None when it can't be read."""
        if self._workspace_manager is None:
            return None
        try:
            repo_path = self._workspace_manager.repository_path(task_id, repo_id)
        except Exception:
            return None
        target = repo_path / file_path
        try:
            if not target.is_file():
                return None
            with target.open('r', encoding='utf-8', errors='replace') as handle:
                return sum(1 for _ in handle)
        except Exception:
            return None

    def add_task_comment(
        self,
        task_id: str,
        *,
        repo_id: str,
        file_path: str,
        line: int = -1,
        body: str = '',
        parent_id: str = '',
        author: str = '',
    ) -> dict[str, object]:
        """Persist a new local comment + immediately queue / run kato.

        On create the comment lands as ``kato_status=QUEUED``. If
        the task currently has no live agent turn in flight, kato
        kicks off a review-fix run for this comment right away
        (``KatoCommentStatus.IN_PROGRESS``); otherwise the comment
        sits in the queue and the next "agent went idle" tick
        drains it. This mirrors the operator expectation "submit
        comment → kato fixes immediately if free, queues otherwise."
        """
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            CommentSource,
            KatoCommentStatus,
        )

        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'error': 'no workspace for task — adopt it first'}
        record = CommentRecord(
            repo_id=str(repo_id or '').strip(),
            file_path=str(file_path or '').strip(),
            line=int(line if line is not None else -1),
            parent_id=str(parent_id or '').strip(),
            author=str(author or 'operator'),
            body=str(body or '').strip(),
            source=CommentSource.LOCAL.value,
        )
        try:
            persisted = store.add(record)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        # An operator reply RE-ENGAGES kato: it flips the thread's root
        # comment back to QUEUED (pending) and triggers a run, so kato
        # addresses the new reply (e.g. "no, do it differently") instead
        # of leaving the thread ADDRESSED. The re-run's prompt includes
        # the thread replies (see ``_comment_agent_prompt``) so kato sees
        # the latest pushback. Claude's own replies are added via
        # ``_add_comment_agent_reply`` (store.add directly), NOT this
        # path, so this never self-triggers a loop.
        if persisted.parent_id:
            root = persisted
            seen: set[str] = set()
            while root.parent_id and root.id not in seen:
                seen.add(root.id)
                parent = store.get(root.parent_id)
                if parent is None:
                    break
                root = parent
            store.update_kato_status(
                root.id, kato_status=KatoCommentStatus.QUEUED.value,
            )
            triggered = self._maybe_trigger_comment_run(str(task_id), root.id)
            return {
                'ok': True,
                'comment': persisted.to_dict(),
                'triggered_immediately': triggered,
                'requeued_root_id': root.id,
            }
        # Kick off the agent if the task is idle, otherwise queue.
        store.update_kato_status(
            persisted.id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        triggered = self._maybe_trigger_comment_run(
            str(task_id), persisted.id,
        )
        persisted = store.get(persisted.id) or persisted
        return {
            'ok': True,
            'comment': persisted.to_dict(),
            'triggered_immediately': triggered,
        }

    def drain_next_queued_task_comment(self, task_id: str) -> dict[str, object]:
        """Start the oldest queued local diff comment for this task if possible."""
        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'started': False, 'error': 'no workspace for task'}
        record = store.next_queued()
        if record is None:
            return {'ok': True, 'started': False, 'comment_id': ''}
        started = self._maybe_trigger_comment_run(str(task_id), record.id)
        return {'ok': True, 'started': started, 'comment_id': record.id}

    def drain_all_queued_task_comments(self) -> list[dict[str, object]]:
        """Drain one queued local diff comment for every task workspace.

        Server-side, browser-independent. Previously a queued comment
        was ONLY drained when a ``RESULT`` event flowed through an
        open browser SSE (or a browser reconnected to a dead session)
        — so a comment queued while Claude was busy stayed ``QUEUED``
        forever if nobody happened to be watching that task's tab when
        the turn finished (the "3-hour-old queued comment" report).
        The scan loop now calls this every cycle so a queued comment
        is picked up on the next idle transition no matter what the
        UI is doing. ``drain_next_queued_task_comment`` is a cheap
        no-op when nothing is queued or the turn is still busy, so
        running it across every workspace each tick is safe.
        """
        results: list[dict[str, object]] = []
        for record in self._safe_list_workspaces():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            try:
                outcome = self.drain_next_queued_task_comment(task_id)
            except Exception:
                self.logger.exception(
                    'queued-comment drain failed for task %s', task_id,
                )
                continue
            if outcome.get('started'):
                results.append({'task_id': task_id, **outcome})
        return results

    def requeue_stuck_in_progress_comments(self) -> list[dict[str, object]]:
        """Reset comments orphaned in IN_PROGRESS by a kato restart.

        ``_maybe_trigger_comment_run`` flips a comment to
        ``IN_PROGRESS`` *before* it spawns the agent. If kato is
        killed / restarted mid-run the agent subprocess dies but the
        on-disk comment stays ``IN_PROGRESS`` forever — and
        ``next_queued()`` only ever returns ``QUEUED`` comments, so
        the scan-loop drain never re-dispatches it and (with lazy
        resume) the chat session never wakes. That's the "I restarted
        kato and the conversation with my comment is still sleeping"
        report.

        Mirrors the boot-time ``_reset_stuck_workspace_statuses``
        recovery: at boot no streaming session is alive yet, so any
        ``IN_PROGRESS`` comment is by definition stale — flip it back
        to ``QUEUED`` so the very next scan tick drains it and
        respawns the session. Safe to run only at boot for that
        reason; do NOT call it while sessions may be live.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        requeued: list[dict[str, object]] = []
        for record in self._safe_list_workspaces():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            store = self._comment_store_for(task_id)
            if store is None:
                continue
            try:
                comments = store.list()
            except Exception:
                self.logger.exception(
                    'failed to list comments while requeueing task %s', task_id,
                )
                continue
            for comment in comments:
                if comment.kato_status != KatoCommentStatus.IN_PROGRESS.value:
                    continue
                try:
                    store.update_kato_status(
                        comment.id,
                        kato_status=KatoCommentStatus.QUEUED.value,
                    )
                except Exception:
                    self.logger.exception(
                        'failed to requeue stuck comment %s on task %s',
                        comment.id, task_id,
                    )
                    continue
                requeued.append(
                    {'task_id': task_id, 'comment_id': comment.id},
                )
        return requeued

    def complete_in_progress_task_comments(
        self, task_id: str, *, success: bool, result_text: str = '',
    ) -> list[dict[str, object]]:
        """Move a task's IN_PROGRESS comments out when its turn ends.

        ``_maybe_trigger_comment_run`` flips a comment to
        ``IN_PROGRESS`` before handing it to the streaming session,
        but nothing moved it OUT when the turn finished — so a comment
        kato actually completed sat on the "kato working" badge
        forever (and a restart's ``requeue_stuck_in_progress_comments``
        would redo the already-done work). Called from the
        RESULT-event handler: the turn that just ended is the one the
        in-progress comment was dispatched into, so ``success`` →
        ``ADDRESSED``, an errored turn → ``FAILED``.

        Reuses :meth:`mark_comment_addressed` with
        ``post_remote_reply=False`` — the auto-flip must not spam the
        source platform on every turn; the operator's explicit
        "Mark addressed" / Resolve still drives any remote reply.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        # Hard invariant: a comment must NEVER be marked addressed while
        # Claude is still working — it stays on the WORKING badge. A
        # result can reach this method that does NOT belong to the
        # in-progress comment's turn: a browser replaying the session
        # backlog on reconnect, a resumed session's history, or a stale
        # result still sitting in the buffer while THIS comment's own
        # turn is in flight (``user_messages_sent > result_events_received``).
        # Completing then attaches the WRONG turn's answer and flips the
        # badge to ADDRESSED while the real work is still running (the
        # "kato replied instantly with an unrelated answer and never did
        # the work" report). If the session is busy, leave every comment
        # IN_PROGRESS — the live RESULT for this comment's own turn, or
        # the scan-loop fallback once the turn truly ends, completes it
        # with the right answer.
        if self._task_has_busy_turn(task_id):
            return []

        store = self._comment_store_for(task_id)
        if store is None:
            return []
        try:
            comments = store.list()
        except Exception:
            self.logger.exception(
                'failed to list comments completing task %s', task_id,
            )
            return []
        completed: list[dict[str, object]] = []
        for comment in comments:
            if comment.kato_status != KatoCommentStatus.IN_PROGRESS.value:
                continue
            try:
                if success:
                    self._add_comment_agent_reply(store, comment, result_text)
                    self.mark_comment_addressed(
                        task_id, comment.id, post_remote_reply=False,
                    )
                    new_status = KatoCommentStatus.ADDRESSED.value
                    self.logger.info(
                        'comment %s on task %s marked addressed '
                        '(agent turn finished)', comment.id, task_id,
                    )
                else:
                    store.update_kato_status(
                        comment.id,
                        kato_status=KatoCommentStatus.FAILED.value,
                        failure_reason='agent turn ended with an error',
                    )
                    new_status = KatoCommentStatus.FAILED.value
                    self.logger.warning(
                        'comment %s on task %s marked failed '
                        '(agent turn errored)', comment.id, task_id,
                    )
            except Exception:
                self.logger.exception(
                    'failed to complete comment %s on task %s',
                    comment.id, task_id,
                )
                continue
            completed.append({
                'task_id': task_id,
                'comment_id': comment.id,
                'kato_status': new_status,
            })
        # Chain straight to the next queued comment the instant this turn
        # finishes, instead of stranding it on the slow scan-loop fallback
        # — the operator's "the next comment takes ages, and the last one
        # never runs" report. The turn we just completed left the session
        # idle, so starting the next one is safe; it is a no-op when the
        # queue is empty. Runs after success OR failure so a failed
        # comment never blocks the rest of the queue.
        if completed:
            try:
                self.drain_next_queued_task_comment(task_id)
            except Exception:
                self.logger.exception(
                    'failed to chain to next queued comment for task %s',
                    task_id,
                )
        return completed

    def _add_comment_agent_reply(self, store, comment, result_text: str) -> None:
        """Mirror Claude's final answer back into the comment thread."""
        body = str(result_text or '').strip()
        if not body:
            return
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            CommentSource,
        )

        try:
            store.add(CommentRecord(
                repo_id=str(getattr(comment, 'repo_id', '') or '').strip(),
                file_path=str(getattr(comment, 'file_path', '') or '').strip(),
                line=int(getattr(comment, 'line', -1) or -1),
                parent_id=str(getattr(comment, 'id', '') or '').strip(),
                author='claude',
                body=body,
                source=CommentSource.LOCAL.value,
            ))
        except Exception:
            self.logger.exception(
                'failed to add Claude reply for comment %s',
                getattr(comment, 'id', '<unknown>'),
            )

    def advance_finished_comment_runs(self) -> list[dict[str, object]]:
        """Scan-loop fallback: advance IN_PROGRESS comments whose session has ended.

        Normal path: SSE RESULT event → ``_advance_task_comments_after_result``
        → ``complete_in_progress_task_comments``. Fallback (no SSE subscriber
        at the moment the turn finished): called each scan tick so the badge
        doesn't stay "⟳ kato working" after Claude has already finished.

        Safe to call at any time — skips tasks whose session is still alive
        and working so running comments are never interrupted.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        advanced: list[dict[str, object]] = []
        for record in self._safe_list_workspaces():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            store = self._comment_store_for(task_id)
            if store is None:
                continue
            try:
                comments = store.list()
            except Exception:
                continue
            in_progress = [
                c for c in comments
                if c.kato_status == KatoCommentStatus.IN_PROGRESS.value
            ]
            if not in_progress:
                continue
            # A stalled session is alive but no longer consuming stdin
            # (the classic post-restart ``--resume`` respawn that never
            # picked up the piped message). ``_task_has_busy_turn``
            # reports it busy (``sent > received``), which would
            # otherwise pin the comment IN_PROGRESS forever — the scan
            # loop's safety net never fires, and the operator sees kato
            # ignore the comment after a restart. Requeue so the next
            # drain force-respawns a fresh session for it.
            if self._task_session_is_stalled(task_id):
                advanced.extend(
                    self._requeue_in_progress_comments(
                        store, task_id, in_progress, reason='session stalled',
                    )
                )
                continue
            # Leave comments alone while the session is mid-turn.
            if self._task_has_busy_turn(task_id):
                continue
            session = None
            if self._session_manager is not None:
                try:
                    session = self._session_manager.get_session(task_id)
                except Exception:
                    pass
            if session is not None and getattr(session, 'is_alive', False):
                # Session alive and idle: check if a RESULT turn already fired.
                # If so, advance now (SSE subscriber may have missed the event).
                last_result = None
                try:
                    for e in reversed(session.recent_events()):
                        if getattr(e, 'event_type', None) == 'result':
                            last_result = e
                            break
                except Exception:
                    pass
                if last_result is None:
                    # Session just spawned — no completed turn yet; wait.
                    continue
                is_error = bool((getattr(last_result, 'raw', None) or {}).get('is_error', False))
                result_text = str((getattr(last_result, 'raw', None) or {}).get('result') or '')
                results = self.complete_in_progress_task_comments(
                    task_id, success=not is_error, result_text=result_text,
                )
                advanced.extend(results)
                continue
            terminal = getattr(session, 'terminal_event', None) if session else None
            if terminal is not None:
                raw = getattr(terminal, 'raw', {}) or {}
                is_error = bool(raw.get('is_error', False))
                results = self.complete_in_progress_task_comments(
                    task_id, success=not is_error,
                    result_text=str(raw.get('result') or ''),
                )
                advanced.extend(results)
            else:
                # Session gone with no terminal event (crash / restart) — requeue.
                advanced.extend(
                    self._requeue_in_progress_comments(
                        store, task_id, in_progress,
                        reason='session gone without terminal event',
                    )
                )
        return advanced

    def _requeue_in_progress_comments(
        self, store, task_id: str, comments, *, reason: str,
    ) -> list[dict[str, object]]:
        """Flip IN_PROGRESS comments back to QUEUED so the next drain redispatches them.

        Shared by ``advance_finished_comment_runs``'s stalled-session
        and session-gone branches. Best-effort per comment: a failed
        ``update_kato_status`` is logged and skipped so one bad comment
        doesn't strand the rest.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        requeued: list[dict[str, object]] = []
        for comment in comments:
            try:
                store.update_kato_status(
                    comment.id,
                    kato_status=KatoCommentStatus.QUEUED.value,
                )
                self.logger.info(
                    'comment %s on task %s requeued (%s)',
                    comment.id, task_id, reason,
                )
                requeued.append({
                    'task_id': task_id,
                    'comment_id': comment.id,
                    'action': 'requeued',
                })
            except Exception:
                self.logger.exception(
                    'failed to requeue stuck comment %s on task %s',
                    comment.id, task_id,
                )
        return requeued

    def resolve_task_comment(
        self,
        task_id: str,
        comment_id: str,
        *,
        resolved_by: str = '',
    ) -> dict[str, object]:
        """Mark a comment thread resolved (operator-driven).

        Independent of ``kato_status`` — kato may have already
        addressed the comment (``ADDRESSED``) and the operator
        decides whether to keep the thread open for review or
        close it.

        For ``source=remote`` comments, ALSO mirrors the resolve
        back to the source git platform: posts a reply explaining
        why kato thought it was addressed (when applicable) and
        resolves the thread there too. Best-effort — a platform
        failure leaves the local store resolved but flags the
        sync gap in the response so the UI can surface it.
        """
        from kato_core_lib.comment_core_lib import (
            CommentSource,
            CommentStatus,
            KatoCommentStatus,
        )

        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'error': 'no workspace for task'}
        updated = store.update_status(
            comment_id,
            status=CommentStatus.RESOLVED.value,
            resolved_by=resolved_by or 'operator',
        )
        if updated is None:
            return {'ok': False, 'error': f'comment {comment_id!r} not found'}
        remote_sync = {'attempted': False}
        if updated.source == CommentSource.REMOTE.value and updated.remote_id:
            # When kato had already addressed it, post the
            # "addressed" reply too so the source thread carries
            # context. Otherwise just resolve.
            include_reply = (
                updated.kato_status == KatoCommentStatus.ADDRESSED.value
            )
            remote_sync = self._sync_resolve_to_remote(
                task_id, updated, include_reply=include_reply,
            )
        return {'ok': True, 'comment': updated.to_dict(), 'remote_sync': remote_sync}

    def mark_comment_addressed(
        self,
        task_id: str,
        comment_id: str,
        *,
        addressed_sha: str = '',
        post_remote_reply: bool = True,
    ) -> dict[str, object]:
        """Move ``kato_status`` to ADDRESSED + (for remote) post a reply.

        Called after a kato run produces a fix for a comment. Two
        side-effects on remote-sourced comments:

          1. ``kato_status`` flips to ADDRESSED on the local
             record so the UI's pipeline pill switches to
             ``✓ kato addressed``.
          2. Posts the "Kato addressed this review comment and
             pushed a follow-up update" reply on the source git
             platform (same wording as the autonomous review-fix
             flow, via ``review_comment_reply_body``) so reviewers
             see the same thread continuity they get from kato's
             other paths.

        Resolve on the source is left to the operator's explicit
        Resolve click — kato is *not* the right authority to
        decide whether the reviewer's ask is fully addressed,
        only to claim "I shipped a fix, please confirm."
        """
        from kato_core_lib.comment_core_lib import (
            CommentSource,
            KatoCommentStatus,
        )

        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'error': 'no workspace for task'}
        updated = store.update_kato_status(
            comment_id,
            kato_status=KatoCommentStatus.ADDRESSED.value,
            addressed_sha=str(addressed_sha or ''),
        )
        if updated is None:
            return {'ok': False, 'error': f'comment {comment_id!r} not found'}
        remote_reply = {'attempted': False}
        if (
            post_remote_reply
            and updated.source == CommentSource.REMOTE.value
            and updated.remote_id
        ):
            remote_reply = self._sync_addressed_reply_to_remote(task_id, updated)
        return {'ok': True, 'comment': updated.to_dict(), 'remote_reply': remote_reply}

    def reopen_task_comment(
        self, task_id: str, comment_id: str,
    ) -> dict[str, object]:
        from kato_core_lib.comment_core_lib import (
            CommentStatus,
            KatoCommentStatus,
        )

        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'error': 'no workspace for task'}
        updated = store.update_status(
            comment_id, status=CommentStatus.OPEN.value,
        )
        if updated is None:
            return {'ok': False, 'error': f'comment {comment_id!r} not found'}
        if updated.parent_id:
            return {'ok': True, 'comment': updated.to_dict()}
        store.update_kato_status(
            comment_id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        triggered = self._maybe_trigger_comment_run(str(task_id), comment_id)
        updated = store.get(comment_id) or updated
        return {
            'ok': True,
            'comment': updated.to_dict(),
            'triggered_immediately': triggered,
        }

    def delete_task_comment(
        self, task_id: str, comment_id: str,
    ) -> dict[str, object]:
        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'error': 'no workspace for task'}
        removed = store.delete(comment_id)
        return {'ok': bool(removed), 'comment_id': comment_id}

    def sync_remote_comments(
        self, task_id: str, repo_id: str,
    ) -> dict[str, object]:
        """Pull review comments from the source git platform + git pull.

        Two-step:
          1. ``git pull`` on the workspace clone so the line
             numbers in remote comments line up with what the
             operator sees in the diff (a remote comment refers
             to a commit-shaped position; if local HEAD is behind
             those positions are stale).
          2. List PR comments via ``RepositoryService`` and
             ``upsert_remote`` each one into the local store.

        Best-effort: errors are reported in the response so the
        UI can show a toast rather than crashing the picker.
        """
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            CommentSource,
            CommentStatus,
        )

        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'error': 'no workspace for task'}
        normalized_repo = str(repo_id or '').strip()
        if not normalized_repo:
            return {'ok': False, 'error': 'repo_id is required'}
        # Look up the workspace clone for this repo so we can git
        # pull. Resolve via the workspace_manager rather than the
        # inventory entry; the inventory ``local_path`` is the
        # operator's REPOSITORY_ROOT_PATH checkout, which we
        # explicitly don't touch from kato.
        if self._workspace_manager is None:
            return {'ok': False, 'error': 'workspace manager not wired'}
        try:
            clone_path = self._workspace_manager.repository_path(
                str(task_id), normalized_repo,
            )
        except Exception as exc:
            return {'ok': False, 'error': f'no workspace clone: {exc}'}
        if not (clone_path / '.git').is_dir():
            return {'ok': False, 'error': f'workspace clone for {normalized_repo!r} missing'}
        # Pull. Best-effort — a failed pull leaves whatever was
        # already on disk (dirty tree, conflict, network error)
        # and we still try to list comments below since the
        # operator might just want the latest comments without
        # the git side.
        try:
            inventory_repo = self._repository_service.get_repository(
                normalized_repo,
            )
        except Exception:
            inventory_repo = None
        pull_result: dict[str, object] = {'ok': True}
        try:
            run_git = getattr(self._repository_service, '_run_git', None)
            if callable(run_git):
                run_git(
                    str(clone_path), ['pull', '--ff-only'],
                    f'failed to git pull workspace clone {clone_path}',
                    inventory_repo,
                )
        except Exception as exc:
            pull_result = {'ok': False, 'error': str(exc)}
        # List PR comments. The agent_service already has the
        # state-registry that tracks pull request id per task.
        synced: list[dict[str, object]] = []
        try:
            list_comments = getattr(
                self._repository_service, 'list_pull_request_comments', None,
            )
            if not callable(list_comments) or inventory_repo is None:
                return {
                    'ok': True, 'pull': pull_result,
                    'synced': [], 'note': (
                        'platform listing unavailable; pulled git only'
                    ),
                }
            pr_id = self._task_pull_request_id(str(task_id), normalized_repo)
            if not pr_id:
                return {
                    'ok': True, 'pull': pull_result,
                    'synced': [], 'note': (
                        'no pull request id on file for this repo + task'
                    ),
                }
            for entry in list_comments(inventory_repo, pr_id) or []:
                remote_id = str(
                    entry.get('id') or entry.get('comment_id') or '',
                ).strip()
                body = str(entry.get('content') or entry.get('body') or '').strip()
                if not remote_id or not body:
                    continue
                record = CommentRecord(
                    repo_id=normalized_repo,
                    file_path=str(entry.get('file_path') or ''),
                    line=int(entry.get('line') or -1),
                    parent_id=str(entry.get('parent_id') or ''),
                    author=str(entry.get('author') or ''),
                    body=body,
                    source=CommentSource.REMOTE.value,
                    remote_id=remote_id,
                    status=(
                        CommentStatus.RESOLVED.value
                        if entry.get('resolved')
                        else CommentStatus.OPEN.value
                    ),
                )
                store.upsert_remote(record)
                synced.append({'remote_id': remote_id, 'file_path': record.file_path})
        except Exception as exc:
            self.logger.exception(
                'failed to sync remote comments for task %s repo %s',
                task_id, repo_id,
            )
            return {'ok': False, 'pull': pull_result, 'error': str(exc)}
        return {'ok': True, 'pull': pull_result, 'synced': synced}

    def _sync_resolve_to_remote(
        self, task_id: str, comment, *, include_reply: bool,
    ) -> dict[str, object]:
        """Mirror an operator-resolve back to the source git platform.

        Posts an optional "Kato addressed…" reply (when kato had
        actually addressed the comment), then calls
        ``resolve_review_comment``. Best-effort each step.
        """
        result: dict[str, object] = {'attempted': True}
        try:
            inventory_repo = self._repository_service.get_repository(
                comment.repo_id,
            )
        except Exception as exc:
            result['error'] = f'inventory lookup failed: {exc}'
            return result
        pr_id = self._task_pull_request_id(task_id, comment.repo_id)
        if not pr_id:
            result['error'] = (
                'no pull request id on file — kato cannot resolve the '
                'remote thread without one. This is normal when no PR '
                'has been opened yet.'
            )
            return result
        # Build a minimal ReviewComment-like object: the publish
        # service only reads ``comment_id`` and ``pull_request_id``
        # off the argument, so a SimpleNamespace works.
        from types import SimpleNamespace
        comment_obj = SimpleNamespace(
            comment_id=comment.remote_id,
            pull_request_id=pr_id,
            repository_id=comment.repo_id,
        )
        if include_reply and comment.kato_addressed_sha:
            try:
                from kato_core_lib.helpers.review_comment_utils import (
                    review_comment_reply_body,
                )
                body = review_comment_reply_body({
                    'success': True,
                    'message': (
                        f'Addressed in commit '
                        f'{comment.kato_addressed_sha[:8]}.'
                    ),
                })
                self._repository_service.reply_to_review_comment(
                    inventory_repo, comment_obj, body,
                )
                result['reply_posted'] = True
            except Exception as exc:
                result['reply_error'] = str(exc)
        try:
            self._repository_service.resolve_review_comment(
                inventory_repo, comment_obj,
            )
            result['resolved'] = True
        except Exception as exc:
            result['resolve_error'] = str(exc)
        return result

    def _sync_addressed_reply_to_remote(
        self, task_id: str, comment,
    ) -> dict[str, object]:
        """Post the "kato addressed this" reply on the source thread.

        Same wording as the autonomous review-fix flow uses. Does
        NOT resolve the thread — leaving "should I close this?"
        as an explicit operator click.
        """
        result: dict[str, object] = {'attempted': True}
        try:
            inventory_repo = self._repository_service.get_repository(
                comment.repo_id,
            )
        except Exception as exc:
            result['error'] = f'inventory lookup failed: {exc}'
            return result
        pr_id = self._task_pull_request_id(task_id, comment.repo_id)
        if not pr_id:
            result['error'] = (
                'no pull request id on file — reply will be posted '
                'on the next sync once the PR is opened.'
            )
            return result
        from types import SimpleNamespace
        comment_obj = SimpleNamespace(
            comment_id=comment.remote_id,
            pull_request_id=pr_id,
            repository_id=comment.repo_id,
        )
        try:
            from kato_core_lib.helpers.review_comment_utils import (
                review_comment_reply_body,
            )
            body = review_comment_reply_body({
                'success': True,
                'message': (
                    f'Addressed in commit '
                    f'{comment.kato_addressed_sha[:8]}.'
                    if comment.kato_addressed_sha
                    else 'Addressed.'
                ),
            })
            self._repository_service.reply_to_review_comment(
                inventory_repo, comment_obj, body,
            )
            result['reply_posted'] = True
        except Exception as exc:
            result['reply_error'] = str(exc)
        return result

    def _comment_store_for(self, task_id: str):
        """Return the LocalCommentStore for a task — None if no workspace."""
        from kato_core_lib.comment_core_lib import LocalCommentStore

        if self._workspace_manager is None:
            return None
        normalized = str(task_id or '').strip()
        if not normalized:
            return None
        try:
            workspace_dir = self._workspace_manager.workspace_path(normalized)
        except Exception:
            return None
        if not workspace_dir.is_dir():
            return None
        return LocalCommentStore(workspace_dir)

    def _comment_dispatch_lock_for(self, task_id: str):
        """Return the per-task lock that serializes comment dispatch."""
        with self._comment_dispatch_locks_lock:
            lock = self._comment_dispatch_locks.get(task_id)
            if lock is None:
                import threading as _threading
                lock = _threading.Lock()
                self._comment_dispatch_locks[task_id] = lock
            return lock

    def _maybe_trigger_comment_run(
        self, task_id: str, comment_id: str,
    ) -> bool:
        """Kick off a review-fix agent if the task has no live turn.

        Returns True when an agent run was started immediately,
        False when the comment was left in QUEUED for later
        draining. Wraps the actual launch in try/except so a
        bad spawn just leaves the comment queued — the operator
        can retry by reopening the comment or running the queue
        drain manually.

        Serialized per-task via ``_comment_dispatch_lock_for`` so
        the busy-check → IN_PROGRESS flip → ``send_user_message``
        sequence is atomic. Two concurrent triggers (scan-tick drain
        + browser POST) used to each pass the busy check before
        either had incremented ``user_messages_sent``, each dispatch
        its own comment, and then BOTH comments got the same
        result_text attached when the FIRST RESULT fired.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        store = self._comment_store_for(task_id)
        if store is None:
            return False
        record = store.get(comment_id)
        if record is None:
            return False
        with self._comment_dispatch_lock_for(task_id):
            # Strict one-at-a-time: never dispatch a comment while another
            # is already IN_PROGRESS for this task. The session busy-checks
            # below can under-report — a respawned/resumed turn does not
            # always bump ``user_messages_sent`` — which previously let a
            # SECOND comment dispatch into the SAME turn. ``complete_in_
            # progress_task_comments`` then stamped that ONE turn's result
            # onto BOTH comments, so a reply landed on the wrong comment
            # (the "I added two comments, he replied to the wrong one"
            # report). The comment store is the authoritative serializer:
            # comments run strictly one-by-one, steered into the agent like
            # pending prompts. Fix A keeps the in-flight comment on WORKING
            # until its turn ends; the stall-requeue keeps it from getting
            # stuck; the post-turn drain releases the next one.
            if self._task_has_in_progress_comment(store, exclude_id=comment_id):
                return False
            stalled = self._task_session_is_stalled(task_id)
            live_turn_busy = self._task_has_busy_turn(task_id) and not stalled
            if live_turn_busy:
                # Stay queued; the queue drain (called from the
                # ``RESULT`` event handler) picks it up on the next
                # idle transition.
                return False
            store.update_kato_status(
                comment_id, kato_status=KatoCommentStatus.IN_PROGRESS.value,
            )
            try:
                # A stalled session is alive but not consuming stdin, so
                # ``send_user_message`` would vanish into the void and the
                # comment would sit IN_PROGRESS forever. Force a fresh
                # respawn instead so the comment actually runs.
                started = self._run_comment_agent(
                    task_id, record, force_respawn=stalled,
                )
            except Exception as exc:
                self.logger.exception(
                    'comment agent run failed for task %s comment %s',
                    task_id, comment_id,
                )
                store.update_kato_status(
                    comment_id,
                    kato_status=KatoCommentStatus.FAILED.value,
                    failure_reason=str(exc),
                )
                return False
            if not started:
                self.logger.warning(
                    'comment %s on task %s could not be started; left QUEUED '
                    'for the next scan tick to retry',
                    comment_id, task_id,
                )
                store.update_kato_status(
                    comment_id, kato_status=KatoCommentStatus.QUEUED.value,
                )
                return False
        self.logger.info(
            'comment %s on task %s dispatched to the agent', comment_id, task_id,
        )
        return True

    @staticmethod
    def _task_has_in_progress_comment(store, exclude_id: str = '') -> bool:
        """True when the task already has a comment being worked on.

        The store is the authoritative serializer for comment dispatch:
        only one comment may be IN_PROGRESS at a time so a single agent
        turn's result can never be attributed to more than one comment.
        ``exclude_id`` skips the comment currently being considered so it
        doesn't block itself. Best-effort: a store read failure reports
        "not in progress" so a transient error can't wedge the queue.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        try:
            comments = store.list()
        except Exception:
            return False
        target = str(exclude_id or '')
        for comment in comments:
            if str(getattr(comment, 'id', '') or '') == target:
                continue
            if getattr(comment, 'kato_status', '') == KatoCommentStatus.IN_PROGRESS.value:
                return True
        return False

    def _task_has_busy_turn(self, task_id: str) -> bool:
        """True when the live streaming session has any work in flight.

        "In flight" covers TWO states the dispatch path must treat as
        busy, because each one used to let a queued comment slip into
        a turn it didn't own and then be marked ADDRESSED by that
        turn's RESULT:

        1. Mid-turn (``is_working``): Claude has spoken at least one
           event for the current message but no RESULT yet.
        2. Sent-but-unacked: ``send_user_message`` has written to the
           CLI's stdin but Claude has not yet emitted its first event
           for that message. ``is_working`` walks ``_recent_events``,
           so during this race window it returns False even though
           there is a queued message waiting to be processed. Without
           this second check, a comment dispatched in that gap would
           fire its OWN ``send_user_message`` onto a "false-idle"
           session, and the PRIOR message's RESULT would then mark the
           comment ``ADDRESSED`` before its work had even started
           (visible symptom: kato's reply quoted prior-turn work and
           the chat panel was still ``thinking`` on the comment).
        """
        if self._session_manager is None:
            return False
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return False
        if session is None or not getattr(session, 'is_alive', False):
            return False
        if bool(getattr(session, 'is_working', False)):
            return True
        sent = int(getattr(session, 'user_messages_sent', 0) or 0)
        received = int(getattr(session, 'result_events_received', 0) or 0)
        return sent > received

    def _task_session_is_stalled(self, task_id: str) -> bool:
        """True when the task's session is alive but no longer processing input.

        A stalled session has a sent user message that never produced a
        ``result`` (``user_messages_sent > result_events_received``),
        is NOT actively mid-turn (``is_working`` is False), and the last
        send was longer ago than ``_COMMENT_SEND_ACK_GRACE_SECONDS``.
        That combination means the subprocess is alive but its turn loop
        has ended — writing another ``send_user_message`` would vanish
        into the void. ``_task_has_busy_turn`` reports such a session as
        busy (``sent > received``), which is what kept queued comments
        ``pending`` forever; dispatch uses this to age that gap out and
        force a fresh respawn instead. Deliberately conservative: an
        unknown last-send time (``0``) is NOT treated as stalled.
        """
        if self._session_manager is None:
            return False
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return False
        if session is None or not getattr(session, 'is_alive', False):
            return False
        if bool(getattr(session, 'is_working', False)):
            return False
        sent = int(getattr(session, 'user_messages_sent', 0) or 0)
        received = int(getattr(session, 'result_events_received', 0) or 0)
        if sent <= received:
            return False
        last_sent = float(
            getattr(session, 'last_user_message_sent_epoch', 0.0) or 0.0,
        )
        if last_sent <= 0:
            return False
        return (time.time() - last_sent) >= _COMMENT_SEND_ACK_GRACE_SECONDS

    def _run_comment_agent(
        self, task_id: str, record, force_respawn: bool = False,
    ) -> bool:
        """Hand the comment off to the streaming session as a user message.

        Sends the prompt into the live chat session when one exists and
        is healthy; otherwise (no session, dead session, or — when
        ``force_respawn`` is set — a stalled session that won't consume
        stdin) respawns Claude so the comment actually runs. The
        operator workflow is "comment lands → kato works on it".

        ``force_respawn`` is set by the dispatcher when the alive
        session is stalled: we terminate the dead-but-alive subprocess
        first so the session manager spawns a genuinely fresh one
        (``start_session`` returns the existing session untouched while
        it is still ``is_alive``), preserving the ``--resume`` id on the
        record so conversation history carries over.
        """
        prompt = self._comment_agent_prompt(task_id, record)
        if self._session_manager is None:
            return self._spawn_comment_agent(task_id, record, prompt)
        session = self._session_manager.get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return self._spawn_comment_agent(task_id, record, prompt)
        if force_respawn:
            self._terminate_stalled_session(task_id)
            return self._spawn_comment_agent(task_id, record, prompt)
        send = getattr(session, 'send_user_message', None)
        if not callable(send):
            return False
        send(prompt)
        return True

    def _terminate_stalled_session(self, task_id: str) -> None:
        """Kill a stalled-but-alive subprocess so a fresh one can spawn.

        Keeps the session RECORD (``remove_record=False``) so the
        respawn can still ``--resume`` the prior conversation id.
        Best-effort: a failure here just means the respawn may reuse the
        stalled session, which is no worse than before.
        """
        if self._session_manager is None:
            return
        terminate = getattr(self._session_manager, 'terminate_session', None)
        if not callable(terminate):
            return
        try:
            terminate(task_id, remove_record=False)
            self.logger.info(
                'terminated stalled session for task %s before respawn',
                task_id,
            )
        except Exception:
            self.logger.exception(
                'failed to terminate stalled session for task %s', task_id,
            )

    def _spawn_comment_agent(self, task_id: str, record, prompt: str) -> bool:
        """Respawn Claude for a queued local diff comment when no subprocess is alive."""
        runner = self._planning_session_runner
        if runner is None:
            # The prime "Claude is idle, not working on my comment"
            # cause: nothing can respawn the session, so the comment
            # ping-pongs QUEUED↔IN_PROGRESS every scan tick forever.
            # Make it loud instead of a silent False.
            self.logger.warning(
                'comment %s on task %s cannot start: no planning session '
                'runner wired — Claude will stay idle until a session is '
                'spawned another way',
                getattr(record, 'id', '<unknown>'), task_id,
            )
            return False
        cwd = self._comment_agent_cwd(task_id, record)
        summary = ''
        if self._workspace_manager is not None:
            workspace = self._workspace_manager.get(task_id)
            summary = str(getattr(workspace, 'task_summary', '') or '')
        self.logger.info(
            'comment %s on task %s: respawning Claude to work on it '
            '(cwd=%s)',
            getattr(record, 'id', '<unknown>'), task_id, cwd or '<none>',
        )
        runner.resume_session_for_chat(
            task_id=task_id,
            message=prompt,
            cwd=cwd,
            task_summary=summary,
        )
        return True

    def _comment_agent_cwd(self, task_id: str, record) -> str:
        """Prefer the commented repo clone, fallback to the task workspace."""
        if self._workspace_manager is None:
            return ''
        repo_id = str(getattr(record, 'repo_id', '') or '').strip()
        if repo_id:
            try:
                return str(self._workspace_manager.repository_path(task_id, repo_id))
            except Exception:
                pass
        try:
            return str(self._workspace_manager.workspace_path(task_id))
        except Exception:
            return ''

    def _comment_agent_prompt(self, task_id, record) -> str:
        file_path = str(getattr(record, 'file_path', '') or '')
        line = int(getattr(record, 'line', -1) or -1)
        body = str(getattr(record, 'body', '') or '')
        location_hint = (
            f'`{file_path}` (line {line})'
            if file_path and line > 0
            else (file_path or '(no file specified)')
        )
        # Include the thread's follow-up replies so a re-engaged run sees
        # the operator's latest pushback (e.g. "no, do it differently")
        # instead of re-doing the original comment blind. Empty for a
        # first run, so the common path is unchanged.
        thread = self._comment_thread_replies(task_id, getattr(record, 'id', ''))
        conversation = ''
        if thread:
            lines = []
            for reply in thread:
                who = 'Claude' if str(getattr(reply, 'author', '')) == 'claude' else 'Operator'
                lines.append(f'{who}: {str(getattr(reply, "body", "") or "").strip()}')
            conversation = (
                '\n\nThread so far (oldest to newest) — address the '
                'LATEST operator reply, which supersedes earlier turns:\n'
                + '\n'.join(lines)
            )
        return (
            'Operator-added review comment from the kato diff tab.\n\n'
            f'File: {location_hint}\n'
            f'Comment: {body}'
            f'{conversation}\n\n'
            'Address this comment, commit the fix on the current task '
            'branch when a code change is needed. Your final response '
            'is copied into this comment thread as Claude\'s reply, so '
            'write it directly to the reviewer. If the comment is a '
            'question rather than a fix request, answer the question '
            'without committing.'
        )

    def _comment_thread_replies(self, task_id, root_id: str) -> list:
        """Replies in the thread rooted at ``root_id``, oldest first.

        Walks each comment's parent chain to find which thread it belongs
        to, so a reply-to-a-reply still resolves to the right root. Excludes
        the root itself. Best-effort: a store failure yields no replies.
        """
        root_id = str(root_id or '')
        if not root_id:
            return []
        store = self._comment_store_for(task_id)
        if store is None:
            return []
        try:
            comments = list(store.list())
        except Exception:
            return []
        by_id = {c.id: c for c in comments}

        def root_of(comment):
            seen = set()
            cur = comment
            while cur.parent_id and cur.parent_id in by_id and cur.id not in seen:
                seen.add(cur.id)
                cur = by_id[cur.parent_id]
            return cur.id

        replies = [c for c in comments if c.id != root_id and root_of(c) == root_id]
        replies.sort(key=lambda c: float(getattr(c, 'created_at_epoch', 0) or 0))
        return replies

    def _task_pull_request_id(self, task_id: str, repo_id: str) -> str:
        """Find the source-platform PR id for a (task, repo).

        Two sources, in order:

          1. ``AgentStateRegistry`` — the review-comment service
             writes PR contexts here as it discovers them on every
             scan tick. Cheap, accurate when populated.
          2. ``RepositoryService.find_pull_requests`` against the
             task branch — falls back to a live API call when the
             registry hasn't seen this task yet (e.g. an operator
             who adopted a task that hadn't gone through the scan
             loop).

        Empty string when neither source produces a hit. Callers
        treat that as "no PR yet" and skip the platform-side push.
        """
        normalized_task_id = str(task_id or '').strip()
        normalized_repo_id = str(repo_id or '').strip()
        if not normalized_task_id or not normalized_repo_id:
            return ''
        # 1. Registry lookup. Best-effort — defensive against
        # a registry shape change.
        registry = getattr(
            self._review_comment_service, 'state_registry', None,
        )
        list_contexts = getattr(registry, 'list_pull_request_contexts', None)
        if callable(list_contexts):
            try:
                contexts = list_contexts() or []
            except Exception:
                contexts = []
            for context in contexts:
                if not isinstance(context, dict):
                    continue
                ctx_task = text_from_mapping(context, 'task_id')
                ctx_repo = text_from_mapping(context, 'repository_id')
                ctx_pr = text_from_mapping(context, 'pull_request_id')
                if ctx_task == normalized_task_id and ctx_repo == normalized_repo_id and ctx_pr:
                    return ctx_pr
        # 2. Live find_pull_requests fallback. Compute the task
        # branch on the inventory repo and ask the platform.
        try:
            inventory_repo = self._repository_service.get_repository(
                normalized_repo_id,
            )
        except Exception:
            return ''
        try:
            from types import SimpleNamespace
            task_lite = SimpleNamespace(
                id=normalized_task_id, summary='',
            )
            branch_name = self._repository_service.build_branch_name(
                task_lite, inventory_repo,
            )
        except Exception:
            return ''
        try:
            prs = self._repository_service.find_pull_requests(
                inventory_repo,
                source_branch=branch_name,
                title_prefix=f'{normalized_task_id} ',
            ) or []
        except Exception:
            return ''
        for entry in prs:
            pr_id = str(
                entry.get('id') or entry.get('pull_request_id') or '',
            ).strip()
            if pr_id:
                return pr_id
        return ''

    # ----- on-demand push / PR (planning UI buttons) -----

    def update_source_for_task(self, task_id: str) -> dict[str, object]:
        """Push + sync the operator's REPOSITORY_ROOT_PATH clones to the task branch.

        Drives the planning UI's "Update source" button. Pure git
        plumbing — no AI involvement. Two phases:

        1. ``push_task(task_id)`` — pushes the per-task workspace
           clone's branch to origin so the remote has the latest
           commits to pull from.
        2. For each repository the task touches, locate the
           corresponding clone under ``REPOSITORY_ROOT_PATH``
           (the inventory's ``local_path``) and switch it to the
           task branch via fetch / checkout / ``pull --ff-only``.
           Refuses to update a source clone that has uncommitted
           changes — the operator's running system, not a kato
           scratch space.

        Returns a per-repo summary the UI renders in the toast.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {
                'updated': False,
                'task_id': task_id,
                'error': 'empty task id',
            }
        push_result = self.push_task(normalized)
        # Even if push partially failed, attempt to update the
        # source for the repos that DID push — partial success is
        # still useful (tester can see whatever made it to origin).
        repos, _branch, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {
                'updated': False,
                'task_id': normalized,
                'pushed': push_result,
                'error': 'no workspace context for this task',
            }
        updated_repositories: list[str] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        # Per-repo warnings produced by ``update_source_to_task_branch``
        # — e.g. "stashed your changes and reapplied with conflicts".
        # Surfaced to the UI toast so the operator knows the repo did
        # update but they have something to clean up.
        warnings_per_repo: list[dict[str, object]] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(
                task_obj, repository,
            )
            # Skip repos the agent never touched. ``repository`` here is
            # the workspace-clone view (``_resolve_publish_context``
            # rewrites ``local_path`` to the workspace path), so this
            # asks "did the agent leave anything in the workspace worth
            # propagating?" — no task-branch commits and a clean tree
            # means switching the operator's source folder is busywork
            # that would yank them off whatever branch they were on.
            try:
                has_changes = self._repository_service.workspace_has_task_changes(
                    repository, branch_name,
                )
            except Exception:
                self.logger.exception(
                    'workspace-has-changes pre-check failed for task %s '
                    'repository %s',
                    normalized, repository.id,
                )
                has_changes = True
            self.logger.info(
                'update-source for task %s: %s has_changes=%s '
                '(workspace=%s, branch=%s)',
                normalized, repository.id, has_changes,
                getattr(repository, 'local_path', '<unknown>'), branch_name,
            )
            if not has_changes:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': 'no changes in workspace clone',
                })
                continue
            # Resolve the SOURCE-side repository (inventory ``local_path``,
            # i.e. the operator's running-system clone under
            # REPOSITORY_ROOT_PATH) — NOT the per-task workspace clone.
            try:
                source_repo = self._repository_service.get_repository(
                    repository.id,
                )
            except ValueError as exc:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': str(exc),
                })
                continue
            source_path = str(getattr(source_repo, 'local_path', '') or '').strip()
            if not source_path:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': 'inventory entry has no local_path '
                              '(REPOSITORY_ROOT_PATH not configured?)',
                })
                continue
            try:
                update_result = self._repository_service.update_source_to_task_branch(
                    source_repo, branch_name,
                ) or {}
                updated_repositories.append(repository.id)
                warning = text_from_mapping(update_result, 'warning')
                if warning:
                    warnings_per_repo.append({
                        'repository_id': repository.id,
                        'warning': warning,
                        'stash_conflict': bool(
                            update_result.get('stash_conflict', False),
                        ),
                    })
                self.logger.info(
                    'update-source for task %s: %s @ %s now on %s%s',
                    normalized, repository.id, source_path, branch_name,
                    f' ({warning})' if warning else '',
                )
            except RuntimeError as exc:
                # ``update_source_to_task_branch`` raises with a
                # operator-readable message (dirty tree, fetch
                # failed, fast-forward refused, etc.). Surface as
                # one-line warning, no traceback — these are
                # operator-state issues, not kato bugs.
                self.logger.warning(
                    'update-source for task %s failed for repository %s: %s',
                    normalized, repository.id, exc,
                )
                failed_repositories.append({
                    'repository_id': repository.id,
                    'error': str(exc),
                })
            except Exception as exc:
                self.logger.exception(
                    'update-source for task %s crashed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append({
                    'repository_id': repository.id,
                    'error': str(exc),
                })
        return {
            'updated': bool(updated_repositories),
            'task_id': normalized,
            'pushed': push_result,
            'updated_repositories': updated_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
            'warnings': warnings_per_repo,
        }

    def configured_destination_branch(self, repository_id: str) -> str:
        """Branch the task was forked from for ``repository_id``, per kato config.

        This is the authoritative answer for the diff base in the
        Changes tab — kato always creates a task branch off this
        ref, so ``git diff <task_branch>...origin/<destination>``
        is what the operator wants to see. Auto-detecting via git
        (``origin/HEAD``) returns the *remote's* default, which
        is wrong whenever an operator has configured a non-default
        base (e.g. ``develop`` on Bitbucket).

        Returns '' when the inventory has no entry for the repo
        (unknown id) or when neither config nor inferred default
        is available — the webserver surfaces that as a precise
        operator-facing error instead of guessing.
        """
        normalized = str(repository_id or '').strip()
        if not normalized:
            return ''
        try:
            repository = self._repository_service.get_repository(normalized)
        except Exception:
            return ''
        try:
            return self._repository_service.destination_branch(repository) or ''
        except Exception:
            # ``destination_branch`` raises when no configured value
            # AND inference fails — safe to swallow here; '' means
            # "we don't know" and the caller emits the right
            # operator-facing error.
            return ''

    def list_all_assigned_tasks(self) -> list[dict[str, str]]:
        """Return ``{id, summary, state, description}`` for every task assigned.

        Drives the planning UI's "+ Add task" picker. Spans the full
        ticket lifecycle (open / in progress / in review / done) so
        the operator can drop any of their tickets into kato — even
        completed ones for retrospective review or to re-run an
        agent against the existing branch.
        """
        try:
            tasks = self._task_service.list_all_assigned_tasks()
        except Exception:
            self.logger.exception('failed to list all assigned tasks')
            return []
        out: list[dict[str, str]] = []
        for task in tasks or []:
            out.append({
                'id': str(getattr(task, 'id', '') or ''),
                'summary': str(getattr(task, 'summary', '') or ''),
                'state': str(getattr(task, 'state', '') or ''),
                'description': str(getattr(task, 'description', '') or '')[:500],
            })
        return out

    def adopt_task(self, task_id: str) -> dict[str, object]:
        """Provision a workspace + clones for a task the operator picked.

        Drives the "+ Add task" flow on the left panel. Mirrors the
        autonomous initial-task path's first three steps (resolve
        repos → REP gate → workspace clones) so an operator-picked
        task has the same on-disk shape as one kato discovered via
        the queue scan. Skips the agent spawn — the operator will
        type into the chat tab when they're ready.

        Idempotent on already-adopted: if the workspace already
        exists, ``provision_task_workspace_clones`` reuses the
        existing clones (the create call is a no-op for an existing
        record), so re-clicking a task in the picker doesn't blow
        anything away.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'adopted': False, 'error': 'empty task id'}
        if self._workspace_manager is None:
            return {
                'adopted': False, 'task_id': normalized,
                'error': 'workspace manager not wired',
            }
        # Find the live Task — needed for tags/description-driven
        # repo resolution.
        task_obj = self._lookup_assigned_or_review_task(normalized)
        if task_obj is None:
            return {
                'adopted': False, 'task_id': normalized,
                'error': (
                    f'task {normalized!r} is not assigned to this kato '
                    f'(or the ticket platform refused the lookup)'
                ),
            }
        # Resolve all task repos via the same path the autonomous
        # flow uses; refuse the adoption when REP says no.
        try:
            repositories = self._repository_service.resolve_task_repositories(
                task_obj,
            )
        except Exception as exc:
            return {
                'adopted': False, 'task_id': normalized,
                'error': f'failed to resolve task repositories: {exc}',
            }
        # REP gate. We don't have the failure-handler chain that
        # the autonomous path uses (it posts a ticket comment),
        # so we surface as a structured error and let the UI
        # render the "approve repo first" message.
        try:
            from kato_core_lib.data_layers.service.repository_approval_service import (
                RepositoryApprovalService,
            )
            approval = RepositoryApprovalService()
            unapproved = []
            for repo in repositories:
                repo_id = str(getattr(repo, 'id', '') or '')
                if not approval.is_approved(repo_id):
                    unapproved.append(repo_id)
            if unapproved:
                return {
                    'adopted': False, 'task_id': normalized,
                    'error': (
                        f'restricted execution protocol: refusing — '
                        f'no approval on record for repository id(s) '
                        f'{", ".join(unapproved)}. Run '
                        f'``kato approve-repo`` and retry.'
                    ),
                    'unapproved_repositories': unapproved,
                }
        except Exception:
            # Approval service blew up — log and proceed; the
            # autonomous path's REP enforcement will catch it on
            # the next scan if there's a real problem.
            self.logger.exception(
                'REP approval check crashed for adopt_task on %s; '
                'skipping the gate',
                normalized,
            )
        # Provision clones via the same workspace_provisioner the
        # autonomous flow uses.
        from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )
        try:
            provisioned = provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task_obj,
                repositories,
            )
        except Exception as exc:
            self.logger.exception(
                'workspace provisioning failed for adopt_task %s', normalized,
            )
            return {
                'adopted': False, 'task_id': normalized,
                'error': f'workspace provisioning failed: {exc}',
            }
        cloned_ids = [
            str(getattr(r, 'id', '') or '') for r in (provisioned or [])
        ]
        return {
            'adopted': True,
            'task_id': normalized,
            'task_summary': str(getattr(task_obj, 'summary', '') or ''),
            'cloned_repositories': [rid for rid in cloned_ids if rid],
        }

    def _lookup_assigned_or_review_task(self, task_id: str):
        """Find ``task_id`` in the assigned or review queue (or all-list).

        Walks all three sources because the operator might pick a
        task that's no longer in the active queue (already done /
        merged) — ``list_all_assigned_tasks`` covers that case.
        """
        return find_task_by_id(
            self._task_service,
            task_id,
            queues=(
                'list_all_assigned_tasks',
                'get_assigned_tasks',
                'get_review_tasks',
            ),
        )

    def list_inventory_repositories(self) -> list[dict[str, str]]:
        """Return ``{id, owner, repo_slug, local_path}`` for every configured repo.

        Drives the Files-tab "Add repository" picker — the operator
        sees the full list of repos kato knows about (the repository
        inventory in the kato config) and picks one to attach to the
        current task. Repositories already on the task are filtered
        UI-side rather than here so the same payload can power other
        chooser UIs in the future.
        """
        try:
            inventory = self._repository_service.repositories
        except Exception:
            self.logger.exception('failed to list inventory repositories')
            return []
        out: list[dict[str, str]] = []
        for repo in inventory:
            out.append({
                'id': str(getattr(repo, 'id', '') or ''),
                'owner': str(getattr(repo, 'owner', '') or ''),
                'repo_slug': str(getattr(repo, 'repo_slug', '') or ''),
                'local_path': str(getattr(repo, 'local_path', '') or ''),
            })
        return out

    def add_task_repository(
        self, task_id: str, repository_id: str,
    ) -> dict[str, object]:
        """Tag the task with ``kato:repo:<id>`` and clone the repo.

        Drives the Files-tab "+ Add repository" flow. Two steps,
        run in order so a tag failure aborts cloning (the tag is what
        makes the resolution durable across kato restarts):

          1. ``task_service.add_tag(task_id, 'kato:repo:<id>')`` —
             idempotent on the platform side; YouTrack / Jira return
             cleanly if the tag already exists.
          2. ``sync_task_repositories(task_id)`` — provisions the
             new repo's clone into the per-task workspace via the
             same code path the operator's Sync icon uses, so a
             single missing repo is treated identically to a fresh
             multi-repo task.

        Returns the sync result enriched with ``tag_added`` so the
        UI toast can distinguish "already tagged, just cloned" from
        "tagged AND cloned".
        """
        normalized_task_id = str(task_id or '').strip()
        normalized_repo_id = str(repository_id or '').strip()
        if not normalized_task_id:
            return {'added': False, 'error': 'empty task id'}
        if not normalized_repo_id:
            return {'added': False, 'error': 'empty repository id'}
        # Defensive: only allow ids that exist in the inventory.
        # Without this, a typo or a stale tab could create a kato:repo:
        # tag pointing at a repo kato doesn't know about — the next
        # ``resolve_task_repositories`` would then raise on every
        # scan.
        try:
            inventory_ids = {
                str(getattr(r, 'id', '') or '').lower()
                for r in self._repository_service.repositories
            }
        except Exception:
            inventory_ids = set()
        if normalized_repo_id.lower() not in inventory_ids:
            return {
                'added': False,
                'task_id': normalized_task_id,
                'repository_id': normalized_repo_id,
                'error': (
                    f'repository {normalized_repo_id!r} is not in the kato '
                    f'inventory; add it to the kato config under '
                    f'``repositories`` first'
                ),
            }
        from kato_core_lib.data_layers.data.fields import RepositoryFields
        tag_name = f'{RepositoryFields.REPOSITORY_TAG_PREFIX}{normalized_repo_id}'
        tag_added = False
        try:
            # Check whether the tag is already present so the toast can
            # report "tag already there" rather than implying we did
            # something we didn't.
            existing_task = self._lookup_task_for_sync(normalized_task_id)
            existing_tags = []
            if existing_task is not None:
                raw_tags = getattr(existing_task, 'tags', None) or []
                for entry in raw_tags:
                    if isinstance(entry, dict):
                        existing_tags.append(str(entry.get('name', '') or ''))
                    else:
                        existing_tags.append(
                            str(getattr(entry, 'name', entry) or ''),
                        )
            already_tagged = any(
                t.strip().lower() == tag_name.lower()
                for t in existing_tags
            )
            if not already_tagged:
                self._task_service.add_tag(normalized_task_id, tag_name)
                tag_added = True
        except Exception as exc:
            self.logger.exception(
                'failed to add tag %s to task %s', tag_name, normalized_task_id,
            )
            return {
                'added': False,
                'task_id': normalized_task_id,
                'repository_id': normalized_repo_id,
                'error': f'failed to tag task: {exc}',
            }
        sync_result = self.sync_task_repositories(normalized_task_id)
        # Compose the response so the UI can show one toast for the
        # whole flow (tag + clone), not two.
        return {
            'added': bool(sync_result.get('synced')) or tag_added,
            'task_id': normalized_task_id,
            'repository_id': normalized_repo_id,
            'tag_added': tag_added,
            'tag_name': tag_name,
            'sync': sync_result,
        }

    def sync_task_repositories(self, task_id: str) -> dict[str, object]:
        """Add any task repos missing from the workspace; never remove.

        Drives the planning UI's "Sync repositories" icon on the Files
        tab. The flow:

          1. Fetch the live task from the ticket platform — needed for
             tags + description, which drive
             ``RepositoryService.resolve_task_repositories``.
          2. Resolve the full repo set the task touches.
          3. Compare against the workspace's current
             ``repository_ids``; anything in the task set but not in
             the workspace is "missing".
          4. Provision clones for the missing ones (mirrors the
             initial-task path).

        Never removes repos from the workspace — repos that were
        cloned but are no longer on the task stay on disk so the
        operator can still inspect / commit them. Returns a per-repo
        summary the UI renders in the toast.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {
                'synced': False,
                'task_id': task_id,
                'error': 'empty task id',
            }
        if self._workspace_manager is None:
            return {
                'synced': False,
                'task_id': normalized,
                'error': 'workspace manager not wired',
            }
        workspace = self._workspace_manager.get(normalized)
        if workspace is None:
            return {
                'synced': False,
                'task_id': normalized,
                'error': 'no workspace exists for this task yet',
            }
        task_obj = self._lookup_task_for_sync(normalized)
        if task_obj is None:
            return {
                'synced': False,
                'task_id': normalized,
                'error': (
                    'could not load task from the ticket platform — '
                    'no tags / description available to resolve repositories'
                ),
            }
        try:
            task_repos = self._repository_service.resolve_task_repositories(task_obj)
        except Exception as exc:
            return {
                'synced': False,
                'task_id': normalized,
                'error': f'failed to resolve task repositories: {exc}',
            }
        existing_ids = {
            str(rid).lower()
            for rid in (workspace.repository_ids or [])
        }
        missing_repos = [
            r for r in task_repos
            if str(getattr(r, 'id', '') or '').lower() not in existing_ids
        ]
        already_present = [
            str(getattr(r, 'id', '') or '')
            for r in task_repos
            if str(getattr(r, 'id', '') or '').lower() in existing_ids
        ]
        if not missing_repos:
            return {
                'synced': True,
                'task_id': normalized,
                'added_repositories': [],
                'already_present': already_present,
                'failed_repositories': [],
                'requires_session_restart': False,
            }
        # Provision the missing clones. ``provision_task_workspace_clones``
        # extends the workspace's ``repository_ids`` and clones each
        # repo idempotently — passing the FULL task set lets it both
        # update the metadata and skip the already-cloned ones with
        # the existing dedupe in ``WorkspaceManager.create``.
        from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )
        added: list[str] = []
        failed: list[dict[str, str]] = []
        provisioned: list = []
        try:
            provisioned = provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task_obj,
                task_repos,
            ) or []
            added = [str(getattr(r, 'id', '') or '') for r in missing_repos]
        except Exception as exc:
            self.logger.exception(
                'failed to sync repositories for task %s', normalized,
            )
            failed = [
                {'repository_id': str(getattr(r, 'id', '') or ''), 'error': str(exc)}
                for r in missing_repos
            ]
        # Critical: the freshly-cloned repos land on the remote's
        # default branch (master / main), NOT the task branch. Without
        # this preparation step the new repo's clone stays on master,
        # Claude commits to master locally, and ``push_task`` /
        # ``create_pull_request_for_task`` BOTH silently skip the new
        # repo because ``branch_needs_push(repo, feature/<task>)``
        # returns False (no commits on a branch that doesn't even
        # exist yet). The operator's symptom: changes appear in the
        # clone but never become a PR. Run ``prepare_task_branches``
        # on the newly-provisioned ones so they end up on the task
        # branch and behave like the original repos.
        added_set = {str(getattr(r, 'id', '') or '').lower() for r in missing_repos}
        newly_provisioned = [
            r for r in provisioned
            if str(getattr(r, 'id', '') or '').lower() in added_set
        ]
        branch_prep_failures: list[dict[str, str]] = []
        if newly_provisioned:
            repository_branches = {
                repo.id: self._repository_service.build_branch_name(task_obj, repo)
                for repo in newly_provisioned
            }
            try:
                self._repository_service.prepare_task_branches(
                    newly_provisioned, repository_branches,
                )
            except Exception as exc:
                self.logger.exception(
                    'failed to prepare task branches for newly-synced '
                    'repositories on task %s', normalized,
                )
                branch_prep_failures = [{
                    'repository_id': str(getattr(r, 'id', '') or ''),
                    'error': f'branch prep: {exc}',
                } for r in newly_provisioned]
        return {
            'synced': bool(added) and not failed and not branch_prep_failures,
            'task_id': normalized,
            'added_repositories': added,
            'already_present': already_present,
            'failed_repositories': failed + branch_prep_failures,
            'requires_session_restart': self._sync_requires_session_restart(
                normalized, provisioned, missing_repos,
            ),
        }

    def _sync_requires_session_restart(
        self, task_id: str, provisioned: list, missing_repos: list,
    ) -> bool:
        """Did a live Claude session miss the newly-synced repo paths?

        The Claude CLI bakes its sandbox into the subprocess at spawn
        time — there is NO in-flight widening API. So when an operator
        clicks "Sync repositories" while a chat tab is already open,
        the disk gets the new clone but the live subprocess stays
        locked to its spawn-time ``--add-dir`` set and will refuse to
        write into the new repo. The UI needs an explicit signal to
        prompt the operator to restart the tab; that signal is this
        return value.

        Returns False when:
          * no live session for the task,
          * no session manager wired,
          * the session pre-dates ``allowed_additional_dirs`` (older
            subprocess; conservative — caller treats as "no signal"),
          * every newly-added repo's clone path is ALREADY in the
            session's allowed-dir set (e.g. the operator triggered
            a no-op resync after the spawn was widened by some other
            path).
        Returns True only when there is a live session AND at least
        one newly-cloned repo lives outside the session's sandbox.
        """
        if self._session_manager is None or not provisioned:
            return False
        get_session = getattr(self._session_manager, 'get_session', None)
        if not callable(get_session):
            return False
        session = get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return False
        get_dirs = getattr(session, 'allowed_additional_dirs', None)
        if not callable(get_dirs):
            return False
        try:
            raw_dirs = get_dirs()
        except Exception:
            return False
        from pathlib import Path
        sandbox: set[str] = set()
        cwd = str(getattr(session, 'cwd', '') or '')
        if cwd:
            sandbox.add(str(Path(cwd)))
        for entry in raw_dirs or ():
            value = str(entry or '').strip()
            if value:
                sandbox.add(str(Path(value)))
        added_ids = {str(getattr(r, 'id', '') or '').lower() for r in missing_repos}
        for repo in provisioned:
            if str(getattr(repo, 'id', '') or '').lower() not in added_ids:
                continue
            local_path = str(getattr(repo, 'local_path', '') or '').strip()
            if not local_path:
                continue
            if str(Path(local_path)) not in sandbox:
                return True
        return False

    def _lookup_task_for_sync(self, task_id: str):
        """Return the live Task for ``task_id`` (or ``None``).

        ``resolve_task_repositories`` needs the real Task — the
        workspace's ``task_summary`` stub doesn't carry tags or
        description, which are what drive multi-repo resolution. We
        look across both queues (assigned + review) so the sync icon
        works while a task is in either lifecycle state.
        """
        try:
            queues = (
                self._task_service.get_assigned_tasks(),
                self._task_service.get_review_tasks(),
            )
        except Exception:
            self.logger.exception(
                'failed to load tasks for repository sync (task %s)', task_id,
            )
            return None
        for queue in queues:
            for task in queue or []:
                if task_id_matches(task, task_id):
                    return task
        return None

    def push_task(self, task_id: str) -> dict[str, object]:
        """Commit + push the task branch for every repo in its workspace.

        Used by the planning UI's ``Push`` button: surfaces the work-in-
        progress branch on the remote without opening a pull request.
        Idempotent — pushes again from where the workspace currently is.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'pushed': False, 'task_id': task_id, 'error': 'empty task id'}
        repos, _branch_name, _task = self._resolve_publish_context(normalized)
        if not repos:
            return {
                'pushed': False,
                'task_id': normalized,
                'error': 'no workspace context for this task',
            }
        pushed_repositories: list[str] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(_task, repository)
            # Only act on repos that actually have unpushed work. The
            # ``Push`` button is enabled when *any* repo on the task
            # needs pushing — without this filter we would also call
            # ``publish_review_fix`` on the in-sync repos and trip
            # ``_assert_branch_checked_out`` (workspace on master) or
            # ``RepositoryHasNoChangesError`` for them.
            try:
                needs_push = self._repository_service.branch_needs_push(
                    repository, branch_name,
                )
            except Exception:
                self.logger.exception(
                    'branch-needs-push pre-check failed for task %s repository %s',
                    normalized, repository.id,
                )
                needs_push = False
            if not needs_push:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': 'nothing to push',
                })
                continue
            try:
                self._repository_service.publish_review_fix(
                    repository,
                    branch_name,
                    commit_message=f'Update {normalized}',
                )
                pushed_repositories.append(repository.id)
                self.logger.info(
                    'on-demand push for task %s: pushed branch %s to %s',
                    normalized, branch_name, repository.id,
                )
            except _ON_DEMAND_PUSH_EXPECTED_ERRORS as exc:
                # Race fallback: state changed between the pre-check
                # and the publish call (e.g. another agent pushed). One
                # warning line, no traceback.
                self.logger.warning(
                    'on-demand push for task %s skipped repository %s: %s',
                    normalized, repository.id, exc,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
                continue
            except Exception as exc:
                self.logger.exception(
                    'on-demand push for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
        return {
            'pushed': bool(pushed_repositories),
            'task_id': normalized,
            'pushed_repositories': pushed_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
        }

    def pull_task(self, task_id: str) -> dict[str, object]:
        """Fast-forward every workspace clone of the task from its remote.

        Drives the planning UI's ``Pull`` button — symmetric to the
        ``Push`` button. Per-repo outcomes are surfaced so the
        operator sees exactly what happened (pulled, already in
        sync, refused for dirty tree, etc.) without having to look
        at logs.

        Returns:
            {
              'task_id': <id>,
              'pulled': bool,                # any repo actually moved
              'pulled_repositories': [{repository_id, commits_pulled}],
              'skipped_repositories': [{repository_id, reason, detail}],
              'failed_repositories':  [{repository_id, error}],
            }
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'pulled': False, 'task_id': task_id, 'error': 'empty task id'}
        repos, branch_name, _task = self._resolve_publish_context(normalized)
        if not repos:
            return {
                'pulled': False, 'task_id': normalized,
                'error': 'no workspace context for this task',
            }
        pulled_repositories: list[dict[str, object]] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        for repository in repos:
            repo_branch = self._repository_service.build_branch_name(_task, repository)
            try:
                outcome = self._repository_service.pull_workspace_clone(
                    repository, repo_branch,
                )
            except Exception as exc:
                self.logger.exception(
                    'on-demand pull for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
                continue
            if outcome.get('pulled') and outcome.get('updated'):
                pulled_repositories.append({
                    'repository_id': repository.id,
                    'commits_pulled': int(outcome.get('commits_pulled') or 0),
                })
                self.logger.info(
                    'on-demand pull for task %s: fast-forwarded %s by %s commit(s)',
                    normalized, repository.id, outcome.get('commits_pulled'),
                )
            elif outcome.get('pulled'):
                # ``pulled=True, updated=False`` — already in sync.
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'already_in_sync',
                    'detail': outcome.get('detail') or 'nothing to pull',
                })
            else:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'unknown',
                    'detail': outcome.get('detail') or '',
                })
        return {
            'task_id': normalized,
            'pulled': bool(pulled_repositories),
            'pulled_repositories': pulled_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
        }

    def merge_default_branch_for_task(self, task_id: str) -> dict[str, object]:
        """Fetch + merge each clone's default branch into its task branch.

        Drives the planning UI's ``Merge master`` button. The agent's
        clone can't run git itself (sandbox), so when a task branch
        drifts behind ``origin/<default>`` and conflicts, the agent
        is stuck. This does the merge on the operator's behalf; on
        conflict the markers are LEFT in the tree (not aborted) so
        the agent can resolve them by editing files.

        Returns:
            {
              'task_id': <id>,
              'merged': bool,                 # any repo cleanly merged
              'has_conflicts': bool,          # any repo left conflicted
              'merged_repositories':     [{repository_id, commits_merged}],
              'conflicted_repositories': [{repository_id, default_branch,
                                           conflicted_files: [...]}],
              'skipped_repositories':    [{repository_id, reason, detail}],
              'failed_repositories':     [{repository_id, error}],
            }
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'merged': False, 'task_id': task_id, 'error': 'empty task id'}
        repos, _branch_name, task = self._resolve_publish_context(normalized)
        if not repos:
            return {
                'merged': False, 'task_id': normalized,
                'error': 'no workspace context for this task',
            }
        merged_repositories: list[dict[str, object]] = []
        conflicted_repositories: list[dict[str, object]] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        for repository in repos:
            repo_branch = self._repository_service.build_branch_name(
                task, repository,
            )
            try:
                outcome = self._repository_service.merge_default_branch_into_clone(
                    repository, repo_branch,
                )
            except Exception as exc:
                self.logger.exception(
                    'merge-default for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
                continue
            if outcome.get('conflicts'):
                conflicted_repositories.append({
                    'repository_id': repository.id,
                    'default_branch': outcome.get('default_branch') or '',
                    'conflicted_files': list(
                        outcome.get('conflicted_files') or [],
                    ),
                })
                self.logger.info(
                    'merge-default for task %s: %s has %d conflicted file(s) '
                    'against %s — left in tree for the agent to resolve',
                    normalized, repository.id,
                    len(outcome.get('conflicted_files') or []),
                    outcome.get('default_branch'),
                )
            elif outcome.get('merged') and outcome.get('updated'):
                merged_repositories.append({
                    'repository_id': repository.id,
                    'commits_merged': int(outcome.get('commits_merged') or 0),
                    'default_branch': outcome.get('default_branch') or '',
                })
                self.logger.info(
                    'merge-default for task %s: merged %s into %s (%s commits)',
                    normalized, outcome.get('default_branch'),
                    repository.id, outcome.get('commits_merged'),
                )
            elif outcome.get('merged'):
                # merged=True, updated=False — already contained the
                # default branch, nothing to do.
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': 'already_up_to_date',
                    'detail': 'task branch already contains the default branch',
                })
            else:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'unknown',
                    'detail': outcome.get('detail') or '',
                })
        return {
            'task_id': normalized,
            'merged': bool(merged_repositories),
            'has_conflicts': bool(conflicted_repositories),
            'merged_repositories': merged_repositories,
            'conflicted_repositories': conflicted_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
        }

    def create_pull_request_for_task(self, task_id: str) -> dict[str, object]:
        """Open a PR for every repo of the task that doesn't already have one.

        Push happens as part of PR creation (the publication path stages,
        commits, and pushes before calling the host API). Repos that
        already have an open PR for this branch are skipped — surfaced
        in ``skipped_existing`` so the UI can show "PR already exists".
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'created': False, 'task_id': task_id, 'error': 'empty task id'}
        repos, _branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {
                'created': False,
                'task_id': normalized,
                'error': 'no workspace context for this task',
            }
        created: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(task_obj, repository)
            try:
                existing = self._repository_service.find_pull_requests(
                    repository, source_branch=branch_name,
                )
            except Exception:
                self.logger.exception(
                    'on-demand PR for task %s: PR lookup failed in repository %s',
                    normalized, repository.id,
                )
                existing = []
            if existing:
                first = existing[0] if isinstance(existing[0], dict) else {}
                skipped.append({
                    'repository_id': repository.id,
                    'url': str(first.get('url', '') or ''),
                })
                continue
            try:
                # Reuse the canonical title builder so on-demand PRs
                # match the autonomous flow's format: ``<id> <summary>``,
                # not the older ``Implement <id>`` placeholder. Same
                # helper task_publisher uses for the auto-published
                # flow, so PR titles are consistent regardless of which
                # path opened them.
                from kato_core_lib.helpers.pull_request_utils import pull_request_title
                title = pull_request_title(task_obj)
                pull_request = self._repository_service.create_pull_request(
                    repository,
                    title=title,
                    source_branch=branch_name,
                    description=str(getattr(task_obj, 'summary', '') or ''),
                    commit_message=title,
                )
                created.append({
                    'repository_id': repository.id,
                    'url': str(pull_request.get('url', '') or ''),
                })
                self.logger.info(
                    'on-demand PR for task %s: opened %s in %s',
                    normalized, pull_request.get('url', ''), repository.id,
                )
            except _ON_DEMAND_PUSH_EXPECTED_ERRORS as exc:
                # "No changes to publish" race fallback — the workspace
                # state shifted between the pre-check and the create
                # call. One warning line, no traceback.
                self.logger.warning(
                    'on-demand PR for task %s skipped repository %s: %s',
                    normalized, repository.id, exc,
                )
                failed.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
            except RuntimeError as exc:
                # The publish path raises bare RuntimeError for two
                # well-known cases: "expected branch X but found Y"
                # (workspace drift — handled by the boot-time realigner
                # and the diff-tab self-heal) and "remote rejected
                # ... reference already exists" (Git push of a branch
                # the remote already has at a different commit). Both
                # are operator-visible state issues, not kato bugs —
                # surface as a one-line warning, no stack trace.
                if 'expected repository' in str(exc) or 'reference already exists' in str(exc):
                    self.logger.warning(
                        'on-demand PR for task %s skipped repository %s: %s',
                        normalized, repository.id, exc,
                    )
                    failed.append(
                        {'repository_id': repository.id, 'error': str(exc)},
                    )
                else:
                    self.logger.exception(
                        'on-demand PR for task %s failed in repository %s',
                        normalized, repository.id,
                    )
                    failed.append(
                        {'repository_id': repository.id, 'error': str(exc)},
                    )
            except Exception as exc:
                self.logger.exception(
                    'on-demand PR for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
        return {
            'created': bool(created),
            'task_id': normalized,
            'created_pull_requests': created,
            'skipped_existing': skipped,
            'failed_repositories': failed,
        }

    def finish_task_planning_session(self, task_id: str) -> dict[str, object]:
        """Finalize a wait-planning chat task in one call.

        Equivalent to the operator clicking, in sequence: ``Push`` →
        ``Pull request`` → manually moving the ticket to In Review on
        the issue tracker. Idempotent: if everything is already pushed
        and the PR already exists, only the ticket-state move runs.
        Used by both the backend sentinel detector (Claude printed
        ``<KATO_TASK_DONE>``) and the planning UI's ``Done`` button.

        Returns a summary the UI can render — operator gets one
        notification per repo with what happened.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {
                'finished': False,
                'task_id': task_id,
                'error': 'empty task id',
            }
        push_result = self.push_task(normalized)
        pr_result = self.create_pull_request_for_task(normalized)
        moved_to_review = False
        move_error = ''
        try:
            self._task_state_service.move_task_to_review(normalized)
            moved_to_review = True
            self.logger.info(
                'finished planning session for task %s: moved to In Review',
                normalized,
            )
        except Exception as exc:
            move_error = str(exc) or exc.__class__.__name__
            # Full traceback to the kato terminal so the operator can
            # diagnose state-machine / auth / config issues. UI also
            # surfaces the message inline via the /finish response.
            self.logger.exception(
                'failed to move task %s to In Review during finish',
                normalized,
            )
        # Lesson capture (best-effort, non-blocking). When configured,
        # ``LessonsService`` extracts a one-line rule from the task and
        # writes it to the per-task lesson file. Runs in a background
        # thread so the finish call's response time isn't tied to an
        # LLM round-trip; failures stay inside the worker.
        self._kick_lesson_extraction(normalized, push_result, pr_result)
        return {
            'finished': moved_to_review,
            'task_id': normalized,
            'pushed': push_result,
            'pull_request': pr_result,
            'moved_to_review': moved_to_review,
            'move_error': move_error,
        }

    def _kick_lesson_extraction(
        self,
        task_id: str,
        push_result,
        pr_result,
    ) -> None:
        """Fire lesson extraction for a just-finished task in a worker thread.

        Context handed to the LLM is intentionally compact: task id,
        task summary (when retrievable), and a short trail of what
        publish did. The extractor is constrained to output a single
        concrete rule or NO_LESSON — long context isn't useful.
        """
        if self._lessons_service is None:
            return
        import threading

        try:
            task = self._task_service.get_task(task_id)
            task_summary = str(getattr(task, 'summary', '') or '')
            task_description = str(getattr(task, 'description', '') or '')
        except Exception:
            task_summary = ''
            task_description = ''

        context_parts = [f'Task summary: {task_summary or "(none)"}']
        if task_description:
            context_parts.append(f'Task description:\n{task_description}')
        context_parts.append(f'Push result: {push_result!r}')
        context_parts.append(f'Pull request result: {pr_result!r}')
        task_context = '\n\n'.join(context_parts)

        def _run() -> None:
            try:
                self._lessons_service.extract_and_save(task_id, task_context)
            except Exception:
                # Service already logs; swallow so the worker thread
                # never crashes anything visible to the operator.
                pass

        worker = threading.Thread(
            target=_run,
            name=f'kato-lesson-extract-{task_id}',
            daemon=True,
        )
        worker.start()

    def task_publish_state(self, task_id: str) -> dict[str, object]:
        """Workspace + push-readiness + PR-existence summary for the UI.

        Drives the disabled state of the planning UI's Push and Pull
        request buttons:

        - ``has_workspace=False``    → no workspace on disk yet; both
          buttons stay disabled.
        - ``has_changes_to_push``    → the Push button is enabled when
          *any* repo has unpushed work (dirty tree, branch never pushed,
          or local ahead of ``origin/<branch>``); disabled when every
          repo is in sync with its remote.
        - ``has_pull_request``       → the Pull request button is
          disabled and the existing URL is surfaced as a hint.

        Best-effort: any per-repo lookup failure is ignored so a
        transient git/API hiccup doesn't lock the UI buttons forever.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {
                'has_workspace': False,
                'has_changes_to_push': False,
                'has_pull_request': False,
            }
        repos, _branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {
                'has_workspace': False,
                'has_changes_to_push': False,
                'has_pull_request': False,
            }
        has_pull_request = False
        has_changes_to_push = False
        pull_request_urls: list[str] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(task_obj, repository)
            if not has_changes_to_push:
                try:
                    if self._repository_service.branch_needs_push(
                        repository, branch_name,
                    ):
                        has_changes_to_push = True
                except Exception:
                    self.logger.exception(
                        'branch-needs-push check failed for task %s repository %s',
                        normalized, repository.id,
                    )
            try:
                existing = self._repository_service.find_pull_requests(
                    repository, source_branch=branch_name,
                )
            except Exception:
                self.logger.exception(
                    'PR lookup failed for task %s repository %s',
                    normalized, repository.id,
                )
                continue
            if existing:
                has_pull_request = True
                first = existing[0] if isinstance(existing[0], dict) else {}
                url = str(first.get('url', '') or '')
                if url:
                    pull_request_urls.append(url)
        return {
            'has_workspace': True,
            'has_changes_to_push': has_changes_to_push,
            'has_pull_request': has_pull_request,
            'pull_request_urls': pull_request_urls,
        }

    def _resolve_publish_context(self, task_id: str):
        """Build (repos-with-local-path, branch_name, task-lite) for ``task_id``.

        Reads the workspace record + the inventory repositories, then
        rewrites ``local_path`` on each repo to its workspace clone path
        (the same shape :func:`provision_task_workspace_clones` produces
        for the autonomous flow). Returns ``([], '', None)`` whenever the
        task has no on-disk workspace — both UI buttons rely on that as
        the "disable everything" signal.
        """
        if self._workspace_manager is None:
            return [], '', None
        workspace = self._workspace_manager.get(task_id)
        if workspace is None:
            return [], '', None
        rewritten = []
        for repository_id in workspace.repository_ids:
            try:
                inventory_repo = self._repository_service.get_repository(repository_id)
            except ValueError:
                # Inventory lookup failed (e.g. REPOSITORY_ROOT_PATH points to a
                # missing directory). Build a minimal stub so git-only operations
                # (push, branch-check) still work. PR API calls need full credentials
                # and will fail gracefully in their own try/except blocks.
                clone_path = self._workspace_manager.repository_path(task_id, repository_id)
                clone_path_str = str(clone_path) if clone_path else ''
                if not clone_path_str:
                    self.logger.debug(
                        'workspace for task %s references unknown repository %s '
                        'and has no clone path; skipping',
                        task_id, repository_id,
                    )
                    continue
                self.logger.debug(
                    'workspace for task %s references unknown repository %s; '
                    'using workspace clone stub (inventory unavailable)',
                    task_id, repository_id,
                )
                rewritten.append(SimpleNamespace(id=repository_id, local_path=clone_path_str))
                continue
            clone_path = self._workspace_manager.repository_path(task_id, repository_id)
            rewritten_repo = copy.copy(inventory_repo)
            rewritten_repo.local_path = str(clone_path)
            rewritten.append(rewritten_repo)
        if not rewritten:
            return [], '', None
        task_lite = _PublishTaskLite(
            id=task_id, summary=str(workspace.task_summary or ''),
        )
        # build_branch_name only reads ``id`` / ``summary`` so the lite
        # object is a faithful stand-in here.
        branch_name = self._repository_service.build_branch_name(task_lite, rewritten[0])
        return rewritten, branch_name, task_lite

    def _start_task_processing(self, task: Task, prepared_task: PreparedTaskContext) -> bool:
        try:
            self._log_task_step(task.id, 'moving issue to in progress')
            self._task_state_service.move_task_to_in_progress(task.id)
            self._log_task_step(task.id, 'moved issue to in progress')
        except Exception as exc:
            self._task_failure_handler.handle_task_failure(task, exc, prepared_task=prepared_task)
            return False
        self._task_publisher.comment_task_started(task, prepared_task.repositories)
        return True

    def _run_task_implementation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting implementation')
        # ``kato:wait-planning`` is short-circuited earlier — by the time we
        # get here the task is one we *will* execute. Route through the
        # streaming runner when it's wired so the user can watch the work
        # (and intercept permission prompts) in the planning UI. Permission
        # modes are baked into the runner's defaults at construction time.
        runner = self._planning_session_runner
        try:
            if runner is not None:
                self._log_task_step(
                    task.id,
                    'streaming planning session (kato:wait-planning + bypass=false)',
                )
                execution = runner.implement_task(task, prepared_task=prepared_task) or {}
            else:
                execution = self._implementation_service.implement_task(
                    task,
                    prepared_task=prepared_task,
                ) or {}
        except SessionStoppedByUserError:
            # User clicked Stop — do NOT call handle_started_task_failure.
            # Moving the task back to "Open" would trigger an immediate
            # re-spawn; instead leave the task in its current state and
            # let the user decide (Resume button, manual ticket update, etc.).
            self.logger.info(
                'task %s: session stopped by user — skipping failure handler',
                task.id,
            )
            return None
        except Exception as exc:
            self.logger.exception('implementation request failed for task %s', task.id)
            self._task_failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return None
        if not implementation_succeeded(execution):
            self._task_failure_handler.handle_implementation_failure(
                task,
                execution,
                prepared_task=prepared_task,
            )
            return None
        self._log_task_step(
            task.id,
            'implementation completed successfully%s',
            session_suffix(execution),
        )
        return execution

    def _run_task_testing_validation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict[str, str | bool],
    ) -> tuple[bool, dict | None, dict[str, str | bool]]:
        if self._skip_testing:
            execution = dict(execution)
            execution.pop(ImplementationFields.MESSAGE, None)
            self._log_task_step(task.id, 'testing validation skipped by configuration')
            return True, None, execution
        if not self._task_preflight_service.validate_task_branch_publishability(
            task,
            prepared_task,
            failure_handler=self._task_failure_handler.handle_started_task_failure,
        ):
            return False, None, execution
        self._log_task_step(task.id, 'task branches contain changes')
        testing = self._request_testing_validation(task, prepared_task)
        if testing is None:
            return False, None, execution
        if not testing_succeeded(testing):
            self._task_failure_handler.handle_testing_failure(
                task,
                testing,
                prepared_task=prepared_task,
            )
            return False, testing_failed_result(task.id), execution
        execution = apply_testing_message(execution, testing)
        self._log_task_step(task.id, 'testing validation passed')
        return True, None, execution

    def _request_testing_validation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting testing validation')
        try:
            return self._testing_service.test_task(
                task,
                prepared_task=prepared_task,
            ) or {}
        except Exception as exc:
            self.logger.exception('testing request failed for task %s', task.id)
            self._task_failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return None
