from __future__ import annotations

import time
from typing import Callable, TypeVar

from core_lib.data_layers.service.service import Service

from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError,
    RepositoryService,
)
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.helpers.error_handling_utils import run_best_effort
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import MissionStepLoggerMixin
from kato_core_lib.helpers.pull_request_utils import (
    pull_request_description,
    pull_request_repositories_text,
    pull_request_summary_comment,
    pull_request_title,
)
from kato_core_lib.helpers.text_utils import text_from_mapping
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext, task_started_comment
from kato_core_lib.helpers.task_execution_utils import task_execution_report


# Sentinel returned by ``_create_pull_request_for_repository`` when a
# repo had nothing to publish. Distinct from a failure result so the
# caller can route the two cases separately.
_NO_CHANGES_SENTINEL: object = object()


class _PublishFailure:
    """Marker carrying the failure reason for a single repo's publish.

    Per-repo failures used to be reported as ``None`` and the reason
    was only logged — operators reading the summary comment in YouTrack
    saw "Failed repositories: foo" with no idea why. This class lets us
    carry the error text up to the comment-rendering layer so the user
    can act on it without spelunking through kato logs.
    """

    def __init__(self, repository_id: str, reason: str) -> None:
        self.repository_id = repository_id
        self.reason = reason


def _format_publish_failure(exc: Exception) -> str:
    """Compress an exception into a single line for the YouTrack comment.

    Kato comments are user-visible so we trim multi-line tracebacks and
    cap the length — full detail is in kato's logs anyway.
    """
    message = str(exc) or exc.__class__.__name__
    first_line = message.splitlines()[0].strip() if message else exc.__class__.__name__
    if len(first_line) > 280:
        first_line = first_line[:277] + '...'
    return first_line


