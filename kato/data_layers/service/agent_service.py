from __future__ import annotations

from core_lib.data_layers.service.service import Service

from kato.data_layers.service.agent_state_registry import AgentStateRegistry
from kato.data_layers.service.task_failure_handler import TaskFailureHandler
from kato.data_layers.service.review_comment_service import ReviewCommentService
from kato.data_layers.service.task_publisher import TaskPublisher
from kato.data_layers.service.task_state_service import TaskStateService
from kato.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from kato.helpers.logging_utils import configure_logger
from kato.helpers.mission_logging_utils import log_mission_step
from kato.data_layers.data.task import Task
from kato.data_layers.service.implementation_service import ImplementationService
from kato.helpers.task_context_utils import PreparedTaskContext, session_suffix
from kato.data_layers.service.notification_service import NotificationService
from kato.data_layers.service.repository_service import RepositoryService
from kato.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato.data_layers.service.task_service import TaskService
from kato.data_layers.service.testing_service import TestingService
from kato.data_layers.data.fields import ImplementationFields
from kato.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato.validation.branch_push import TaskBranchPushValidator
from kato.validation.model_access import TaskModelAccessValidator
from kato.helpers.task_execution_utils import (
    apply_testing_message,
    implementation_succeeded,
    skip_task_result,
    testing_failed_result,
    testing_succeeded,
)


class AgentService(Service):
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
    ) -> None:
        self.logger = configure_logger(self.__class__.__name__)
        if testing_service is None:
            raise ValueError('testing_service is required')
        if task_state_service is None:
            raise ValueError('task_state_service is required')
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

    def shutdown(self) -> None:
        """Stop all active OpenHands conversations to remove agent-server containers."""
        self._implementation_service.stop_all_conversations()
        self._testing_service.stop_all_conversations()

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_service.get_assigned_tasks()

    def get_new_pull_request_comments(self) -> list:
        self._cleanup_done_task_conversations()
        return self._review_comment_service.get_new_pull_request_comments()

    def _cleanup_done_task_conversations(self) -> None:
        """Delete conversation containers for tasks no longer in the review state.

        When a reviewer merges a PR and moves the task to done, Kato detects
        it is missing from the review-task list and removes the associated
        agent-server container to avoid accumulation.
        """
        try:
            current_review_task_ids = {
                str(task.id) for task in self._task_service.get_review_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch review tasks for conversation cleanup; skipping'
            )
            return

        stale_task_ids = self._state_registry.tracked_task_ids() - current_review_task_ids
        for task_id in stale_task_ids:
            for session_id in self._state_registry.session_ids_for_task(task_id):
                self.logger.info(
                    'task %s is no longer in review; stopping conversation %s',
                    task_id,
                    session_id,
                )
                try:
                    self._implementation_service.delete_conversation(session_id)
                except Exception:
                    self.logger.warning(
                        'failed to stop conversation %s for done task %s',
                        session_id,
                        task_id,
                    )
            self._state_registry.forget_task(task_id)

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
        return skip_task_result(
            task_id,
            self._state_registry.processed_task_pull_requests(task_id),
        )

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

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        log_mission_step(self.logger, task_id, message, *args)
