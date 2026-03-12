import logging
import traceback

from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.testing_service import TestingService

class AgentService(Service):
    def __init__(
        self,
        task_data_access: TaskDataAccess,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        state_data_access=None,
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
        self._state_data_access = state_data_access
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
        if self._state_data_access is not None:
            validations.append(('state', self._state_data_access.validate))
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

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_data_access.get_assigned_tasks()

    def process_assigned_task(self, task: Task) -> dict | None:
        if self._is_task_processed(task.id):
            self.logger.info('skipping already processed task %s', task.id)
            return {
                Task.id.key: task.id,
                StatusFields.STATUS: StatusFields.SKIPPED,
                PullRequestFields.PULL_REQUESTS: self._processed_task_pull_requests(task.id),
                PullRequestFields.FAILED_REPOSITORIES: [],
            }

        self.logger.info('processing task %s', task.id)
        try:
            repositories = self._repository_service.resolve_task_repositories(task)
        except Exception as exc:
            self.logger.exception('failed to resolve repositories for task %s', task.id)
            self._handle_task_failure(task, exc)
            return None

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
            return None

        testing = self._testing_service.test_task(task) or {}
        if not self._testing_succeeded(testing):
            self._handle_testing_failure(task, testing)
            return {
                Task.id.key: task.id,
                StatusFields.STATUS: StatusFields.TESTING_FAILED,
                PullRequestFields.PULL_REQUESTS: [],
                PullRequestFields.FAILED_REPOSITORIES: [],
            }

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
            return {
                Task.id.key: task.id,
                StatusFields.STATUS: StatusFields.PARTIAL_FAILURE,
                PullRequestFields.PULL_REQUESTS: pull_requests,
                PullRequestFields.FAILED_REPOSITORIES: failed_repositories,
            }

        self._task_data_access.move_task_to_review(task.id)
        self._mark_task_processed(task.id, pull_requests)
        self._notify_task_ready_for_review(task, pull_requests)
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: pull_requests,
            PullRequestFields.FAILED_REPOSITORIES: [],
        }

    def get_new_pull_request_comments(self) -> list[ReviewComment]:
        new_comments: list[ReviewComment] = []

        for context in self._tracked_pull_request_contexts():
            repository_id = context[PullRequestFields.REPOSITORY_ID]
            pull_request_id = context[PullRequestFields.ID]
            try:
                repository = self._repository_service.get_repository(repository_id)
                comments = self._repository_service.list_pull_request_comments(
                    repository,
                    pull_request_id,
                )
            except Exception:
                self.logger.exception(
                    'failed to fetch pull request comments for repository %s pull request %s',
                    repository_id,
                    pull_request_id,
                )
                continue

            comment_context: list[dict[str, str]] = []
            for comment in comments:
                comment_context.append(self._comment_context_entry(comment))
                setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
                setattr(comment, ReviewCommentFields.ALL_COMMENTS, list(comment_context))
                if self._is_review_comment_processed(
                    repository_id,
                    pull_request_id,
                    comment.comment_id,
                ):
                    continue
                new_comments.append(comment)

        return new_comments

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        comment = self._implementation_service.review_comment_from_payload(payload)
        return self.process_review_comment(comment)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        self.logger.info(
            'processing review comment %s for pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        repository_id = str(getattr(comment, PullRequestFields.REPOSITORY_ID, '') or '').strip()
        context = self._pull_request_context(comment.pull_request_id, repository_id)
        if context is None:
            raise ValueError(f'unknown pull request id: {comment.pull_request_id}')
        branch_name = context[Task.branch_name.key]
        repository_id = context[PullRequestFields.REPOSITORY_ID]
        setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)

        execution = self._implementation_service.fix_review_comment(comment, branch_name) or {}
        if not execution.get(ImplementationFields.SUCCESS, False):
            raise RuntimeError(f'failed to address comment {comment.comment_id}')
        self._mark_review_comment_processed(
            repository_id,
            comment.pull_request_id,
            comment.comment_id,
        )

        return {
            StatusFields.STATUS: StatusFields.UPDATED,
            ReviewCommentFields.PULL_REQUEST_ID: comment.pull_request_id,
            Task.branch_name.key: branch_name,
            PullRequestFields.REPOSITORY_ID: repository_id,
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
        if self._state_data_access is None:
            return
        try:
            self._state_data_access.remember_pull_request_context(
                pull_request_id,
                pull_request[PullRequestFields.REPOSITORY_ID],
                branch_name,
            )
        except Exception:
            self.logger.exception(
                'failed to persist pull request context for pull request %s',
                pull_request_id,
            )

    def _pull_request_context(
        self,
        pull_request_id: str,
        repository_id: str = '',
    ) -> dict[str, str] | None:
        pull_request_contexts = self._pull_request_context_map.get(pull_request_id, [])
        if not pull_request_contexts:
            pull_request_contexts = self._load_persisted_pull_request_contexts(
                pull_request_id
            )
        if repository_id:
            pull_request_contexts = [
                context
                for context in pull_request_contexts
                if context[PullRequestFields.REPOSITORY_ID] == repository_id
            ]
        if not pull_request_contexts:
            return None
        if len(pull_request_contexts) > 1:
            raise ValueError(
                f'ambiguous pull request id across repositories: {pull_request_id}'
            )
        return pull_request_contexts[0]

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

    def _is_task_processed(self, task_id: str) -> bool:
        if self._state_data_access is None:
            return False
        return self._state_data_access.is_task_processed(task_id)

    def _processed_task_pull_requests(self, task_id: str) -> list[dict[str, str]]:
        if self._state_data_access is None:
            return []
        processed_task = self._state_data_access.get_processed_task(task_id)
        pull_requests = processed_task.get(PullRequestFields.PULL_REQUESTS, [])
        return pull_requests if isinstance(pull_requests, list) else []

    def _mark_task_processed(self, task_id: str, pull_requests: list[dict[str, str]]) -> None:
        if self._state_data_access is None:
            return
        try:
            self._state_data_access.mark_task_processed(task_id, pull_requests)
        except Exception:
            self.logger.exception('failed to persist processed task state for task %s', task_id)

    def _load_persisted_pull_request_contexts(
        self,
        pull_request_id: str,
    ) -> list[dict[str, str]]:
        if self._state_data_access is None:
            return []
        try:
            return self._state_data_access.get_pull_request_contexts(pull_request_id)
        except Exception:
            self.logger.exception(
                'failed to load persisted pull request context for pull request %s',
                pull_request_id,
            )
            return []

    def _tracked_pull_request_contexts(self) -> list[dict[str, str]]:
        contexts: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()

        for pull_request_id, pull_request_contexts in self._pull_request_context_map.items():
            for context in pull_request_contexts:
                repository_id = context[PullRequestFields.REPOSITORY_ID]
                branch_name = context[Task.branch_name.key]
                key = (pull_request_id, repository_id, branch_name)
                if key in seen:
                    continue
                seen.add(key)
                contexts.append(
                    {
                        PullRequestFields.ID: pull_request_id,
                        PullRequestFields.REPOSITORY_ID: repository_id,
                        Task.branch_name.key: branch_name,
                    }
                )

        if self._state_data_access is None:
            return contexts

        try:
            persisted_contexts = self._state_data_access.list_pull_request_contexts()
        except Exception:
            self.logger.exception('failed to load tracked pull request contexts from state')
            return contexts

        for context in persisted_contexts:
            key = (
                context[PullRequestFields.ID],
                context[PullRequestFields.REPOSITORY_ID],
                context[Task.branch_name.key],
            )
            if key in seen:
                continue
            seen.add(key)
            contexts.append(context)
        return contexts

    def _is_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> bool:
        if self._state_data_access is None:
            return False
        return self._state_data_access.is_review_comment_processed(
            repository_id,
            pull_request_id,
            comment_id,
        )

    def _mark_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> None:
        if self._state_data_access is None:
            return
        try:
            self._state_data_access.mark_review_comment_processed(
                repository_id,
                pull_request_id,
                comment_id,
            )
        except Exception:
            self.logger.exception(
                'failed to persist processed review comment %s for pull request %s in repository %s',
                comment_id,
                pull_request_id,
                repository_id,
            )

    @staticmethod
    def _comment_context_entry(comment: ReviewComment) -> dict[str, str]:
        return {
            ReviewCommentFields.COMMENT_ID: str(comment.comment_id),
            ReviewCommentFields.AUTHOR: str(comment.author),
            ReviewCommentFields.BODY: str(comment.body),
        }

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
