from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.task_failure_handler import TaskFailureHandler
from openhands_agent.data_layers.service.review_comment_service import ReviewCommentService
from openhands_agent.data_layers.service.task_publisher import TaskPublisher
from openhands_agent.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from openhands_agent.validation.branch_push import (
    TaskBranchPushValidator,
)
from openhands_agent.validation.model_access import (
    TaskModelAccessValidator,
)
from openhands_agent.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from openhands_agent.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.helpers.task_context_utils import (
    PreparedTaskContext,
    session_suffix,
    task_started_comment,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.data_layers.service.testing_service import TestingService


class AgentService(Service):
    # NOTE: Task and review coordination state is kept in memory only.
    # It is not durable across process restarts.
    def __init__(
        self,
        task_service: TaskService,
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
        task_branch_publishability_validator: TaskBranchPublishabilityValidator | None = None,
        skip_testing: bool = False,
    ) -> None:
        self.logger = configure_logger(self.__class__.__name__)
        if testing_service is None:
            raise ValueError('testing_service is required')
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
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._skip_testing = bool(skip_testing)
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
        )
        self._task_branch_publishability_validator = (
            task_branch_publishability_validator
            or TaskBranchPublishabilityValidator(self._repository_service)
        )
        self._task_publisher = task_publisher or TaskPublisher(
            self._task_service,
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

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_service.get_assigned_tasks()

    def get_new_pull_request_comments(self) -> list:
        return self._review_comment_service.get_new_pull_request_comments()

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        return self._review_comment_service.handle_pull_request_comment(payload)

    def process_review_comment(self, comment):
        return self._review_comment_service.process_review_comment(comment)

    def process_assigned_task(self, task: Task) -> dict | None:
        processed_result = self._processed_task_result(task.id)
        if processed_result is not None:
            return processed_result

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
        return self._task_publisher.publish_task_execution(task, prepared_task, execution)

    def _processed_task_result(self, task_id: str) -> dict | None:
        if not self._state_registry.is_task_processed(task_id):
            return None
        self.logger.info('skipping already processed task %s', task_id)
        return self._skip_task_result(
            task_id,
            self._state_registry.processed_task_pull_requests(task_id),
        )

    def _start_task_processing(self, task: Task, prepared_task: PreparedTaskContext) -> bool:
        try:
            self._log_task_step(task.id, 'moving issue to in progress')
            self._task_service.move_task_to_in_progress(task.id)
            self._log_task_step(task.id, 'moved issue to in progress')
        except Exception as exc:
            self._task_failure_handler.handle_task_failure(task, exc, prepared_task=prepared_task)
            return False
        self._comment_task_started(task, prepared_task.repositories)
        return True

    def _run_task_implementation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting implementation')
        try:
            execution = self._implementation_service.implement_task(
                task,
                prepared_task=prepared_task,
            ) or {}
        except Exception as exc:
            self.logger.exception('implementation request failed for task %s', task.id)
            self._task_failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return None
        if not self._implementation_succeeded(execution):
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
        try:
            self._task_branch_publishability_validator.validate(
                prepared_task.repositories,
                prepared_task.repository_branches,
            )
        except Exception as exc:
            self.logger.exception(
                'failed to validate task branches before testing for task %s',
                task.id,
            )
            self._task_failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return False, None, execution
        self._log_task_step(task.id, 'task branches contain changes')
        testing = self._request_testing_validation(task, prepared_task)
        if testing is None:
            return False, None, execution
        if not self._testing_succeeded(testing):
            self._task_failure_handler.handle_testing_failure(
                task,
                testing,
                prepared_task=prepared_task,
            )
            return False, self._testing_failed_result(task.id), execution
        execution = self._apply_testing_message(execution, testing)
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

    @staticmethod
    def _testing_failed_result(task_id: str) -> dict[str, object]:
        return {
            Task.id.key: task_id,
            StatusFields.STATUS: StatusFields.TESTING_FAILED,
            PullRequestFields.PULL_REQUESTS: [],
            PullRequestFields.FAILED_REPOSITORIES: [],
        }

    @staticmethod
    def _apply_testing_message(
        execution: dict[str, str | bool],
        testing: dict[str, str | bool],
    ) -> dict[str, str | bool]:
        testing_message = str(
            testing.get(ImplementationFields.MESSAGE, '') or ''
        ).strip()
        if testing_message:
            execution = dict(execution)
            execution[ImplementationFields.MESSAGE] = testing_message
        return execution

    @staticmethod
    def _implementation_succeeded(execution: dict[str, str | bool]) -> bool:
        return bool(execution.get(ImplementationFields.SUCCESS, False))

    @staticmethod
    def _testing_succeeded(testing: dict[str, str | bool]) -> bool:
        return bool(testing.get(ImplementationFields.SUCCESS, False))

    @staticmethod
    def _skip_task_result(
        task_id: str,
        pull_requests: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {
            Task.id.key: task_id,
            StatusFields.STATUS: StatusFields.SKIPPED,
            PullRequestFields.PULL_REQUESTS: pull_requests or [],
            PullRequestFields.FAILED_REPOSITORIES: [],
        }

    def _comment_task_started(
        self,
        task: Task,
        repositories: list[object] | None = None,
    ) -> None:
        self._log_task_step(task.id, 'adding started comment')
        try:
            self._task_service.add_comment(
                task.id,
                task_started_comment(task, repositories),
            )
            self._log_task_step(task.id, 'added started comment')
        except Exception:
            self.logger.exception('failed to add started comment for task %s', task.id)

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        formatted_message = message
        if args:
            try:
                formatted_message = message % args
            except Exception:
                formatted_message = ' '.join([message, *[str(arg) for arg in args]])
        self.logger.info('Mission %s: %s', task_id, formatted_message)
