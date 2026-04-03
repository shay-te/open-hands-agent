from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_failure_handler import TaskFailureHandler
from openhands_agent.data_layers.service.task_state_service import TaskStateService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.helpers.error_handling_utils import run_best_effort
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.mission_logging_utils import log_mission_step
from openhands_agent.helpers.pull_request_utils import (
    pull_request_description,
    pull_request_repositories_text,
    pull_request_summary_comment,
    pull_request_title,
)
from openhands_agent.helpers.text_utils import text_from_mapping
from openhands_agent.helpers.task_context_utils import PreparedTaskContext, task_started_comment
from openhands_agent.helpers.task_execution_utils import task_execution_report


class TaskPublisher(Service):
    """Publish finished task work as pull requests, summary comments, and completion notifications."""
    def __init__(
        self,
        task_service: TaskService,
        task_state_service: TaskStateService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        state_registry: AgentStateRegistry,
        failure_handler: TaskFailureHandler,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._state_registry = state_registry
        self._failure_handler = failure_handler
        self.logger = logger or configure_logger(self.__class__.__name__)

    def publish_task_execution(
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

    def comment_task_started(
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

    def _task_commit_message(
        self,
        task: Task,
    ) -> str:
        return f'Implement {task.id}'

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
                title=pull_request_title(task),
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

    def _comment_pull_request_summary(
        self,
        task: Task,
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
        execution_report = task_execution_report(execution)
        self._comment_task_completed(
            task,
            pull_requests,
            failed_repositories,
            execution_report,
        )

    def _comment_task_completed(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
        execution_report: str = '',
    ) -> bool:
        try:
            self._task_service.add_comment(
                task.id,
                pull_request_summary_comment(
                    task,
                    pull_requests,
                    failed_repositories,
                    execution_report,
                ),
            )
            self._log_task_step(task.id, 'added review summary comment')
            return True
        except Exception:
            self.logger.exception('failed to add review summary comment for task %s', task.id)
            return False

    def _partial_publish_result(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
    ) -> dict[str, object]:
        self._failure_handler.handle_started_task_failure(
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
            self._task_state_service.move_task_to_review(task.id)
        except Exception as exc:
            self._failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
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

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        log_mission_step(self.logger, task_id, message, *args)
