import logging
import traceback

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.testing_service import TestingService

class AgentService:
    def __init__(
        self,
        task_data_access: TaskDataAccess,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
    ) -> None:
        if testing_service is None:
            raise ValueError('testing_service is required')
        if notification_service is None:
            raise ValueError('notification_service is required')
        self._task_data_access = task_data_access
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._pull_request_context_map: dict[str, list[dict[str, str]]] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        validations = [
            ('youtrack', self._task_data_access.validate_connection),
            ('openhands', self._implementation_service.validate_connection),
            ('openhands_testing', self._testing_service.validate_connection),
            ('repositories', self._repository_service.validate_connections),
        ]
        failures: list[str] = []

        for service_name, validate in validations:
            try:
                validate()
                self.logger.info('validated %s connection', service_name)
            except Exception:
                self.logger.exception('failed to validate %s connection', service_name)
                failures.append(
                    f'[{service_name}]\n{traceback.format_exc().rstrip()}'
                )

        if failures:
            raise RuntimeError(
                'startup dependency validation failed:\n\n' + '\n\n'.join(failures)
            )

    def process_assigned_tasks(self) -> list[dict]:
        results: list[dict] = []

        for task in self._task_data_access.get_assigned_tasks():
            self.logger.info('processing task %s', task.id)
            try:
                repositories = self._repository_service.resolve_task_repositories(task)
            except Exception as exc:
                self.logger.exception('failed to resolve repositories for task %s', task.id)
                self._handle_task_failure(task, exc)
                continue

            repository_branches = {
                repository.id: self._repository_service.build_branch_name(task, repository)
                for repository in repositories
            }
            task.branch_name = next(iter(repository_branches.values()))
            setattr(task, 'repositories', repositories)
            setattr(task, 'repository_branches', repository_branches)
            execution = self._implementation_service.implement_task(task) or {}
            if not self._implementation_succeeded(execution):
                self.logger.warning('implementation failed for task %s', task.id)
                continue
            testing = self._testing_service.test_task(task) or {}
            if not self._testing_succeeded(testing):
                self._handle_testing_failure(task, testing)
                results.append(
                    {
                        Task.id.key: task.id,
                        StatusFields.STATUS: StatusFields.TESTING_FAILED,
                        PullRequestFields.PULL_REQUESTS: [],
                        PullRequestFields.FAILED_REPOSITORIES: [],
                    }
                )
                continue

            pull_requests, failed_repositories = self._create_pull_requests(
                task,
                execution,
            )
            if pull_requests:
                self._task_data_access.add_comment(
                    task.id,
                    self._pull_request_summary_comment(task, pull_requests, failed_repositories),
                )
            if failed_repositories:
                self._handle_task_failure(
                    task,
                    RuntimeError(
                        f'failed to create pull requests for repositories: '
                        f'{", ".join(failed_repositories)}'
                    ),
                )
                results.append(
                    {
                        Task.id.key: task.id,
                        StatusFields.STATUS: StatusFields.PARTIAL_FAILURE,
                        PullRequestFields.PULL_REQUESTS: pull_requests,
                        PullRequestFields.FAILED_REPOSITORIES: failed_repositories,
                    }
                )
                continue

            self._task_data_access.move_task_to_review(task.id)
            self._notify_task_ready_for_review(task, pull_requests)
            results.append(
                {
                    Task.id.key: task.id,
                    StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
                    PullRequestFields.PULL_REQUESTS: pull_requests,
                    PullRequestFields.FAILED_REPOSITORIES: [],
                }
            )

        return results

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        comment = self._implementation_service.review_comment_from_payload(payload)
        self.logger.info(
            'processing review comment %s for pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        pull_request_contexts = self._pull_request_context_map.get(comment.pull_request_id, [])
        if not pull_request_contexts:
            raise ValueError(f'unknown pull request id: {comment.pull_request_id}')
        if len(pull_request_contexts) > 1:
            raise ValueError(
                f'ambiguous pull request id across repositories: {comment.pull_request_id}'
            )
        context = pull_request_contexts[0]
        branch_name = context[Task.branch_name.key]
        setattr(comment, PullRequestFields.REPOSITORY_ID, context[PullRequestFields.REPOSITORY_ID])

        execution = self._implementation_service.fix_review_comment(comment, branch_name) or {}
        if not execution.get(ImplementationFields.SUCCESS, False):
            raise RuntimeError(f'failed to address comment {comment.comment_id}')

        return {
            StatusFields.STATUS: StatusFields.UPDATED,
            ReviewComment.pull_request_id.key: comment.pull_request_id,
            Task.branch_name.key: branch_name,
            PullRequestFields.REPOSITORY_ID: context[PullRequestFields.REPOSITORY_ID],
        }

    @staticmethod
    def _implementation_succeeded(execution: dict[str, str | bool]) -> bool:
        return bool(execution.get(ImplementationFields.SUCCESS, False))

    @staticmethod
    def _testing_succeeded(testing: dict[str, str | bool]) -> bool:
        return bool(testing.get(ImplementationFields.SUCCESS, False))

    def _create_pull_requests(
        self,
        task: Task,
        execution: dict[str, str | bool],
    ) -> tuple[list[dict[str, str]], list[str]]:
        pull_requests: list[dict[str, str]] = []
        failed_repositories: list[str] = []
        description = str(execution.get(Task.summary.key) or '')

        for repository in getattr(task, 'repositories', []) or []:
            branch_name = task.repository_branches[repository.id]
            try:
                pull_request = self._repository_service.create_pull_request(
                    repository,
                    title=f'{task.id}: {task.summary}',
                    source_branch=branch_name,
                    description=description,
                )
                self._remember_pull_request_context(pull_request, branch_name)
                pull_requests.append(pull_request)
                self.logger.info(
                    'created pull request %s for task %s in repository %s',
                    pull_request[PullRequestFields.ID],
                    task.id,
                    repository.id,
                )
            except Exception:
                self.logger.exception(
                    'failed to create pull request for task %s in repository %s',
                    task.id,
                    repository.id,
                )
                failed_repositories.append(repository.id)

        return pull_requests, failed_repositories

    def _remember_pull_request_context(
        self,
        pull_request: dict[str, str],
        branch_name: str,
    ) -> None:
        pull_request_id = pull_request[PullRequestFields.ID]
        self._pull_request_context_map.setdefault(pull_request_id, []).append(
            {
                PullRequestFields.REPOSITORY_ID: pull_request[PullRequestFields.REPOSITORY_ID],
                Task.branch_name.key: branch_name,
            }
        )

    def _notify_task_ready_for_review(self, task: Task, pull_requests) -> None:
        try:
            self._notification_service.notify_task_ready_for_review(task, pull_requests)
        except Exception:
            self.logger.exception('failed to send completion notification for task %s', task.id)

    def _handle_testing_failure(self, task: Task, testing: dict[str, str | bool]) -> None:
        summary = str(testing.get(Task.summary.key) or 'testing agent reported the task is not ready')
        self.logger.warning('testing failed for task %s: %s', task.id, summary)
        self._handle_task_failure(task, RuntimeError(summary))

    def _handle_task_failure(self, task: Task, error: Exception) -> None:
        try:
            self._task_data_access.add_comment(
                task.id,
                f'OpenHands agent could not safely process this task: {error}',
            )
        except Exception:
            self.logger.exception('failed to add failure comment for task %s', task.id)
        try:
            self._notification_service.notify_failure(
                'process_assigned_task',
                error,
                {Task.id.key: task.id},
            )
        except Exception:
            self.logger.exception('failed to send failure notification for task %s', task.id)

    @staticmethod
    def _pull_request_summary_comment(
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
    ) -> str:
        lines = [f'OpenHands completed task {task.id}: {task.summary}.']
        if pull_requests:
            lines.append('')
            lines.append('Created pull requests:')
            for pull_request in pull_requests:
                lines.append(
                    f'- {pull_request[PullRequestFields.REPOSITORY_ID]}: '
                    f'{pull_request[PullRequestFields.URL]}'
                )
        if failed_repositories:
            lines.append('')
            lines.append(
                'Failed repositories: ' + ', '.join(failed_repositories)
            )
        return '\n'.join(lines)
