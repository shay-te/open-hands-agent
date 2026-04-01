import traceback
from collections.abc import Callable

from core_lib.data_layers.service.service import Service

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.error_handling import run_best_effort
from openhands_agent.logging_utils import configure_logger
from openhands_agent.pull_request_context import build_pull_request_context
from openhands_agent.client.retry_utils import is_retryable_exception
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
    TaskCommentFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.data_layers.service.agent_service_utils import (
    PreparedTaskContext,
    ReviewFixContext,
    comment_context_entry,
    pull_request_repositories_text,
    pull_request_summary_comment,
    repository_branch_text,
    repository_destination_text,
    repository_ids_text,
    review_fix_context_from_mapping,
    review_fix_result,
    review_comment_fixed_comment,
    review_comment_resolution_key,
    session_suffix,
    task_has_actionable_definition,
    task_started_comment,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.data_layers.service.testing_service import TestingService
from openhands_agent.text_utils import text_from_attr, text_from_mapping


class AgentService(Service):
    def __init__(
        self,
        task_service: TaskService,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
    ) -> None:
        self.logger = configure_logger(self.__class__.__name__)
        if testing_service is None:
            raise ValueError('testing_service is required')
        if notification_service is None:
            raise ValueError('notification_service is required')
        self._task_service = task_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._pull_request_context_map: dict[str, list[dict[str, str]]] = {}
        self._processed_task_map: dict[str, dict[str, object]] = {}
        self._processed_review_comment_map: dict[tuple[str, str], set[str]] = {}

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        try:
            self._repository_service.validate_connections()
            self.logger.info('validated repositories connection')
        except Exception as exc:
            self.logger.error('failed to validate repositories connection: %s', exc)
            raise RuntimeError(str(exc)) from None

        summaries: list[str] = []
        details: list[str] = []
        for service_name, validate, max_retries in self._connection_validations():
            self._collect_validation_result(
                service_name,
                validate,
                max_retries,
                summaries,
                details,
            )

        if details:
            raise RuntimeError(
                'startup dependency validation failed:\n\n'
                + '\n'.join(f'- {summary}' for summary in summaries)
                + '\n\nDetails:\n\n'
                + '\n\n'.join(details)
            )

    def _connection_validations(self) -> list[tuple[str, Callable[[], None], int]]:
        validations = [
            (
                self._task_service.provider_name,
                self._task_service.validate_connection,
                self._task_service.max_retries,
            ),
            (
                'openhands',
                self._implementation_service.validate_connection,
                self._implementation_service.max_retries,
            ),
            (
                'openhands_testing',
                self._testing_service.validate_connection,
                self._testing_service.max_retries,
            ),
        ]
        return validations

    def _collect_validation_result(
        self,
        service_name: str,
        validate: Callable[[], None],
        max_retries: int,
        summaries: list[str],
        details: list[str],
    ) -> None:
        try:
            validate()
            self.logger.info('validated %s connection', service_name)
        except Exception as exc:
            if service_name == 'repositories':
                self.logger.error(
                    'failed to validate %s connection: %s',
                    service_name,
                    exc,
                )
                summaries.append(
                    self._validation_failure_summary(service_name, exc, max_retries)
                )
                details.append(f'[{service_name}] {exc}')
                return

            self.logger.exception('failed to validate %s connection', service_name)
            summaries.append(
                self._validation_failure_summary(service_name, exc, max_retries)
            )
            details.append(f'[{service_name}]\n{traceback.format_exc().rstrip()}')

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

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_service.get_assigned_tasks()

    def process_assigned_task(self, task: Task) -> dict | None:
        processed_result = self._processed_task_result(task.id)
        if processed_result is not None:
            return processed_result

        prepared_task = self._prepare_task_execution_context(task)
        if prepared_task is None or isinstance(prepared_task, dict):
            return prepared_task

        if not self._start_task_processing(task):
            return None
        execution = self._run_task_implementation(task)
        if execution is None:
            return None
        testing_succeeded, testing_result = self._run_task_testing_validation(
            task,
            prepared_task,
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
    ) -> PreparedTaskContext | dict | None:
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
    ) -> PreparedTaskContext | dict | None:
        if not self._can_retry_without_explicit_override(blocking_comment):
            return self._skip_blocked_task_result(task, blocking_comment)
        self._log_task_step(
            task.id,
            're-checking prior pre-start blocking comment before retry: %s',
            blocking_comment,
        )
        prepared_task = self._prepare_task_start(task, report_failures=False)
        if prepared_task is None:
            return self._skip_blocked_task_result(task, blocking_comment)
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
            session_suffix(execution),
        )
        return execution

    def _run_task_testing_validation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict[str, str | bool],
    ) -> tuple[bool, dict | None]:
        if not self._prepare_task_branches_for_testing(task, prepared_task):
            return False, None
        testing = self._request_testing_validation(task)
        if testing is None:
            return False, None
        if not self._testing_succeeded(testing):
            self._handle_testing_failure(task, testing)
            return False, self._testing_failed_result(task.id)
        self._apply_testing_commit_message(execution, testing)
        self._log_task_step(task.id, 'testing validation passed')
        return True, None

    def _prepare_task_branches_for_testing(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
    ) -> bool:
        self._log_task_step(task.id, 're-validating task branches before testing')
        try:
            self._repository_service.prepare_task_branches(
                prepared_task.repositories,
                prepared_task.repository_branches,
            )
        except Exception as exc:
            self.logger.exception(
                'failed to prepare task branches for testing validation for task %s',
                task.id,
            )
            self._handle_started_task_failure(task, exc)
            return False
        self._log_task_step(task.id, 'task branches ready for testing')
        return True

    def _request_testing_validation(
        self,
        task: Task,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting testing validation')
        try:
            return self._testing_service.test_task(task) or {}
        except Exception as exc:
            self.logger.exception('testing request failed for task %s', task.id)
            self._handle_started_task_failure(task, exc)
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
    def _apply_testing_commit_message(
        execution: dict[str, str | bool],
        testing: dict[str, str | bool],
    ) -> None:
        testing_commit_message = str(
            testing.get(ImplementationFields.COMMIT_MESSAGE, '') or ''
        ).strip()
        if testing_commit_message:
            execution[ImplementationFields.COMMIT_MESSAGE] = testing_commit_message
        testing_message = str(
            testing.get(ImplementationFields.MESSAGE, '') or ''
        ).strip()
        if testing_message:
            execution[ImplementationFields.MESSAGE] = testing_message

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
        if not self._comment_pull_request_summary(
            task,
            pull_requests,
            failed_repositories,
            execution,
        ):
            return None
        if failed_repositories:
            return self._partial_publish_result(task, pull_requests, failed_repositories)
        return self._complete_successful_publish(task, pull_requests)

    def _comment_pull_request_summary(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
        execution: dict[str, str | bool],
    ) -> bool:
        if not pull_requests:
            return True
        self._log_task_step(
            task.id,
            'adding review summary comment for %s',
            pull_request_repositories_text(pull_requests),
        )
        validation_report = ''
        if not failed_repositories:
            validation_report = self._task_validation_report(execution)
        if self._comment_task_completed(
            task,
            pull_requests,
            failed_repositories,
            validation_report,
        ):
            return True
        self._handle_started_task_failure(
            task,
            RuntimeError('failed to add completion comment'),
        )
        return False

    def _partial_publish_result(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
    ) -> dict[str, object]:
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

    def _complete_successful_publish(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
    ) -> dict[str, object] | None:
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
    ) -> PreparedTaskContext | None:
        repositories = self._resolve_task_repositories(task, report_failures=report_failures)
        if repositories is None:
            return None
        repositories = self._prepare_task_repositories_for_start(
            task,
            repositories,
            report_failures=report_failures,
        )
        if repositories is None:
            return None
        if not self._task_definition_ready(task, report_failures=report_failures):
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
            report_failures=report_failures,
        ):
            return None
        self._log_task_step(task.id, 'prepared task branches')
        return prepared_task

    def _resolve_task_repositories(
        self,
        task: Task,
        *,
        report_failures: bool,
    ) -> list[object] | None:
        repositories = self._run_pre_start_step(
            task,
            self._repository_service.resolve_task_repositories,
            task,
            report_failures=report_failures,
            failure_log_message='failed to resolve repositories for task %s',
            blocked_log_message=(
                'pre-start retry check is still blocked during repository resolution: %s'
            ),
            failure_handler=self._handle_repository_resolution_failure,
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
        report_failures: bool,
    ) -> list[object] | None:
        repositories = self._run_pre_start_step(
            task,
            self._repository_service.prepare_task_repositories,
            repositories,
            report_failures=report_failures,
            failure_log_message='failed to prepare repositories for task %s',
            blocked_log_message=(
                'pre-start retry check is still blocked during repository preparation: %s'
            ),
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
        report_failures: bool,
    ) -> bool:
        if task_has_actionable_definition(task):
            return True
        self._handle_pre_start_task_definition_failure(
            task,
            report_failures=report_failures,
        )
        return False

    def _prepare_task_execution_branches(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        *,
        report_failures: bool,
    ) -> bool:
        prepared_branches = self._run_pre_start_step(
            task,
            self._repository_service.prepare_task_branches,
            prepared_task.repositories,
            prepared_task.repository_branches,
            report_failures=report_failures,
            failure_log_message='failed to prepare task branches for task %s',
            blocked_log_message=(
                'pre-start retry check is still blocked during task-branch preparation: %s'
            ),
        )
        return prepared_branches is not None

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
            new_comments.extend(
                self._new_pull_request_comments_for_context(
                    context,
                    review_pull_request_keys,
                )
            )

        return new_comments

    def _new_pull_request_comments_for_context(
        self,
        context: dict[str, str],
        review_pull_request_keys: set[tuple[str, str]],
    ) -> list[ReviewComment]:
        repository_id = context[PullRequestFields.REPOSITORY_ID]
        pull_request_id = context[PullRequestFields.ID]
        if (pull_request_id, repository_id) not in review_pull_request_keys:
            return []
        comments = self._pull_request_comments(repository_id, pull_request_id)
        if not comments:
            return []
        comment_context = [comment_context_entry(comment) for comment in comments]
        return self._unprocessed_review_comments(
            comments,
            repository_id,
            pull_request_id,
            comment_context,
        )

    def _pull_request_comments(
        self,
        repository_id: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        try:
            repository = self._repository_service.get_repository(repository_id)
            return self._repository_service.list_pull_request_comments(
                repository,
                pull_request_id,
            )
        except Exception:
            self.logger.exception(
                'failed to fetch pull request comments for repository %s pull request %s',
                repository_id,
                pull_request_id,
            )
            return []

    def _unprocessed_review_comments(
        self,
        comments: list[ReviewComment],
        repository_id: str,
        pull_request_id: str,
        comment_context: list[dict[str, str]],
    ) -> list[ReviewComment]:
        new_comments: list[ReviewComment] = []
        seen_resolution_targets: set[tuple[str, str]] = set()
        for comment in reversed(comments):
            setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
            setattr(comment, ReviewCommentFields.ALL_COMMENTS, list(comment_context))
            resolution_key = review_comment_resolution_key(comment)
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
        review_context = self._review_fix_context(comment)
        repository = self._repository_service.get_repository(review_context.repository_id)
        self._prepare_review_fix_branch(repository, review_context)
        execution = self._run_review_comment_fix(comment, review_context)
        self._publish_review_comment_fix(comment, repository, review_context, execution)
        self._complete_review_fix(comment, review_context)
        return review_fix_result(comment, review_context)

    def _review_fix_context(self, comment: ReviewComment) -> ReviewFixContext:
        repository_id = text_from_attr(comment, PullRequestFields.REPOSITORY_ID)
        context = self._pull_request_context(comment.pull_request_id, repository_id)
        if context is None:
            raise ValueError(f'unknown pull request id: {comment.pull_request_id}')
        review_context = review_fix_context_from_mapping(context)
        setattr(comment, PullRequestFields.REPOSITORY_ID, review_context.repository_id)
        return review_context

    def _prepare_review_fix_branch(
        self,
        repository,
        review_context: ReviewFixContext,
    ) -> None:
        self._repository_service.prepare_task_branches(
            [repository],
            {review_context.repository_id: review_context.branch_name},
        )

    def _run_review_comment_fix(
        self,
        comment: ReviewComment,
        review_context: ReviewFixContext,
    ) -> dict[str, str | bool]:
        execution = self._implementation_service.fix_review_comment(
            comment,
            review_context.branch_name,
            review_context.session_id,
            task_id=review_context.task_id,
            task_summary=review_context.task_summary,
        ) or {}
        if not execution.get(ImplementationFields.SUCCESS, False):
            raise RuntimeError(f'failed to address comment {comment.comment_id}')
        return execution

    def _publish_review_comment_fix(
        self,
        comment: ReviewComment,
        repository,
        review_context: ReviewFixContext,
        execution: dict[str, str | bool],
    ) -> None:
        self.logger.info(
            'publishing review fix for pull request %s comment %s on branch %s',
            comment.pull_request_id,
            comment.comment_id,
            review_context.branch_name,
        )
        self._repository_service.publish_review_fix(
            repository,
            review_context.branch_name,
            self._review_fix_commit_message(execution),
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

    def _complete_review_fix(
        self,
        comment: ReviewComment,
        review_context: ReviewFixContext,
    ) -> None:
        self._mark_review_comment_processed(
            review_context.repository_id,
            comment.pull_request_id,
            comment.comment_id,
        )
        self._comment_review_fix_completed(
            comment,
            review_context.repository_id,
        )

    @staticmethod
    def _review_fix_commit_message(execution: dict[str, str | bool]) -> str:
        return (
            str(execution.get(ImplementationFields.COMMIT_MESSAGE, '') or '').strip()
            or 'Address review comments'
        )

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
        session_id = text_from_mapping(execution, ImplementationFields.SESSION_ID)
        commit_message = self._task_commit_message(task, execution)
        for repository in getattr(task, 'repositories', []) or []:
            pull_request = self._create_pull_request_for_repository(
                task,
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
        execution: dict[str, str | bool],
    ) -> str:
        return (
            str(execution.get(ImplementationFields.COMMIT_MESSAGE, '') or '').strip()
            or f'Implement {task.id}'
        )

    @staticmethod
    def _task_validation_report(execution: dict[str, str | bool]) -> str:
        return str(execution.get(ImplementationFields.MESSAGE, '') or '').strip()

    def _create_pull_request_for_repository(
        self,
        task: Task,
        repository,
        description: str,
        commit_message: str,
        session_id: str,
    ) -> dict[str, str] | None:
        branch_name = task.repository_branches[repository.id]
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
        self._remember_pull_request_context(
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

    def _remember_pull_request_context(
        self,
        pull_request: dict[str, str],
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> None:
        pull_request_id = pull_request[PullRequestFields.ID]
        context = build_pull_request_context(
            pull_request[PullRequestFields.REPOSITORY_ID],
            branch_name,
            session_id,
            task_id,
            task_summary,
        )
        self._pull_request_context_map.setdefault(pull_request_id, []).append(context)

    def _pull_request_context(
        self,
        pull_request_id: str,
        repository_id: str = '',
    ) -> dict[str, str] | None:
        pull_request_contexts = self._pull_request_context_map.get(pull_request_id, [])
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

    def _handle_testing_failure(self, task: Task, testing: dict[str, str | bool]) -> None:
        self._handle_unsuccessful_agent_result(
            task,
            testing,
            default_summary='testing agent reported the task is not ready',
            warning_log_message='testing failed for task %s: %s',
        )

    def _handle_implementation_failure(
        self,
        task: Task,
        execution: dict[str, str | bool],
    ) -> None:
        self._handle_unsuccessful_agent_result(
            task,
            execution,
            default_summary='implementation agent reported the task is not ready',
            warning_log_message='implementation failed for task %s: %s',
        )

    def _handle_task_failure(self, task: Task, error: Exception) -> None:
        self._restore_task_repositories(task)
        self._report_task_failure(
            task,
            error,
            f'OpenHands agent could not safely process this task: {error}',
        )

    def _handle_started_task_failure(self, task: Task, error: Exception) -> None:
        self._restore_task_repositories(task)
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

    def _restore_task_repositories(self, task: Task) -> None:
        repositories = getattr(task, 'repositories', []) or []
        if not repositories:
            return
        self._log_task_step(task.id, 'restoring repository branches after task rejection')
        try:
            self._repository_service.restore_task_repositories(repositories)
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

    def _comment_task_started(self, task: Task) -> None:
        self._log_task_step(task.id, 'adding started comment')
        self._add_task_comment(
            task.id,
            task_started_comment(task),
            after_step='added started comment',
            failure_log_message='failed to add started comment for task %s',
        )

    def _comment_task_completed(
        self,
        task: Task,
        pull_requests: list[dict[str, str]],
        failed_repositories: list[str],
        validation_report: str = '',
    ) -> bool:
        return self._add_task_comment(
            task.id,
            pull_request_summary_comment(
                task,
                pull_requests,
                failed_repositories,
                validation_report,
            ),
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
            review_comment_fixed_comment(comment),
            failure_log_message=(
                'failed to add review-fix comment for task %s after pull request '
                f'{comment.pull_request_id} comment {comment.comment_id}'
            ),
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
        self.logger.info(f'Mission %s: {message}', task_id, *args)

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
        return self._skip_task_result(task.id)

    def _run_pre_start_step(
        self,
        task: Task,
        step: Callable,
        *step_args,
        report_failures: bool,
        failure_log_message: str,
        blocked_log_message: str,
        failure_handler: Callable[[Task, Exception], None] | None = None,
    ):
        try:
            return step(*step_args)
        except Exception as exc:
            self._handle_pre_start_exception(
                task,
                exc,
                report_failures=report_failures,
                failure_log_message=failure_log_message,
                blocked_log_message=blocked_log_message,
                failure_handler=failure_handler,
            )
            return None

    def _handle_pre_start_exception(
        self,
        task: Task,
        error: Exception,
        *,
        report_failures: bool,
        failure_log_message: str,
        blocked_log_message: str,
        failure_handler: Callable[[Task, Exception], None] | None = None,
    ) -> None:
        if report_failures:
            self.logger.exception(failure_log_message, task.id)
            handler = failure_handler or self._handle_task_failure
            handler(task, error)
            return
        self._log_task_step(task.id, blocked_log_message, error)

    def _handle_repository_resolution_failure(self, task: Task, error: Exception) -> None:
        if self._is_repository_detection_failure(error):
            self._handle_repository_detection_failure(task, error)
            return
        self._handle_task_failure(task, error)

    def _handle_pre_start_task_definition_failure(
        self,
        task: Task,
        *,
        report_failures: bool,
    ) -> None:
        self._restore_task_repositories(task)
        if report_failures:
            self.logger.info(
                'skipping task %s because the task definition is too thin to work from safely',
                task.id,
            )
            self._handle_task_definition_failure(task)
            return
        self._log_task_step(
            task.id,
            'pre-start retry check is still blocked because the task definition remains too thin',
        )

    def _handle_unsuccessful_agent_result(
        self,
        task: Task,
        payload: dict[str, str | bool],
        *,
        default_summary: str,
        warning_log_message: str,
    ) -> None:
        summary = str(payload.get(Task.summary.key) or default_summary)
        self.logger.warning(warning_log_message, task.id, summary)
        self._handle_started_task_failure(task, RuntimeError(summary))

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

    def _attach_task_repository_context(
        self,
        task: Task,
        repositories: list[object],
    ) -> PreparedTaskContext:
        repository_branches = {
            repository.id: self._repository_service.build_branch_name(task, repository)
            for repository in repositories
        }
        task.branch_name = next(iter(repository_branches.values()), '')
        setattr(task, 'repositories', repositories)
        setattr(task, 'repository_branches', repository_branches)
        return PreparedTaskContext(
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

    def _is_task_processed(self, task_id: str) -> bool:
        return str(task_id) in self._processed_task_map

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
        return []

    def _mark_task_processed(self, task_id: str, pull_requests: list[dict[str, str]]) -> None:
        self._processed_task_map[str(task_id)] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                dict(pull_request)
                for pull_request in pull_requests
                if isinstance(pull_request, dict)
            ],
        }

    def _review_pull_request_keys(self) -> set[tuple[str, str]]:
        review_pull_request_keys: set[tuple[str, str]] = set()
        for task in self._task_service.get_review_tasks():
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

    def _tracked_pull_request_contexts(self) -> list[dict[str, str]]:
        contexts: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        self._append_tracked_contexts(
            contexts,
            seen,
            self._in_memory_tracked_pull_request_contexts(),
        )
        return contexts

    def _in_memory_tracked_pull_request_contexts(self) -> list[dict[str, str]]:
        contexts: list[dict[str, str]] = []
        for pull_request_id, pull_request_contexts in self._pull_request_context_map.items():
            for context in pull_request_contexts:
                contexts.append(
                    {
                        PullRequestFields.ID: pull_request_id,
                        PullRequestFields.REPOSITORY_ID: context[PullRequestFields.REPOSITORY_ID],
                        Task.branch_name.key: context[Task.branch_name.key],
                    }
                )
        return contexts

    @staticmethod
    def _append_tracked_contexts(
        contexts: list[dict[str, str]],
        seen: set[tuple[str, str, str]],
        candidates: list[dict[str, str]],
    ) -> None:
        for context in candidates:
            key = (
                context[PullRequestFields.ID],
                context[PullRequestFields.REPOSITORY_ID],
                context[Task.branch_name.key],
            )
            if key in seen:
                continue
            seen.add(key)
            contexts.append(context)

    def _is_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> bool:
        key = (str(repository_id), str(pull_request_id))
        return str(comment_id) in self._processed_review_comment_map.get(key, set())

    def _mark_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> None:
        key = (str(repository_id), str(pull_request_id))
        self._processed_review_comment_map.setdefault(key, set()).add(str(comment_id))

    def _task_id_for_pull_request(
        self,
        pull_request_id: str,
        repository_id: str,
    ) -> str:
        try:
            for task in self._task_service.get_review_tasks():
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
