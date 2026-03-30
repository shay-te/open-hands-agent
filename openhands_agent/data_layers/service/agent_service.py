import traceback

from core_lib.data_layers.service.service import Service

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.error_handling import run_best_effort
from openhands_agent.logging_utils import configure_logger
from openhands_agent.client.retry_utils import is_retryable_exception
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
    TaskCommentFields,
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
        self.logger = configure_logger(self.__class__.__name__)
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
        self._processed_task_map: dict[str, dict[str, object]] = {}
        if self._state_data_access is None:
            self.logger.warning(
                'state_data_access is not configured; processed tasks and pull request comment '
                'state will not survive restarts'
            )

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        validations = [
            (
                self._task_data_access.provider_name,
                self._task_data_access.validate_connection,
                self._retry_count(getattr(self._task_data_access, '_client', None)),
            ),
            (
                'openhands',
                self._implementation_service.validate_connection,
                self._retry_count(getattr(self._implementation_service, '_client', None)),
            ),
            (
                'openhands_testing',
                self._testing_service.validate_connection,
                self._retry_count(getattr(self._testing_service, '_client', None)),
            ),
            ('repositories', self._repository_service.validate_connections, 1),
        ]
        if self._state_data_access is not None:
            validations.append(('state', self._state_data_access.validate, 1))
        summaries: list[str] = []
        details: list[str] = []

        for service_name, validate, max_retries in validations:
            try:
                validate()
                self.logger.info('validated %s connection', service_name)
            except Exception as exc:
                self.logger.exception('failed to validate %s connection', service_name)
                summaries.append(
                    self._validation_failure_summary(service_name, exc, max_retries)
                )
                details.append(
                    f'[{service_name}]\n{traceback.format_exc().rstrip()}'
                )

        if details:
            raise RuntimeError(
                'startup dependency validation failed:\n\n'
                + '\n'.join(f'- {summary}' for summary in summaries)
                + '\n\nDetails:\n\n'
                + '\n\n'.join(details)
            )

    @staticmethod
    def _validation_failure_summary(
        service_name: str,
        exc: Exception,
        max_retries: int,
    ) -> str:
        if is_retryable_exception(exc):
            return (
                f'unable to connect to {service_name} '
                f'(tried {max(1, max_retries)} times)'
            )
        return f'unable to validate {service_name}: {exc}'

    @staticmethod
    def _retry_count(client) -> int:
        try:
            return max(1, int(getattr(client, 'max_retries', 1)))
        except (TypeError, ValueError):
            return 1

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_data_access.get_assigned_tasks()

    def process_assigned_task(self, task: Task) -> dict | None:
        processed_result = self._processed_task_result(task.id)
        if processed_result is not None:
            return processed_result

        prepared_task = self._prepare_task_execution_context(task)
        if prepared_task is None or isinstance(prepared_task, dict):
            return prepared_task
        repositories, repository_branches = prepared_task

        if not self._start_task_processing(task):
            return None
        execution = self._run_task_implementation(task)
        if execution is None:
            return None
        testing_succeeded, testing_result = self._run_task_testing_validation(
            task,
            repositories,
            repository_branches,
            execution,
        )
        if not testing_succeeded:
            return testing_result
        return self._publish_task_execution(task, execution)

    def _processed_task_result(self, task_id: str) -> dict | None:
        if not self._is_task_processed(task_id):
            return None
        self.logger.info('skipping already processed task %s', task_id)
        return self._skip_task_result(
            task_id,
            self._processed_task_pull_requests(task_id),
        )

    def _prepare_task_execution_context(
        self,
        task: Task,
    ) -> tuple[list[object], dict[str, str]] | dict | None:
        blocking_comment = self._active_execution_blocking_comment(task)
        prepared_task = None
        if blocking_comment:
            prepared_task = self._prepare_blocked_task_execution_context(
                task,
                blocking_comment,
            )
            if prepared_task is None or isinstance(prepared_task, dict):
                return prepared_task

        self._log_task_step(task.id, 'starting mission: %s', str(task.summary or '').strip() or task.id)
        if prepared_task is not None:
            return prepared_task
        return self._prepare_task_start(task, report_failures=True)

    def _prepare_blocked_task_execution_context(
        self,
        task: Task,
        blocking_comment: str,
    ) -> tuple[list[object], dict[str, str]] | dict | None:
        if not self._can_retry_without_explicit_override(blocking_comment):
            self.logger.info(
                'skipping task %s because a prior OpenHands %s comment is still active: %s',
                task.id,
                self._blocking_comment_kind(blocking_comment),
                blocking_comment,
            )
            return self._skip_task_result(task.id)
        self._log_task_step(
            task.id,
            're-checking prior pre-start blocking comment before retry: %s',
            blocking_comment,
        )
        prepared_task = self._prepare_task_start(task, report_failures=False)
        if prepared_task is None:
            self.logger.info(
                'skipping task %s because a prior OpenHands %s comment is still active: %s',
                task.id,
                self._blocking_comment_kind(blocking_comment),
                blocking_comment,
            )
            return self._skip_task_result(task.id)
        self._log_task_step(
            task.id,
            'prior pre-start blocking comment no longer applies; retrying task',
        )
        return prepared_task

    def _start_task_processing(self, task: Task) -> bool:
        try:
            self._move_task_to_in_progress(task.id, strict=True)
        except Exception as exc:
            self._handle_task_failure(task, exc)
            return False
        self._comment_task_started(task)
        return True

    def _run_task_implementation(
        self,
        task: Task,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting implementation')
        try:
            execution = self._implementation_service.implement_task(task) or {}
        except Exception as exc:
            self.logger.exception('implementation request failed for task %s', task.id)
            self._handle_started_task_failure(task, exc)
            return None
        if not self._implementation_succeeded(execution):
            self._handle_implementation_failure(task, execution)
            return None
        self._log_task_step(
            task.id,
            'implementation completed successfully%s',
            self._session_suffix(execution),
        )
        return execution

    def _run_task_testing_validation(
        self,
        task: Task,
        repositories: list[object],
        repository_branches: dict[str, str],
        execution: dict[str, str | bool],
    ) -> tuple[bool, dict | None]:
        self._log_task_step(task.id, 're-validating task branches before testing')
        try:
            self._repository_service.prepare_task_branches(
                repositories,
                repository_branches,
            )
        except Exception as exc:
            self.logger.exception(
                'failed to prepare task branches for testing validation for task %s',
                task.id,
            )
            self._handle_started_task_failure(task, exc)
            return False, None
        self._log_task_step(task.id, 'task branches ready for testing')

        self._log_task_step(task.id, 'starting testing validation')
        try:
            testing = self._testing_service.test_task(task) or {}
        except Exception as exc:
            self.logger.exception('testing request failed for task %s', task.id)
            self._handle_started_task_failure(task, exc)
            return False, None
        if not self._testing_succeeded(testing):
            self._handle_testing_failure(task, testing)
            return False, {
                Task.id.key: task.id,
                StatusFields.STATUS: StatusFields.TESTING_FAILED,
                PullRequestFields.PULL_REQUESTS: [],
                PullRequestFields.FAILED_REPOSITORIES: [],
            }
        testing_commit_message = str(
            testing.get(ImplementationFields.COMMIT_MESSAGE, '') or ''
        ).strip()
        if testing_commit_message:
            execution[ImplementationFields.COMMIT_MESSAGE] = testing_commit_message
        self._log_task_step(task.id, 'testing validation passed')
        return True, None

    def _publish_task_execution(
        self,
        task: Task,
        execution: dict[str, str | bool],
    ) -> dict | None:
        self._log_task_step(task.id, 'publishing pull requests')
        pull_requests, failed_repositories = self._create_pull_requests(
            task,
            execution,
        )
        if pull_requests:
            self._log_task_step(
                task.id,
                'adding review summary comment for %s',
                self._pull_request_repositories_text(pull_requests),
            )
            if not self._comment_task_completed(
                task,
                pull_requests,
                failed_repositories,
            ):
                self._handle_started_task_failure(
                    task,
                    RuntimeError('failed to add completion comment'),
                )
                return None
        if failed_repositories:
            self._handle_started_task_failure(
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

        try:
            self._move_task_to_review(task.id, strict=True)
        except Exception as exc:
            self._handle_started_task_failure(task, exc)
            return None
        self._mark_task_processed(task.id, pull_requests)
        self._notify_task_ready_for_review(task, pull_requests)
        self._log_task_step(task.id, 'workflow completed successfully')
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: pull_requests,
            PullRequestFields.FAILED_REPOSITORIES: [],
        }

    def _prepare_task_start(
        self,
        task: Task,
        *,
        report_failures: bool,
    ) -> tuple[list[object], dict[str, str]] | None:
        try:
            repositories = self._repository_service.resolve_task_repositories(task)
        except Exception as exc:
            if report_failures:
                self.logger.exception('failed to resolve repositories for task %s', task.id)
                if self._is_repository_detection_failure(exc):
                    self._handle_repository_detection_failure(task, exc)
                else:
                    self._handle_task_failure(task, exc)
            else:
                self._log_task_step(
                    task.id,
                    'pre-start retry check is still blocked during repository resolution: %s',
                    exc,
                )
            return None
        self._log_task_step(
            task.id,
            'resolved repositories: %s',
            self._repository_ids_text(repositories),
        )
        try:
            repositories = self._repository_service.prepare_task_repositories(repositories)
        except Exception as exc:
            if report_failures:
                self.logger.exception('failed to prepare repositories for task %s', task.id)
                self._handle_task_failure(task, exc)
            else:
                self._log_task_step(
                    task.id,
                    'pre-start retry check is still blocked during repository preparation: %s',
                    exc,
                )
            return None
        self._log_task_step(
            task.id,
            'repository preflight passed: %s',
            self._repository_destination_text(repositories),
        )
        if not self._has_actionable_task_definition(task):
            if report_failures:
                self.logger.info(
                    'skipping task %s because the task definition is too thin to work from safely',
                    task.id,
                )
                self._handle_task_definition_failure(task)
            else:
                self._log_task_step(
                    task.id,
                    'pre-start retry check is still blocked because the task definition remains too thin',
                )
            return None

        repository_branches = self._attach_task_repository_context(task, repositories)
        self._log_task_step(
            task.id,
            'planned working branches: %s',
            self._repository_branch_text(repository_branches),
        )
        try:
            self._repository_service.prepare_task_branches(
                repositories,
                repository_branches,
            )
        except Exception as exc:
            if report_failures:
                self.logger.exception('failed to prepare task branches for task %s', task.id)
                self._handle_task_failure(task, exc)
            else:
                self._log_task_step(
                    task.id,
                    'pre-start retry check is still blocked during task-branch preparation: %s',
                    exc,
                )
            return None
        self._log_task_step(task.id, 'prepared task branches')
        return repositories, repository_branches

    def get_new_pull_request_comments(self) -> list[ReviewComment]:
        new_comments: list[ReviewComment] = []
        try:
            review_pull_request_keys = self._review_pull_request_keys()
        except Exception:
            self.logger.exception('failed to determine review-state pull requests to poll')
            return new_comments
        if not review_pull_request_keys:
            return new_comments

        for context in self._tracked_pull_request_contexts():
            repository_id = context[PullRequestFields.REPOSITORY_ID]
            pull_request_id = context[PullRequestFields.ID]
            if (pull_request_id, repository_id) not in review_pull_request_keys:
                continue
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

            comment_context = [
                self._comment_context_entry(comment)
                for comment in comments
            ]
            seen_resolution_targets: set[tuple[str, str]] = set()
            for comment in reversed(comments):
                setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
                setattr(comment, ReviewCommentFields.ALL_COMMENTS, list(comment_context))
                resolution_key = self._review_comment_resolution_key(comment)
                if resolution_key in seen_resolution_targets:
                    continue
                seen_resolution_targets.add(resolution_key)
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
        session_id = str(context.get(ImplementationFields.SESSION_ID, '') or '').strip()
        task_id = str(context.get(TaskFields.ID, '') or '').strip()
        task_summary = str(context.get(TaskFields.SUMMARY, '') or '').strip()
        setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
        repository = self._repository_service.get_repository(repository_id)
        self._repository_service.prepare_task_branches(
            [repository],
            {repository_id: branch_name},
        )

        execution = self._implementation_service.fix_review_comment(
            comment,
            branch_name,
            session_id,
            task_id=task_id,
            task_summary=task_summary,
        ) or {}
        if not execution.get(ImplementationFields.SUCCESS, False):
            raise RuntimeError(f'failed to address comment {comment.comment_id}')
        commit_message = str(
            execution.get(ImplementationFields.COMMIT_MESSAGE, '') or ''
        ).strip() or 'Address review comments'
        self.logger.info(
            'publishing review fix for pull request %s comment %s on branch %s',
            comment.pull_request_id,
            comment.comment_id,
            branch_name,
        )
        self._repository_service.publish_review_fix(
            repository,
            branch_name,
            commit_message,
        )
        self.logger.info(
            'published review fix for pull request %s comment %s',
            comment.pull_request_id,
            comment.comment_id,
        )
        self.logger.info(
            'resolving review comment %s on pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        self._repository_service.resolve_review_comment(repository, comment)
        self.logger.info(
            'resolved review comment %s on pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        self._mark_review_comment_processed(
            repository_id,
            comment.pull_request_id,
            comment.comment_id,
        )
        self._comment_review_fix_completed(
            comment,
            repository_id,
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
        session_id = str(execution.get(ImplementationFields.SESSION_ID, '') or '').strip()
        commit_message = str(
            execution.get(ImplementationFields.COMMIT_MESSAGE, '') or ''
        ).strip() or f'Implement {task.id}'

        for repository in getattr(task, 'repositories', []) or []:
            branch_name = task.repository_branches[repository.id]
            try:
                self._log_task_step(
                    task.id,
                    'creating pull request for repository %s from branch %s into %s',
                    repository.id,
                    branch_name,
                    getattr(repository, 'destination_branch', '') or 'the default branch',
                )
                pull_request = self._repository_service.create_pull_request(
                    repository,
                    title=f'{task.id}: {task.summary}',
                    source_branch=branch_name,
                    description=description,
                    commit_message=commit_message,
                )
                self._remember_pull_request_context(
                    pull_request,
                    branch_name,
                    session_id,
                    str(task.id or ''),
                    str(task.summary or ''),
                )
                pull_requests.append(pull_request)
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
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> None:
        pull_request_id = pull_request[PullRequestFields.ID]
        context = {
            PullRequestFields.REPOSITORY_ID: pull_request[PullRequestFields.REPOSITORY_ID],
            Task.branch_name.key: branch_name,
        }
        normalized_session_id = str(session_id or '').strip()
        normalized_task_id = str(task_id or '').strip()
        normalized_task_summary = str(task_summary or '').strip()
        if normalized_session_id:
            context[ImplementationFields.SESSION_ID] = normalized_session_id
        if normalized_task_id:
            context[TaskFields.ID] = normalized_task_id
        if normalized_task_summary:
            context[TaskFields.SUMMARY] = normalized_task_summary
        self._pull_request_context_map.setdefault(pull_request_id, []).append(context)
        if self._state_data_access is None:
            return
        try:
            self._state_data_access.remember_pull_request_context(
                pull_request_id,
                pull_request[PullRequestFields.REPOSITORY_ID],
                branch_name,
                normalized_session_id,
                normalized_task_id,
                normalized_task_summary,
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
        def notify_task_ready_for_review() -> None:
            self._log_task_step(
                task.id,
                'sending completion notification for %s',
                self._pull_request_repositories_text(pull_requests),
            )
            self._notification_service.notify_task_ready_for_review(task, pull_requests)
            self._log_task_step(task.id, 'completion notification sent')

        run_best_effort(
            notify_task_ready_for_review,
            logger=self.logger,
            failure_log_message='failed to send completion notification for task %s',
            failure_args=(task.id,),
        )

    def _handle_testing_failure(self, task: Task, testing: dict[str, str | bool]) -> None:
        summary = str(testing.get(Task.summary.key) or 'testing agent reported the task is not ready')
        self.logger.warning('testing failed for task %s: %s', task.id, summary)
        self._handle_started_task_failure(task, RuntimeError(summary))

    def _handle_implementation_failure(
        self,
        task: Task,
        execution: dict[str, str | bool],
    ) -> None:
        summary = str(
            execution.get(Task.summary.key) or 'implementation agent reported the task is not ready'
        )
        self.logger.warning('implementation failed for task %s: %s', task.id, summary)
        self._handle_started_task_failure(task, RuntimeError(summary))

    def _handle_task_failure(self, task: Task, error: Exception) -> None:
        self._report_task_failure(
            task,
            error,
            f'OpenHands agent could not safely process this task: {error}',
        )

    def _handle_started_task_failure(self, task: Task, error: Exception) -> None:
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

    def _comment_task_started(self, task: Task) -> None:
        self._log_task_step(task.id, 'adding started comment')
        self._add_task_comment(
            task.id,
            self._task_started_comment(task),
            after_step='added started comment',
            failure_log_message='failed to add started comment for task %s',
        )

    def _comment_task_completed(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
    ) -> bool:
        return self._add_task_comment(
            task.id,
            self._pull_request_summary_comment(task, pull_requests, failed_repositories),
            after_step='added review summary comment',
            failure_log_message='failed to add review summary comment for task %s',
        )

    def _comment_review_fix_completed(
        self,
        comment: ReviewComment,
        repository_id: str,
    ) -> None:
        task_id = self._task_id_for_pull_request(comment.pull_request_id, repository_id)
        if not task_id:
            return
        self._add_task_comment(
            task_id,
            self._review_comment_fixed_comment(comment),
            failure_log_message=(
                'failed to add review-fix comment for task %s after pull request '
                f'{comment.pull_request_id} comment {comment.comment_id}'
            ),
        )

    def _move_task_to_in_progress(self, task_id: str, strict: bool = False) -> bool:
        try:
            self._log_task_step(task_id, 'moving issue to in progress')
            self._task_data_access.move_task_to_in_progress(task_id)
            self._log_task_step(task_id, 'moved issue to in progress')
            return True
        except Exception:
            self.logger.exception('failed to move task %s to in progress', task_id)
            if strict:
                raise
            return False

    def _move_task_to_open(self, task_id: str) -> bool:
        try:
            self._log_task_step(task_id, 'moving issue back to open')
            self._task_data_access.move_task_to_open(task_id)
            self._log_task_step(task_id, 'moved issue back to open')
            return True
        except Exception:
            self.logger.exception('failed to move task %s back to open', task_id)
            return False

    def _move_task_to_review(self, task_id: str, strict: bool = False) -> bool:
        try:
            self._log_task_step(task_id, 'moving issue to review')
            self._task_data_access.move_task_to_review(task_id)
            self._log_task_step(task_id, 'moved issue to review')
            return True
        except Exception:
            self.logger.exception('failed to move task %s to review', task_id)
            if strict:
                raise
            return False

    @staticmethod
    def _task_started_comment(task: Task) -> str:
        repositories = getattr(task, 'repositories', []) or []
        repository_ids = [
            str(repository.id).strip()
            for repository in repositories
            if str(repository.id).strip()
        ]
        if not repository_ids:
            return 'OpenHands agent started working on this task.'
        if len(repository_ids) == 1:
            return (
                'OpenHands agent started working on this task in repository '
                f'{repository_ids[0]}.'
            )
        return (
            'OpenHands agent started working on this task in repositories: '
            f'{", ".join(repository_ids)}.'
        )

    @staticmethod
    def _is_repository_detection_failure(error: Exception) -> bool:
        return isinstance(error, ValueError) and 'no configured repository matched task' in str(error)

    @staticmethod
    def _has_actionable_task_definition(task: Task) -> bool:
        description = str(task.description or '').strip()
        if description and description.lower() != 'no description provided.':
            return True
        summary = str(task.summary or '').strip()
        return len(summary) >= 24 or len(summary.split()) >= 4

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        self.logger.info(f'Mission %s: {message}', task_id, *args)

    @staticmethod
    def _repository_ids_text(repositories: list[object]) -> str:
        repository_ids = [
            str(getattr(repository, 'id', '') or '').strip()
            for repository in repositories
            if str(getattr(repository, 'id', '') or '').strip()
        ]
        return ', '.join(repository_ids) if repository_ids else '<none>'

    @staticmethod
    def _repository_destination_text(repositories: list[object]) -> str:
        entries = []
        for repository in repositories:
            repository_id = str(getattr(repository, 'id', '') or '').strip()
            destination_branch = str(getattr(repository, 'destination_branch', '') or '').strip()
            if not repository_id:
                continue
            entries.append(f'{repository_id}->{destination_branch or "default"}')
        return ', '.join(entries) if entries else '<none>'

    @staticmethod
    def _repository_branch_text(repository_branches: dict[str, str]) -> str:
        if not repository_branches:
            return '<none>'
        return ', '.join(
            f'{repository_id}->{branch_name}'
            for repository_id, branch_name in repository_branches.items()
        )

    @staticmethod
    def _pull_request_repositories_text(pull_requests) -> str:
        if not isinstance(pull_requests, list):
            return '<none>'
        repository_ids = [
            str(pull_request.get(PullRequestFields.REPOSITORY_ID, '') or '').strip()
            for pull_request in pull_requests
            if isinstance(pull_request, dict)
        ]
        repository_ids = [repository_id for repository_id in repository_ids if repository_id]
        return ', '.join(repository_ids) if repository_ids else '<none>'

    @staticmethod
    def _session_suffix(payload: dict[str, str | bool]) -> str:
        session_id = str(payload.get(ImplementationFields.SESSION_ID, '') or '').strip()
        return f' (session {session_id})' if session_id else ''

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

    @staticmethod
    def _blocking_comment_kind(blocking_comment: str) -> str:
        if TicketClientBase.is_completion_comment(blocking_comment):
            return 'completion'
        return 'failure'

    @staticmethod
    def _can_retry_without_explicit_override(blocking_comment: str) -> bool:
        return TicketClientBase.is_pre_start_blocking_comment(blocking_comment)

    def _attach_task_repository_context(
        self,
        task: Task,
        repositories: list[object],
    ) -> dict[str, str]:
        repository_branches = {
            repository.id: self._repository_service.build_branch_name(task, repository)
            for repository in repositories
        }
        task.branch_name = next(iter(repository_branches.values()), '')
        setattr(task, 'repositories', repositories)
        setattr(task, 'repository_branches', repository_branches)
        return repository_branches

    def _add_task_comment(
        self,
        task_id: str,
        comment: str,
        *,
        after_step: str = '',
        failure_log_message: str,
    ) -> bool:
        try:
            self._task_data_access.add_comment(task_id, comment)
            if after_step:
                self._log_task_step(task_id, after_step)
            return True
        except Exception:
            self.logger.exception(failure_log_message, task_id)
            return False

    def _is_task_processed(self, task_id: str) -> bool:
        if str(task_id) in self._processed_task_map:
            return True
        if self._state_data_access is None:
            return False
        if not hasattr(self._state_data_access, 'is_task_processed'):
            return False
        return self._state_data_access.is_task_processed(task_id)

    @staticmethod
    def _active_execution_blocking_comment(task: Task) -> str:
        comments = getattr(task, TaskCommentFields.ALL_COMMENTS, [])
        return TicketClientBase.active_execution_blocking_comment(comments)

    def _processed_task_pull_requests(self, task_id: str) -> list[dict[str, str]]:
        if str(task_id) in self._processed_task_map:
            in_memory_task = self._processed_task_map[str(task_id)]
            pull_requests = in_memory_task.get(PullRequestFields.PULL_REQUESTS, [])
            if isinstance(pull_requests, list):
                return pull_requests
        if self._state_data_access is None:
            return []
        if not hasattr(self._state_data_access, 'get_processed_task'):
            return []
        processed_task = self._state_data_access.get_processed_task(task_id)
        pull_requests = processed_task.get(PullRequestFields.PULL_REQUESTS, [])
        return pull_requests if isinstance(pull_requests, list) else []

    def _mark_task_processed(self, task_id: str, pull_requests: list[dict[str, str]]) -> None:
        self._processed_task_map[str(task_id)] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                dict(pull_request)
                for pull_request in pull_requests
                if isinstance(pull_request, dict)
            ],
        }
        if self._state_data_access is None:
            return
        try:
            self._state_data_access.mark_task_processed(task_id, pull_requests)
        except Exception:
            self.logger.exception('failed to persist processed task state for task %s', task_id)

    def _review_pull_request_keys(self) -> set[tuple[str, str]]:
        review_pull_request_keys: set[tuple[str, str]] = set()
        for task in self._task_data_access.get_review_tasks():
            for pull_request in self._processed_task_pull_requests(task.id):
                if not isinstance(pull_request, dict):
                    continue
                pull_request_id = str(pull_request.get(PullRequestFields.ID, '') or '').strip()
                repository_id = str(
                    pull_request.get(PullRequestFields.REPOSITORY_ID, '') or ''
                ).strip()
                if pull_request_id and repository_id:
                    review_pull_request_keys.add((pull_request_id, repository_id))
        return review_pull_request_keys

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
    def _review_comment_resolution_key(comment: ReviewComment) -> tuple[str, str]:
        resolution_target_type = str(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, '') or 'comment'
        ).strip() or 'comment'
        resolution_target_id = str(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '') or comment.comment_id or ''
        ).strip()
        return resolution_target_type, resolution_target_id

    @staticmethod
    def _pull_request_summary_comment(
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
    ) -> str:
        lines = [f'OpenHands completed task {task.id}: {task.summary}.']
        if pull_requests:
            lines.append('')
            lines.append('Published review links:')
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

    def _task_id_for_pull_request(
        self,
        pull_request_id: str,
        repository_id: str,
    ) -> str:
        try:
            for task in self._task_data_access.get_review_tasks():
                for pull_request in self._processed_task_pull_requests(task.id):
                    if not isinstance(pull_request, dict):
                        continue
                    tracked_pull_request_id = str(
                        pull_request.get(PullRequestFields.ID, '') or ''
                    ).strip()
                    tracked_repository_id = str(
                        pull_request.get(PullRequestFields.REPOSITORY_ID, '') or ''
                    ).strip()
                    if (
                        tracked_pull_request_id == str(pull_request_id).strip()
                        and tracked_repository_id == str(repository_id).strip()
                    ):
                        return str(task.id)
        except Exception:
            self.logger.exception(
                'failed to look up task for pull request %s in repository %s',
                pull_request_id,
                repository_id,
            )
        return ''

    @staticmethod
    def _review_comment_fixed_comment(comment: ReviewComment) -> str:
        return (
            'OpenHands addressed review comment '
            f'{comment.comment_id} on pull request {comment.pull_request_id}.'
        )
