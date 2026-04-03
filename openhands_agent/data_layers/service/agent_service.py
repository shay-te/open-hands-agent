from collections.abc import Callable

from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.review_comment_service import ReviewCommentService
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
from openhands_agent.helpers.error_handling_utils import run_best_effort
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
    TaskFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.helpers.pull_request_utils import (
    pull_request_description,
    pull_request_repositories_text,
    pull_request_summary_comment,
)
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
from openhands_agent.helpers.text_utils import text_from_mapping


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
            task_failure_handler=self._handle_task_failure,
            repository_resolution_failure_handler=self._handle_repository_resolution_failure,
            repository_preparation_failure_handler=self._handle_task_failure,
            task_definition_failure_handler=self._handle_task_definition_failure,
            branch_preparation_failure_handler=self._handle_task_failure,
            branch_push_failure_handler=self._handle_started_task_failure,
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
        return self._publish_task_execution(task, prepared_task, execution)

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
            self._move_task_to_in_progress(task.id, strict=True)
        except Exception as exc:
            self._handle_task_failure(task, exc, prepared_task=prepared_task)
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
            self._handle_started_task_failure(task, exc, prepared_task=prepared_task)
            return None
        if not self._implementation_succeeded(execution):
            self._handle_implementation_failure(task, execution, prepared_task=prepared_task)
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
            self._handle_started_task_failure(task, exc, prepared_task=prepared_task)
            return False, None, execution
        self._log_task_step(task.id, 'task branches contain changes')
        testing = self._request_testing_validation(task, prepared_task)
        if testing is None:
            return False, None, execution
        if not self._testing_succeeded(testing):
            self._handle_testing_failure(task, testing, prepared_task=prepared_task)
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
            self._handle_started_task_failure(task, exc, prepared_task=prepared_task)
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

    def _publish_task_execution(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict[str, str | bool],
    ) -> dict | None:
        self._log_task_step(task.id, 'publishing pull requests')
        pull_requests, failed_repositories = self._create_pull_requests(
            task,
            prepared_task,
            execution,
        )
        self._comment_pull_request_summary(
            task,
            prepared_task,
            pull_requests,
            failed_repositories,
            execution,
        )
        if failed_repositories:
            return self._partial_publish_result(
                task,
                prepared_task,
                pull_requests,
                failed_repositories,
            )
        return self._complete_successful_publish(task, prepared_task, pull_requests)

    def _comment_pull_request_summary(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
        execution: dict[str, str | bool],
    ) -> None:
        if not pull_requests:
            return
        self._log_task_step(
            task.id,
            'adding review summary comment for %s',
            pull_request_repositories_text(pull_requests),
        )
        execution_report = self._task_execution_report(execution)
        self._comment_task_completed(
            task,
            pull_requests,
            failed_repositories,
            execution_report,
        )

    def _partial_publish_result(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
    ) -> dict[str, object]:
        self._handle_started_task_failure(
            task,
            RuntimeError(
                f'failed to create pull requests for repositories: '
                f'{", ".join(failed_repositories)}'
            ),
            prepared_task=prepared_task,
        )
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: StatusFields.PARTIAL_FAILURE,
            PullRequestFields.PULL_REQUESTS: pull_requests,
            PullRequestFields.FAILED_REPOSITORIES: failed_repositories,
        }

    def _complete_successful_publish(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        pull_requests: list[dict[str, str]],
    ) -> dict[str, object] | None:
        try:
            self._move_task_to_review(task.id, strict=True)
        except Exception as exc:
            self._handle_started_task_failure(task, exc, prepared_task=prepared_task)
            return None
        # Record success before notification so a notification failure cannot
        # cause duplicate publish work on a later retry.
        self._state_registry.mark_task_processed(task.id, pull_requests)
        self._notify_task_ready_for_review(task, pull_requests)
        self._log_task_step(task.id, 'workflow completed successfully')
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: pull_requests,
            PullRequestFields.FAILED_REPOSITORIES: [],
        }

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

    def _create_pull_requests(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict[str, str | bool],
    ) -> tuple[list[dict[str, str]], list[str]]:
        pull_requests: list[dict[str, str]] = []
        failed_repositories: list[str] = []
        description = pull_request_description(task, execution)
        session_id = text_from_mapping(execution, ImplementationFields.SESSION_ID)
        commit_message = self._task_commit_message(task)
        for repository in prepared_task.repositories or []:
            pull_request = self._create_pull_request_for_repository(
                task,
                prepared_task,
                repository,
                description,
                commit_message,
                session_id,
            )
            if pull_request is None:
                failed_repositories.append(repository.id)
                continue
            pull_requests.append(pull_request)

        return pull_requests, failed_repositories

    @staticmethod
    def _task_commit_message(
        task: Task,
    ) -> str:
        return f'Implement {task.id}'

    @staticmethod
    def _task_execution_report(execution: dict[str, str | bool]) -> str:
        report_lines: list[str] = []
        implementation_summary = str(execution.get(Task.summary.key, '') or '').strip()
        if implementation_summary:
            report_lines.append('Implementation summary:')
            report_lines.append(implementation_summary)
        validation_report = str(execution.get(ImplementationFields.MESSAGE, '') or '').strip()
        if validation_report:
            report_lines.append('Validation report:')
            report_lines.append(validation_report)
        return '\n'.join(report_lines)

    def _create_pull_request_for_repository(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        repository,
        description: str,
        commit_message: str,
        session_id: str,
    ) -> dict[str, str] | None:
        branch_name = prepared_task.repository_branches[repository.id]
        pull_request = self._create_repository_pull_request(
            task,
            repository,
            branch_name,
            description,
            commit_message,
        )
        if pull_request is None:
            return None
        self._record_created_pull_request(
            task,
            repository,
            branch_name,
            session_id,
            pull_request,
        )
        return pull_request

    def _create_repository_pull_request(
        self,
        task: Task,
        repository,
        branch_name: str,
        description: str,
        commit_message: str,
    ) -> dict[str, str] | None:
        try:
            self._log_pull_request_creation(task.id, repository, branch_name)
            return self._repository_service.create_pull_request(
                repository,
                title=f'{task.id}: {task.summary}',
                source_branch=branch_name,
                description=description,
                commit_message=commit_message,
            )
        except Exception:
            self.logger.exception(
                'failed to create pull request for task %s in repository %s',
                task.id,
                repository.id,
            )
            return None

    def _record_created_pull_request(
        self,
        task: Task,
        repository,
        branch_name: str,
        session_id: str,
        pull_request: dict[str, str],
    ) -> None:
        self._state_registry.remember_pull_request_context(
            pull_request,
            branch_name,
            session_id,
            str(task.id or ''),
            str(task.summary or ''),
        )
        self.logger.info(
            'created pull request %s for task %s in repository %s',
            pull_request[PullRequestFields.ID],
            task.id,
            repository.id,
        )
        self._log_task_step(
            task.id,
            'created pull request for repository %s: %s',
            repository.id,
            pull_request.get(PullRequestFields.URL, ''),
        )

    def _log_pull_request_creation(
        self,
        task_id: str,
        repository,
        branch_name: str,
    ) -> None:
        self._log_task_step(
            task_id,
            'creating pull request for repository %s from branch %s into %s',
            repository.id,
            branch_name,
            getattr(repository, 'destination_branch', '') or 'the default branch',
        )

    def _notify_task_ready_for_review(self, task: Task, pull_requests) -> None:
        def notify_task_ready_for_review() -> None:
            self._log_task_step(
                task.id,
                'sending completion notification for %s',
                pull_request_repositories_text(pull_requests),
            )
            self._notification_service.notify_task_ready_for_review(task, pull_requests)
            self._log_task_step(task.id, 'completion notification sent')

        run_best_effort(
            notify_task_ready_for_review,
            logger=self.logger,
            failure_log_message='failed to send completion notification for task %s',
            failure_args=(task.id,),
        )

    def _handle_testing_failure(
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

    def _handle_implementation_failure(
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

    def _handle_task_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._restore_task_repositories(task, prepared_task=prepared_task)
        self._report_task_failure(
            task,
            error,
            f'OpenHands agent could not safely process this task: {error}',
        )

    def _handle_started_task_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._restore_task_repositories(task, prepared_task=prepared_task)
        self._report_task_failure(
            task,
            error,
            f'OpenHands agent stopped working on this task: {error}',
            move_to_open=True,
        )

    def _report_task_failure(
        self,
        task: Task,
        error: Exception,
        comment: str,
        *,
        move_to_open: bool = False,
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

    def _comment_task_started(
        self,
        task: Task,
        repositories: list[object] | None = None,
    ) -> None:
        self._log_task_step(task.id, 'adding started comment')
        self._add_task_comment(
            task.id,
            task_started_comment(task, repositories),
            after_step='added started comment',
            failure_log_message='failed to add started comment for task %s',
        )

    def _comment_task_completed(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
        execution_report: str = '',
    ) -> bool:
        return self._add_task_comment(
            task.id,
            pull_request_summary_comment(
                task,
                pull_requests,
                failed_repositories,
                execution_report,
            ),
            after_step='added review summary comment',
            failure_log_message='failed to add review summary comment for task %s',
        )

    def _move_task_to_in_progress(self, task_id: str, strict: bool = False) -> bool:
        return self._move_task_state(
            task_id,
            self._task_service.move_task_to_in_progress,
            before_step='moving issue to in progress',
            after_step='moved issue to in progress',
            failure_log_message='failed to move task %s to in progress',
            strict=strict,
        )

    def _move_task_to_open(self, task_id: str) -> bool:
        return self._move_task_state(
            task_id,
            self._task_service.move_task_to_open,
            before_step='moving issue back to open',
            after_step='moved issue back to open',
            failure_log_message='failed to move task %s back to open',
        )

    def _move_task_to_review(self, task_id: str, strict: bool = False) -> bool:
        return self._move_task_state(
            task_id,
            self._task_service.move_task_to_review,
            before_step='moving issue to review',
            after_step='moved issue to review',
            failure_log_message='failed to move task %s to review',
            strict=strict,
        )

    @staticmethod
    def _is_repository_detection_failure(error: Exception) -> bool:
        return isinstance(error, ValueError) and 'no configured repository matched task' in str(error)

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        formatted_message = message
        if args:
            try:
                formatted_message = message % args
            except Exception:
                formatted_message = ' '.join([message, *[str(arg) for arg in args]])
        self.logger.info('Mission %s: %s', task_id, formatted_message)

    def _handle_repository_resolution_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        if self._is_repository_detection_failure(error):
            self._handle_repository_detection_failure(task, error)
            return
        self._handle_task_failure(task, error, prepared_task=prepared_task)

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
        self._handle_started_task_failure(
            task,
            RuntimeError(summary),
            prepared_task=prepared_task,
        )

    def _move_task_state(
        self,
        task_id: str,
        move_task: Callable[[str], None],
        *,
        before_step: str,
        after_step: str,
        failure_log_message: str,
        strict: bool = False,
    ) -> bool:
        try:
            self._log_task_step(task_id, before_step)
            move_task(task_id)
            self._log_task_step(task_id, after_step)
            return True
        except Exception:
            self.logger.exception(failure_log_message, task_id)
            if strict:
                raise
            return False

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