class TaskPublisher(MissionStepLoggerMixin, Service):
    """Publish finished task work as pull requests, summary comments, and completion notifications."""

    DEFAULT_PUBLISH_MAX_RETRIES = 2

    @classmethod
    def max_retries_from_config(cls, open_cfg) -> int:
        """Read ``kato.task_publish.max_retries`` with a safe fallback.

        Lives on the publisher class (not ``kato_core_lib``) so the
        feature's own contract — what the env var means, what the
        default is — stays in this module. ``kato_core_lib`` only
        composes the dependency graph; it shouldn't know publish-side
        config keys directly.
        """
        task_publish_cfg = open_cfg.get('task_publish', {}) or {}
        try:
            return max(0, int(task_publish_cfg.get(
                'max_retries', cls.DEFAULT_PUBLISH_MAX_RETRIES,
            )))
        except (TypeError, ValueError):
            return cls.DEFAULT_PUBLISH_MAX_RETRIES

    def __init__(
        self,
        task_service: TaskService,
        task_state_service: TaskStateService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        state_registry: AgentStateRegistry,
        failure_handler: TaskFailureHandler,
        publish_max_retries: int | None = None,
        sleep_fn=time.sleep,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._state_registry = state_registry
        self._failure_handler = failure_handler
        # Retry budget for the publish-side calls (PR creation +
        # move-to-review). 2 retries → up to 3 attempts. Implementation
        # work is never re-run; this only catches transient git/Bitbucket/
        # YouTrack errors that would otherwise fail an already-done task.
        self._publish_max_retries = max(
            0,
            int(publish_max_retries if publish_max_retries is not None
                else self.DEFAULT_PUBLISH_MAX_RETRIES),
        )
        self._sleep_fn = sleep_fn
        self.logger = logger or configure_logger(self.__class__.__name__)

    def publish_task_execution(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict[str, str | bool],
    ) -> dict[str, object] | None:
        self._log_task_step(task.id, 'publishing pull requests')
        pull_requests, failed_repositories, unchanged_repositories = (
            self._create_pull_requests(task, prepared_task, execution)
        )
        if unchanged_repositories:
            self._log_task_step(
                task.id,
                'no changes published for repositories: %s',
                ', '.join(unchanged_repositories),
            )
        self._comment_pull_request_summary(
            task,
            pull_requests,
            failed_repositories,
            execution,
            unchanged_repositories=unchanged_repositories,
        )
        if failed_repositories:
            return self._partial_publish_result(
                task,
                prepared_task,
                pull_requests,
                failed_repositories,
            )
        if not pull_requests:
            # Every repo was unchanged — the agent ran but produced no commits.
            # Do NOT move the task to "In Review"; leave it in the current
            # state so a human can see that nothing was actually published.
            return self._no_changes_publish_result(task, prepared_task, unchanged_repositories)
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
    ) -> tuple[list[dict[str, str]], list[tuple[str, str]], list[str]]:
        pull_requests: list[dict[str, str]] = []
        # Each failed entry is ``(repository_id, error_reason)`` so the
        # summary comment posted back to the ticket can spell out why
        # each repo couldn't publish — operator can act on the message
        # without digging through logs.
        failed_repositories: list[tuple[str, str]] = []
        unchanged_repositories: list[str] = []
        description = pull_request_description(task, execution)
        agent_session_id = fix_session_id(execution.get(ImplementationFields.AGENT_SESSION_ID))
        commit_message = self._task_commit_message(task)
        for repository in prepared_task.repositories or []:
            outcome = self._create_pull_request_for_repository(
                task,
                prepared_task,
                repository,
                description,
                commit_message,
                agent_session_id,
            )
            if outcome is _NO_CHANGES_SENTINEL:
                # Repo was tagged for context (or the agent didn't end
                # up touching it). Not a publish failure — just no PR
                # to open. Listed in the summary so reviewers see it.
                unchanged_repositories.append(repository.id)
                continue
            if isinstance(outcome, _PublishFailure):
                failed_repositories.append(
                    (outcome.repository_id, outcome.reason),
                )
                continue
            if outcome is None:
                # Defensive: shouldn't happen post-refactor but keep a
                # safety net so a future regression doesn't silently
                # vanish a failure.
                failed_repositories.append(
                    (repository.id, 'unknown error (no reason captured)'),
                )
                continue
            pull_requests.append(outcome)

        return pull_requests, failed_repositories, unchanged_repositories

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
        agent_session_id: str,
    ):
        branch_name = prepared_task.repository_branches[repository.id]
        pull_request = self._create_repository_pull_request(
            task,
            repository,
            branch_name,
            description,
            commit_message,
        )
        if (
            pull_request is _NO_CHANGES_SENTINEL
            or pull_request is None
            or isinstance(pull_request, _PublishFailure)
        ):
            return pull_request
        # Surface the push + PR creation explicitly. Until this fires,
        # the operator can't see "the branch is now on the remote" in
        # the planning UI status feed.
        pull_request_url = text_from_mapping(pull_request, PullRequestFields.URL)
        pull_request_id = text_from_mapping(pull_request, PullRequestFields.ID)
        if pull_request_url:
            self._log_task_step(
                task.id,
                'pushed branch %s to %s and opened PR %s — %s',
                branch_name,
                repository.id,
                f'#{pull_request_id}' if pull_request_id else '(id unknown)',
                pull_request_url,
            )
        else:
            self._log_task_step(
                task.id,
                'pushed branch %s to %s and opened PR %s',
                branch_name,
                repository.id,
                f'#{pull_request_id}' if pull_request_id else '(id unknown)',
            )
        self._record_created_pull_request(
            task,
            repository,
            branch_name,
            agent_session_id,
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
    ):
        self._log_pull_request_creation(task.id, repository, branch_name)
        try:
            return self._run_publish_with_retry(
                lambda: self._repository_service.create_pull_request(
                    repository,
                    title=pull_request_title(task),
                    source_branch=branch_name,
                    description=description,
                    commit_message=commit_message,
                ),
                operation_label=(
                    f'pull request creation for repository {repository.id}'
                ),
                task_id=str(task.id),
            )
        except RepositoryHasNoChangesError:
            # The agent didn't change anything in this repo. Logged at
            # info level (not an exception trace) since this is the
            # expected outcome for repos tagged purely for context.
            self.logger.info(
                'repository %s has no changes to publish for task %s; '
                'skipping pull request',
                repository.id,
                task.id,
            )
            return _NO_CHANGES_SENTINEL
        except Exception as exc:
            self.logger.exception(
                'failed to create pull request for task %s in repository %s '
                '(after %d attempt%s)',
                task.id,
                repository.id,
                self._publish_max_retries + 1,
                '' if self._publish_max_retries == 0 else 's',
            )
            return _PublishFailure(repository.id, _format_publish_failure(exc))

    def _record_created_pull_request(
        self,
        task: Task,
        repository,
        branch_name: str,
        agent_session_id: str,
        pull_request: dict[str, str],
    ) -> None:
        self._state_registry.remember_pull_request_context(
            pull_request,
            branch_name,
            agent_session_id,
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
        *,
        unchanged_repositories: list[str] | None = None,
    ) -> None:
        if not pull_requests:
            return
        self._log_task_step(
            task.id,
            'adding review summary comment for %s',
            pull_request_repositories_text(pull_requests),
        )
        execution_report = task_execution_report(execution)
        if unchanged_repositories:
            execution_report = self._append_unchanged_repos_note(
                execution_report, unchanged_repositories,
            )
        self._comment_task_completed(
            task,
            pull_requests,
            failed_repositories,
            execution_report,
        )

    @staticmethod
    def _append_unchanged_repos_note(
        execution_report: str,
        unchanged_repositories: list[str],
    ) -> str:
        note = (
            'No changes were needed in: '
            + ', '.join(unchanged_repositories)
        )
        if not execution_report:
            return note
        return f'{execution_report}\n\n{note}'

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
        failed_repositories: list[tuple[str, str]],
    ) -> dict[str, object]:
        # Build a single-line failure summary that names each repo
        # AND the reason it failed; previously the operator only saw
        # "failed to create pull requests for repositories: foo, bar"
        # with no actionable detail.
        formatted_failures = [
            f'{repo_id} ({reason})' if reason else repo_id
            for repo_id, reason in failed_repositories
        ]
        self._failure_handler.handle_started_task_failure(
            task,
            RuntimeError(
                f'failed to create pull requests for repositories: '
                f'{", ".join(formatted_failures)}'
            ),
            prepared_task=prepared_task,
        )
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: StatusFields.PARTIAL_FAILURE,
            PullRequestFields.PULL_REQUESTS: pull_requests,
            PullRequestFields.FAILED_REPOSITORIES: [
                {
                    PullRequestFields.REPOSITORY_ID: repo_id,
                    'error': reason,
                }
                for repo_id, reason in failed_repositories
            ],
        }

    def _no_changes_publish_result(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        unchanged_repositories: list[str],
    ) -> dict[str, object]:
        repo_list = ', '.join(unchanged_repositories) if unchanged_repositories else 'all repositories'
        self._log_task_step(
            task.id,
            'agent produced no commits in %s — task left in current state',
            repo_list,
        )
        self._failure_handler.handle_started_task_failure(
            task,
            RuntimeError(
                f'agent produced no changes in {repo_list}; '
                f'nothing was pushed and no pull request was created'
            ),
            prepared_task=prepared_task,
        )
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: StatusFields.NO_CHANGES,
            PullRequestFields.PULL_REQUESTS: [],
            PullRequestFields.FAILED_REPOSITORIES: [],
        }

    def _complete_successful_publish(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        pull_requests: list[dict[str, str]],
    ) -> dict[str, object] | None:
        self._log_task_step(task.id, 'moving issue to review state')
        try:
            self._run_publish_with_retry(
                lambda: self._task_state_service.move_task_to_review(task.id),
                operation_label='move task to review',
                task_id=str(task.id),
            )
        except Exception as exc:
            self._failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return None
        self._log_task_step(task.id, 'moved issue to review state')
        # Record success before notification so a notification failure cannot
        # cause duplicate publish work on a later retry.
        self._state_registry.mark_task_processed(task.id, pull_requests)
        self._notify_task_ready_for_review(task, pull_requests)
        self._log_task_step(task.id, 'workflow completed successfully')
        _record_task_completed(task, prepared_task, pull_requests)
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

    _RetryReturn = TypeVar('_RetryReturn')

    def _run_publish_with_retry(
        self,
        operation: Callable[[], '_RetryReturn'],
        *,
        operation_label: str,
        task_id: str,
    ) -> '_RetryReturn':
        """Run a publish-side call with bounded retries + exponential backoff.

        Implementation work is never re-run here — the caller should
        only wrap network/API calls (push, PR creation, ticket-state
        transition). ``RepositoryHasNoChangesError`` is re-raised
        immediately because "no changes ahead" is a deterministic
        signal that retries can't fix.
        """
        max_attempts = self._publish_max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return operation()
            except RepositoryHasNoChangesError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise
                delay_seconds = min(2.0 ** (attempt - 1), 30.0)
                self._log_task_step(
                    task_id,
                    '%s failed (attempt %d/%d): %s — retrying in %.1fs',
                    operation_label,
                    attempt,
                    max_attempts,
                    exc,
                    delay_seconds,
                )
                self._sleep_fn(delay_seconds)
        # Defensive: the loop either returns or raises.
        assert last_exc is not None  # pragma: no cover
        raise last_exc  # pragma: no cover


def _record_task_completed(task, prepared_task, pull_requests) -> None:
    """Append a task_completed audit-log record. Best-effort.

    Audit failures must never bubble up — observability never blocks
    the publish path.
    """
    from kato_core_lib.helpers.audit_log_utils import (
        EVENT_TASK_COMPLETED,
        OUTCOME_SUCCESS,
        append_task_audit_event,
    )

    pr_urls = []
    for entry in pull_requests or []:
        if isinstance(entry, dict):
            url = entry.get(PullRequestFields.URL, '') or ''
            if url:
                pr_urls.append(str(url))
    append_task_audit_event(
        task,
        prepared_task,
        event=EVENT_TASK_COMPLETED,
        outcome=OUTCOME_SUCCESS,
        pr_url=', '.join(pr_urls),
    )
