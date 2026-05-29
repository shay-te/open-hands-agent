from __future__ import annotations

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.helpers.error_handling_utils import run_best_effort
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import MissionStepLoggerMixin
from kato_core_lib.helpers.task_comment_utils import add_task_comment
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


# Operator-facing failure comments for the three "kato can't even
# start this task" cases. Centralised here because both
# ``TaskFailureHandler`` and ``TaskPreflightService`` reach for the
# same wording, and silent drift between the two would mean
# operators get different instructions for the same problem.
#
# Goals for these messages: name the exact thing that's wrong,
# show the exact tag/env/file the operator has to touch, give a
# concrete example, and call out which command (if any) helps.

TASK_DEFINITION_TOO_THIN_COMMENT = (
    'Kato agent skipped this task because the task definition is too '
    'thin to work from safely.\n'
    '\n'
    '**How to fix:** add a description (or a comment on this issue) '
    'that explains:\n'
    '  • **what** should change (file, function, or feature),\n'
    '  • **why** (the bug, the requirement, the behaviour you want),\n'
    '  • **how kato will know it worked** (a test name, an '
    'acceptance criterion, an example before/after).\n'
    '\n'
    'A one-line summary like "fix the bug" is not enough — kato '
    'needs enough context to reach for the right files. Once you '
    'have updated the description, re-run the task by removing and '
    're-adding the `kato:run` tag.'
)


def _failure_comment_for(
    error: Exception,
    *,
    lead: str = 'Kato agent could not safely process this task',
) -> str:
    """Build the operator-facing comment for a failed task.

    The default body is just ``<lead>: <error>``. For known error
    shapes that carry structured detail (currently only
    :class:`SecurityScanBlocked`), append the long-form breakdown
    so the operator can see WHICH findings tripped the gate without
    having to dig into logs. The short ``str(error)`` line stays —
    it's what shows up in the YouTrack notification preview.
    """
    body = f'{lead}: {error}'
    detail = _security_scan_detail(error)
    if detail:
        body = f'{body}\n\n{detail}'
    return body


def _security_scan_detail(error: Exception) -> str:
    """Markdown breakdown for a SecurityScanBlocked, or '' if not one.

    Lazy-imported so the failure handler doesn't pull
    ``security_scanner_core_lib`` at module load — that lib carries
    runner subprocess code that's not always on the path during
    tests / embedded use.
    """
    try:
        from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
            SecurityScanBlocked,
            SecurityScannerService,
        )
    except ImportError:
        return ''
    if not isinstance(error, SecurityScanBlocked):
        return ''
    # The service formats the markdown; we instantiate a throwaway
    # one — its config doesn't matter for ``summarize_for_ticket``
    # since the report carries its own threshold + findings.
    try:
        return SecurityScannerService().summarize_for_ticket(error.report)
    except Exception:
        # Never let a formatting bug eat the short-line failure
        # comment — operator still sees ``str(error)`` on top.
        return ''


def repository_detection_comment(error: Exception) -> str:
    """Operator-facing comment when kato can't pick a repository.

    We surface ``error`` because it usually contains the agent's
    own reasoning ("found two candidate repos: client, server —
    can't choose"). The fix instructions are constant: tag the
    issue with ``kato:repo:<id>``.
    """
    return (
        'Kato agent skipped this task because it could not detect '
        f'which repository to use from the task content: {error}\n'
        '\n'
        '**How to fix:** tag this issue with the repository kato '
        'should work in. Add a tag of the form '
        '`kato:repo:<repository-id>` (e.g. '
        '`kato:repo:ob-love-admin-client`). The id has to match a '
        '`repositories` entry in your kato config — if you are not '
        'sure which ids exist, run `./kato approve-repo` on the '
        'kato host; the picker lists every repo kato knows about.'
    )


def repository_ignored_comment(error: Exception) -> str:
    """Operator-facing comment when a tagged repo is on the
    ignore list.

    Two possible fixes (remove from ignore list, or change the
    tag) — surface both so the operator picks based on intent.
    """
    return (
        'Kato refused to run this task because one of its '
        '`kato:repo:<name>` tags points at a repository in '
        '`KATO_IGNORED_REPOSITORY_FOLDERS` (the ignore list in '
        f'`<kato>/.env`).\n'
        f'\n'
        f'Details: {error}\n'
        '\n'
        '**How to fix** (pick one):\n'
        '  • Remove the offending entry from '
        '`KATO_IGNORED_REPOSITORY_FOLDERS` in `<kato>/.env` and '
        'restart kato — use this if the ignore was a mistake.\n'
        '  • Change the task\'s `kato:repo:<name>` tag to point at '
        'a different repository — use this if the ignore is '
        'correct and the task was tagged wrong.'
    )


