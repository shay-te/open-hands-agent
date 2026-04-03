from collections.abc import Callable

from core_lib.data_layers.service.service import Service

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.fields import TaskCommentFields
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.mission_logging_utils import log_mission_step
from openhands_agent.helpers.task_context_utils import (
    PreparedTaskContext,
    repository_branch_text,
    repository_destination_text,
    repository_ids_text,
    task_has_actionable_definition,
)
from openhands_agent.helpers.task_execution_utils import skip_task_result
from openhands_agent.validation.branch_push import TaskBranchPushValidator
from openhands_agent.validation.model_access import TaskModelAccessValidator
from openhands_agent.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)


class TaskPreflightService(Service):
    """Prepare a task for execution by validating access, repositories, and branch readiness."""
    def __init__(
        self,
        task_model_access_validator: TaskModelAccessValidator,
        task_service: TaskService,
        repository_service: RepositoryService,
        task_branch_push_validator: TaskBranchPushValidator,
        task_branch_publishability_validator: TaskBranchPublishabilityValidator,
        logger=None,
    ) -> None:
        self._task_model_access_validator = task_model_access_validator
        self._task_service = task_service
        self._repository_service = repository_service
        self._task_branch_push_validator = task_branch_push_validator
        self._task_branch_publishability_validator = task_branch_publishability_validator
        self.logger = logger or configure_logger(self.__class__.__name__)

    def prepare_task_execution_context(
        self,
        task: Task,
        *,
        task_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        repository_resolution_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        repository_preparation_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        task_definition_failure_handler: Callable[[Task], None] | None = None,
        branch_preparation_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        branch_push_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> PreparedTaskContext | dict | None:
        try:
            self._task_model_access_validator.validate(task)
        except Exception as exc:
            self.logger.exception('failed to validate model access for task %s', task.id)
            if task_failure_handler is not None:
                task_failure_handler(task, exc, None)
            return None
        self._log_task_step(task.id, 'OpenHands model access validated')

        blocking_comment = self._active_execution_blocking_comment(task)
        if not blocking_comment:
            self._log_task_step(
                task.id,
                'starting mission: %s',
                str(task.summary or '').strip() or task.id,
            )
            return self._prepare_initial_task_start(
                task,
                repository_resolution_failure_handler=repository_resolution_failure_handler,
                repository_preparation_failure_handler=repository_preparation_failure_handler,
                task_definition_failure_handler=task_definition_failure_handler,
                branch_preparation_failure_handler=branch_preparation_failure_handler,
                branch_push_failure_handler=branch_push_failure_handler,
            )

        prepared_task = self._check_retry_preconditions(task, blocking_comment)
        if prepared_task is None or isinstance(prepared_task, dict):
            return prepared_task

        self._log_task_step(
            task.id,
            'starting mission: %s',
            str(task.summary or '').strip() or task.id,
        )
        return prepared_task

    def _prepare_initial_task_start(
        self,
        task: Task,
        *,
        repository_resolution_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        repository_preparation_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        task_definition_failure_handler: Callable[[Task], None] | None = None,
        branch_preparation_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        branch_push_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> PreparedTaskContext | None:
        return self._prepare_task_start(
            task,
            repository_resolution_failure_handler=repository_resolution_failure_handler,
            repository_preparation_failure_handler=repository_preparation_failure_handler,
            task_definition_failure_handler=task_definition_failure_handler,
            branch_preparation_failure_handler=branch_preparation_failure_handler,
            branch_push_failure_handler=branch_push_failure_handler,
        )

    def _check_retry_preconditions(
        self,
        task: Task,
        blocking_comment: str,
    ) -> PreparedTaskContext | dict | None:
        if not self._can_retry_without_explicit_override(blocking_comment):
            return self._skip_blocked_task_result(task, blocking_comment)
        self._log_task_step(
            task.id,
            're-checking prior pre-start blocking comment before retry: %s',
            blocking_comment,
        )
        prepared_task = self._prepare_task_start(task)
        if prepared_task is None:
            return self._skip_blocked_task_result(task, blocking_comment)
        self._log_task_step(
            task.id,
            'prior pre-start blocking comment no longer applies; retrying task',
        )
        return prepared_task

    def _prepare_task_start(
        self,
        task: Task,
        *,
        repository_resolution_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        repository_preparation_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        task_definition_failure_handler: Callable[[Task], None] | None = None,
        branch_preparation_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
        branch_push_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> PreparedTaskContext | None:
        repositories = self._resolve_task_repositories(
            task,
            failure_handler=repository_resolution_failure_handler,
        )
        if repositories is None:
            return None
        repositories = self._prepare_task_repositories_for_start(
            task,
            repositories,
            failure_handler=repository_preparation_failure_handler,
        )
        if repositories is None:
            return None
        if not self._task_definition_ready(
            task,
            failure_handler=task_definition_failure_handler,
        ):
            return None
        prepared_task = self._attach_task_repository_context(task, repositories)
        self._log_task_step(
            task.id,
            'planned working branches: %s',
            repository_branch_text(prepared_task.repository_branches),
        )
        if not self._prepare_task_execution_branches(
            task,
            prepared_task,
            failure_handler=branch_preparation_failure_handler,
        ):
            return None
        self._log_task_step(task.id, 'prepared task branches')
        if not self.validate_task_branch_push_access(
            task,
            prepared_task,
            failure_handler=branch_push_failure_handler,
        ):
            return None
        self._log_task_step(task.id, 'task branches can be pushed')
        return prepared_task

    def _resolve_task_repositories(
        self,
        task: Task,
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> list[object] | None:
        repositories = self._run_pre_start_step(
            task,
            self._repository_service.resolve_task_repositories,
            task,
            failure_log_message='failed to resolve repositories for task %s',
            blocked_log_message=(
                'pre-start retry check is still blocked during repository resolution: %s'
            ),
            failure_handler=failure_handler,
        )
        if repositories is not None:
            self._log_task_step(
                task.id,
                'resolved repositories: %s',
                repository_ids_text(repositories),
            )
        return repositories

    def _prepare_task_repositories_for_start(
        self,
        task: Task,
        repositories: list[object],
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> list[object] | None:
        repositories = self._run_pre_start_step(
            task,
            self._repository_service.prepare_task_repositories,
            repositories,
            failure_log_message='failed to prepare repositories for task %s',
            blocked_log_message=(
                'pre-start retry check is still blocked during repository preparation: %s'
            ),
            failure_handler=failure_handler,
        )
        if repositories is not None:
            self._log_task_step(
                task.id,
                'repository preflight passed: %s',
                repository_destination_text(repositories),
            )
        return repositories

    def _task_definition_ready(
        self,
        task: Task,
        *,
        failure_handler: Callable[[Task], None] | None = None,
    ) -> bool:
        if task_has_actionable_definition(task):
            return True
        self._handle_pre_start_task_definition_failure(
            task,
            failure_handler=failure_handler,
        )
        return False

    def _prepare_task_execution_branches(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> bool:
        prepared_branches = self._run_pre_start_step(
            task,
            self._repository_service.prepare_task_branches,
            prepared_task.repositories,
            prepared_task.repository_branches,
            failure_log_message='failed to prepare task branches for task %s',
            blocked_log_message=(
                'pre-start retry check is still blocked during task-branch preparation: %s'
            ),
            prepared_task=prepared_task,
            failure_handler=failure_handler,
        )
        return prepared_branches is not None

    def validate_task_branch_push_access(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> bool:
        try:
            self._task_branch_push_validator.validate(
                prepared_task.repositories,
                prepared_task.repository_branches,
            )
        except Exception as exc:
            if failure_handler is None:
                self._log_task_step(
                    task.id,
                    'pre-start retry check is still blocked during task branch push validation: %s',
                    exc,
                )
                return False
            self.logger.exception(
                'failed to validate task branch push access for task %s',
                task.id,
            )
            failure_handler(task, exc, prepared_task)
            return False
        return True

    def validate_task_branch_publishability(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> bool:
        try:
            self._task_branch_publishability_validator.validate(
                prepared_task.repositories,
                prepared_task.repository_branches,
            )
        except Exception as exc:
            if failure_handler is None:
                log_mission_step(
                    self.logger,
                    task.id,
                    'pre-testing validation is still blocked during task branch publishability validation: %s',
                    exc,
                )
                return False
            self.logger.exception(
                'failed to validate task branch publishability for task %s',
                task.id,
            )
            failure_handler(task, exc, prepared_task)
            return False
        return True

    @staticmethod
    def _blocking_comment_kind(blocking_comment: str) -> str:
        if TicketClientBase.is_completion_comment(blocking_comment):
            return 'completion'
        if TicketClientBase.is_pre_start_blocking_comment(blocking_comment):
            return 'pre-start'
        return 'unknown'

    @staticmethod
    def _can_retry_without_explicit_override(blocking_comment: str) -> bool:
        return TicketClientBase.is_pre_start_blocking_comment(blocking_comment)

    def _skip_blocked_task_result(
        self,
        task: Task,
        blocking_comment: str,
    ) -> dict[str, object]:
        self.logger.info(
            'skipping task %s because a prior OpenHands %s comment is still active: %s',
            task.id,
            self._blocking_comment_kind(blocking_comment),
            blocking_comment,
        )
        return skip_task_result(task.id)

    def _run_pre_start_step(
        self,
        task: Task,
        step: Callable,
        *step_args,
        failure_log_message: str,
        blocked_log_message: str,
        prepared_task: PreparedTaskContext | None = None,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ):
        try:
            return step(*step_args)
        except Exception as exc:
            self._handle_pre_start_exception(
                task,
                exc,
                failure_log_message=failure_log_message,
                blocked_log_message=blocked_log_message,
                prepared_task=prepared_task,
                failure_handler=failure_handler,
            )
            return None

    def _handle_pre_start_exception(
        self,
        task: Task,
        error: Exception,
        *,
        failure_log_message: str,
        blocked_log_message: str,
        prepared_task: PreparedTaskContext | None = None,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> None:
        if failure_handler is not None:
            self.logger.exception(failure_log_message, task.id)
            failure_handler(task, error, prepared_task)
            return
        log_mission_step(self.logger, task.id, blocked_log_message, error)

    def _handle_pre_start_task_definition_failure(
        self,
        task: Task,
        *,
        failure_handler: Callable[[Task], None] | None = None,
    ) -> None:
        if failure_handler is not None:
            self.logger.info(
                'skipping task %s because the task definition is too thin to work from safely',
                task.id,
            )
            failure_handler(task)
            return
        log_mission_step(
            self.logger,
            task.id,
            'pre-start retry check is still blocked because the task definition remains too thin',
        )

    def _handle_repository_detection_failure(self, task: Task, error: Exception) -> None:
        self._log_task_step(task.id, 'recording repository detection skip comment')
        self._add_task_comment(
            task.id,
            'OpenHands agent skipped this task because it could not detect which repository '
            f'to use from the task content: {error}. '
            'Please mention the repository name or alias in the task summary or description.',
            after_step='added repository detection skip comment',
            failure_log_message='failed to add repository detection comment for task %s',
        )

    def _handle_task_definition_failure(self, task: Task) -> None:
        self._log_task_step(task.id, 'recording task-definition skip comment')
        self._add_task_comment(
            task.id,
            'OpenHands agent skipped this task because the task definition is too thin '
            'to work from safely. Please add a clearer description or issue comment '
            'describing the expected change.',
            after_step='added task-definition skip comment',
            failure_log_message='failed to add task definition comment for task %s',
        )

    def _attach_task_repository_context(
        self,
        task: Task,
        repositories: list[object],
    ) -> PreparedTaskContext:
        repository_branches = {
            repository.id: self._repository_service.build_branch_name(task, repository)
            for repository in repositories
        }
        return PreparedTaskContext(
            branch_name=next(iter(repository_branches.values()), ''),
            repositories=list(repositories),
            repository_branches=repository_branches,
        )

    def _add_task_comment(
        self,
        task_id: str,
        comment: str,
        *,
        after_step: str = '',
        failure_log_message: str,
    ) -> bool:
        try:
            self._task_service.add_comment(task_id, comment)
            if after_step:
                self._log_task_step(task_id, after_step)
            return True
        except Exception:
            self.logger.exception(failure_log_message, task_id)
            return False

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        log_mission_step(self.logger, task_id, message, *args)

    @staticmethod
    def _active_execution_blocking_comment(task: Task) -> str:
        comments = getattr(task, TaskCommentFields.ALL_COMMENTS, [])
        return TicketClientBase.active_execution_blocking_comment(comments)