class TaskFailureHandler(MissionStepLoggerMixin, Service):
    """Own task failure recovery, user-facing failure comments, and follow-up notifications."""
    def __init__(
        self,
        task_service: TaskService,
        task_state_service: TaskStateService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self.logger = logger or configure_logger(self.__class__.__name__)

    def handle_repository_resolution_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        if self._is_repository_ignored_by_config(error):
            self._handle_repository_ignored_by_config(task, error)
            return
        if self._is_repository_detection_failure(error):
            self._handle_repository_detection_failure(task, error)
            return
        self.handle_task_failure(task, error, prepared_task=prepared_task)

    def handle_task_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._restore_task_repositories(task, prepared_task=prepared_task)
        self._report_task_failure(
            task,
            error,
            _failure_comment_for(error),
            prepared_task=prepared_task,
        )

    def handle_started_task_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._restore_task_repositories(task, prepared_task=prepared_task)
        self._report_task_failure(
            task,
            error,
            _failure_comment_for(error, lead='Kato agent stopped working on this task'),
            move_to_open=True,
            prepared_task=prepared_task,
        )

    def handle_testing_failure(
        self,
        task: Task,
        testing: dict[str, str | bool],
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._handle_unsuccessful_agent_result(
            task,
            testing,
            prepared_task=prepared_task,
            default_summary='testing agent reported the task is not ready',
            warning_log_message='testing failed for task %s: %s',
        )

    def handle_implementation_failure(
        self,
        task: Task,
        execution: dict[str, str | bool],
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._handle_unsuccessful_agent_result(
            task,
            execution,
            prepared_task=prepared_task,
            default_summary='implementation agent reported the task is not ready',
            warning_log_message='implementation failed for task %s: %s',
        )

    def handle_task_definition_failure(self, task: Task) -> None:
        self._log_task_step(task.id, 'recording task-definition skip comment')
        self._add_task_comment(
            task.id,
            TASK_DEFINITION_TOO_THIN_COMMENT,
            after_step='added task-definition skip comment',
            failure_log_message='failed to add task definition comment for task %s',
        )

    def _handle_repository_detection_failure(self, task: Task, error: Exception) -> None:
        self._log_task_step(task.id, 'recording repository detection skip comment')
        self._add_task_comment(
            task.id,
            repository_detection_comment(error),
            after_step='added repository detection skip comment',
            failure_log_message='failed to add repository detection comment for task %s',
        )

    def _handle_repository_ignored_by_config(self, task: Task, error: Exception) -> None:
        # The operator has explicitly told kato to ignore this folder
        # via KATO_IGNORED_REPOSITORY_FOLDERS but the task tagged it
        # anyway. Treat as a clean reject — post an actionable comment,
        # don't move the task to a failed state, don't notify ops. The
        # next scan will hit the same rejection until either the tag
        # or the ignore list is fixed.
        self._log_task_step(task.id, 'rejecting task: tag points at ignored repository')
        self._add_task_comment(
            task.id,
            repository_ignored_comment(error),
            after_step='added ignored-repository rejection comment',
            failure_log_message=(
                'failed to add ignored-repository rejection comment for task %s'
            ),
        )

    def _handle_unsuccessful_agent_result(
        self,
        task: Task,
        payload: dict[str, str | bool],
        *,
        prepared_task: PreparedTaskContext | None = None,
        default_summary: str,
        warning_log_message: str,
    ) -> None:
        summary = str(payload.get(Task.summary.key) or default_summary)
        self.logger.warning(warning_log_message, task.id, summary)
        self.handle_started_task_failure(
            task,
            RuntimeError(summary),
            prepared_task=prepared_task,
        )

    def _report_task_failure(
        self,
        task: Task,
        error: Exception,
        comment: str,
        *,
        move_to_open: bool = False,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._log_task_step(task.id, 'recording failure comment: %s', comment)
        self._add_task_comment(
            task.id,
            comment,
            after_step='added failure comment',
            failure_log_message='failed to add failure comment for task %s',
        )
        if move_to_open:
            self._move_task_to_open(task.id)
        run_best_effort(
            lambda: self._notification_service.notify_failure(
                'process_assigned_task',
                error,
                {Task.id.key: task.id},
            ),
            logger=self.logger,
            failure_log_message='failed to send failure notification for task %s',
            failure_args=(task.id,),
            default=False,
        )
        _record_task_failed(task, error, prepared_task)

    def _restore_task_repositories(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        repositories = prepared_task.repositories if prepared_task is not None else []
        repositories = repositories or []
        if not repositories:
            return
        self._log_task_step(task.id, 'restoring repository branches after task rejection')
        try:
            self._repository_service.restore_task_repositories(
                repositories,
                force=True,
            )
        except Exception:
            self.logger.exception('failed to restore repositories for task %s', task.id)

    def _add_task_comment(
        self,
        task_id: str,
        comment: str,
        *,
        after_step: str = '',
        failure_log_message: str,
    ) -> bool:
        return add_task_comment(
            self._task_service,
            self.logger,
            self._log_task_step,
            task_id,
            comment,
            after_step=after_step,
            failure_log_message=failure_log_message,
        )

    def _move_task_to_open(self, task_id: str) -> bool:
        try:
            self._log_task_step(task_id, 'moving issue back to open')
            self._task_state_service.move_task_to_open(task_id)
            self._log_task_step(task_id, 'moved issue back to open')
            return True
        except Exception:
            self.logger.exception('failed to move task %s back to open', task_id)
            return False

    @staticmethod
    def _is_repository_detection_failure(error: Exception) -> bool:
        return isinstance(error, ValueError) and 'no configured repository matched task' in str(error)

    @staticmethod
    def _is_repository_ignored_by_config(error: Exception) -> bool:
        # Imported lazily — task_failure_handler is loaded early in the
        # service-construction graph and we'd rather avoid a possibly-
        # circular import at module load.
        from kato_core_lib.data_layers.service.repository_inventory_service import (
            RepositoryIgnoredByConfigError,
        )
        return isinstance(error, RepositoryIgnoredByConfigError)


def _record_task_failed(task, error, prepared_task) -> None:
    """Append a task_failed audit record. Best-effort.

    Funneled here so every failure path that reaches
    ``_report_task_failure`` produces exactly one audit row. Skip
    paths (definition-too-thin, ignored-repo) deliberately do not
    pass through this funnel — those are kato refusals, not failures.
    """
    from kato_core_lib.helpers.audit_log_utils import (
        EVENT_TASK_FAILED,
        OUTCOME_FAILURE,
        append_task_audit_event,
    )

    append_task_audit_event(
        task,
        prepared_task,
        event=EVENT_TASK_FAILED,
        outcome=OUTCOME_FAILURE,
        error=str(error)[:500] if error else '',
    )
