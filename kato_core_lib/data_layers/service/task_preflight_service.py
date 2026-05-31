from __future__ import annotations

from collections.abc import Callable

from core_lib.data_layers.service.service import Service

from kato_core_lib.client.ticket_client_base import TicketClientBase
from kato_core_lib.data_layers.data.fields import TaskCommentFields
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_failure_handler import (
    repository_detection_comment,
)
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import (
    MissionStepLoggerMixin,
    log_mission_step,
)
from kato_core_lib.helpers.task_comment_utils import add_task_comment
from kato_core_lib.helpers.task_context_utils import (
    PreparedTaskContext,
    repository_branch_text,
    repository_destination_text,
    repository_ids_text,
    task_has_actionable_definition,
)
from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import repository_agents_instructions_text
from kato_core_lib.helpers.task_execution_utils import skip_task_result
from kato_core_lib.validation.branch_push import TaskBranchPushValidator
from kato_core_lib.validation.model_access import TaskModelAccessValidator
from kato_core_lib.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)


class TaskPreflightService(MissionStepLoggerMixin, Service):
    """Prepare a task for execution by validating access, repositories, and branch readiness."""
    def __init__(
        self,
        task_model_access_validator: TaskModelAccessValidator,
        task_service: TaskService,
        repository_service: RepositoryService,
        task_branch_push_validator: TaskBranchPushValidator,
        task_branch_publishability_validator: TaskBranchPublishabilityValidator,
        workspace_provisioner: Callable[[Task, list], list] | None = None,
        security_scanner_service=None,
        repository_approval_service=None,
        runtime_posture_supplier: Callable[[], object] | None = None,
        logger=None,
    ) -> None:
        self._task_model_access_validator = task_model_access_validator
        self._task_service = task_service
        self._repository_service = repository_service
        self._task_branch_push_validator = task_branch_push_validator
        self._task_branch_publishability_validator = task_branch_publishability_validator
        # Optional callable(task, repositories) -> repositories. When wired,
        # it clones per-task workspace copies and rewrites ``local_path``
        # so the rest of the preflight runs against isolated checkouts.
        # Plumbing-only here — agent_service owns the actual workspace
        # logic so this service stays free of WorkspaceManager coupling.
        self._workspace_provisioner = workspace_provisioner
        # Optional pre-execution security scanner. When wired, it runs
        # after workspace clones land on disk and before any prepare /
        # branch / push step. ``CRITICAL``/``HIGH`` findings raise
        # ``SecurityScanBlocked`` which the existing failure-handler
        # chain catches; ``MEDIUM``/``LOW`` findings are logged and
        # the task proceeds.
        self._security_scanner_service = security_scanner_service
        # Restricted Execution Protocol gate. Refuses tasks that
        # touch repos the operator hasn't explicitly approved. Runs
        # right after repository resolution so unapproved repos never
        # get cloned to the per-task workspace. There is no off
        # switch — REP is a required gate, not an optional one.
        self._repository_approval_service = repository_approval_service
        # Optional ``() -> RuntimePosture`` callable. When wired, the
        # REP gate also checks that RESTRICTED-mode approved repos
        # never run with a weaker-than-required posture (docker-off,
        # bypass-on, lenient scanner threshold). Called at gate time
        # so flipping env vars and restarting kato takes effect on
        # the next task without reinstantiating this service.
        self._runtime_posture_supplier = runtime_posture_supplier
        self._active_blocking_comment_log_state: dict[str, str] = {}
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
        blocking_comment = self._active_execution_blocking_comment(task)
        if blocking_comment and not self._can_retry_without_explicit_override(blocking_comment):
            return self._skip_blocked_task_result(task, blocking_comment)

        if blocking_comment:
            prepared_task = self._check_retry_preconditions(task, blocking_comment)
            if prepared_task is None or isinstance(prepared_task, dict):
                return prepared_task
            if not self._validate_task_model_access(
                task,
                task_failure_handler=task_failure_handler,
            ):
                return None
            self._clear_blocked_task_log_state(task.id)
            self._log_task_step(task.id, 'Kato model access validated')
            self._log_task_step(
                task.id,
                'starting mission: %s',
                str(task.summary or '').strip() or task.id,
            )
            return prepared_task

        if not self._validate_task_model_access(
            task,
            task_failure_handler=task_failure_handler,
        ):
            return None
        self._clear_blocked_task_log_state(task.id)
        self._log_task_step(task.id, 'Kato model access validated')
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

    def _validate_task_model_access(
        self,
        task: Task,
        *,
        task_failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> bool:
        try:
            self._task_model_access_validator.validate(task)
        except Exception as exc:
            self.logger.exception('failed to validate model access for task %s', task.id)
            if task_failure_handler is not None:
                task_failure_handler(task, exc, None)
            return False
        return True

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
        # Restricted Execution Protocol — refuse the task before any
        # workspace clone or agent spawn if the operator has not
        # explicitly approved every repo this task touches. Failure
        # handler posts the operator-facing comment + moves the
        # ticket back to Open via the existing chain.
        if not self._enforce_restricted_execution_protocol(
            task, repositories,
            failure_handler=repository_resolution_failure_handler,
        ):
            return None
        # Workspace mode: clone per-task isolated copies before any
        # ``prepare`` runs, so prepare/branch/push all operate on the
        # task's own checkout, not a shared local clone. No-op when
        # workspace_manager isn't wired (legacy single-clone setups).
        repositories = self._provision_workspace_clones(task, repositories)
        # Pre-execution security scan. Runs after workspace clones land
        # on disk (so files exist) and before any branch/prepare logic
        # (so blocking aborts cleanly). On block, raises
        # ``SecurityScanBlocked`` and the next failure handler in the
        # chain catches it; on warn, logs and proceeds.
        if not self._run_security_scan(
            task, repositories,
            failure_handler=repository_preparation_failure_handler,
        ):
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

    def _provision_workspace_clones(
        self,
        task: Task,
        repositories: list[object],
    ) -> list[object]:
        """Hand the resolved repos to the workspace provisioner if wired.

        The provisioner (set by agent_service) returns repos with
        ``local_path`` rewritten to point at this task's workspace
        clones. With NO provisioner wired (legacy single-clone setup),
        we pass the inventory list through unchanged.

        WITH a provisioner wired, we either return the workspace
        clones OR re-raise — never silently fall back. The previous
        fallback was a foot-gun: a transient git failure mid-clone
        would catch here, the agent would spawn on the operator's
        ``REPOSITORY_ROOT_PATH`` checkout, and edits would land on
        the live dev tree instead of the workspace clone. Hard-fail
        is the only safe default for a workspace-mode install.
        """
        if self._workspace_provisioner is None or not repositories:
            return repositories
        provisioned = self._workspace_provisioner(task, list(repositories))
        if not provisioned:
            raise RuntimeError(
                f'workspace provisioner returned no clones for task {task.id} '
                f'(expected {len(repositories)} repo(s)); refusing to spawn '
                f'an agent that would otherwise run against the inventory '
                f'checkout under REPOSITORY_ROOT_PATH'
            )
        provisioned = list(provisioned)
        if len(provisioned) < len(repositories):
            raise RuntimeError(
                f'workspace provisioner returned {len(provisioned)} clone(s) '
                f'for task {task.id} but {len(repositories)} were expected; '
                f'refusing to spawn an agent on a partial workspace'
            )
        return provisioned

    def _enforce_restricted_execution_protocol(
        self,
        task: Task,
        repositories: list[object],
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> bool:
        """Refuse the task if any repository lacks an approval record.

        Two-stage gate:

        1. Refuse if any repo isn't approved (``RestrictedExecutionRefusal``).
        2. If every repo IS approved, refuse if any RESTRICTED-mode
           approval is paired with a weaker-than-required global
           posture (``RestrictedModePostureViolation``). Operators
           opt out per-repo by elevating to TRUSTED after review.

        Returns ``True`` to proceed, ``False`` to abort. No-op when
        the approval service is not wired or REP is globally
        disabled.
        """
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RestrictedExecutionRefusal,
            RestrictedModePostureViolation,
            restricted_mode_posture_violations,
        )

        if self._repository_approval_service is None:
            return True
        if not repositories:
            return True
        unapproved = self._repository_approval_service.unapproved_repository_ids(repositories)
        if unapproved:
            self._log_task_step(
                task.id,
                'restricted execution protocol: refusing task — '
                'unapproved repository id(s): %s',
                ', '.join(unapproved),
            )
            exc = RestrictedExecutionRefusal(unapproved)
            if failure_handler is not None:
                failure_handler(task, exc, None)
            else:
                self.logger.error(
                    'restricted execution protocol refused task %s but no '
                    'failure handler is wired; aborting silently',
                    task.id,
                )
            return False

        # Posture gate. Skipped when no supplier is wired (tests /
        # legacy callers that don't care about runtime posture).
        if self._runtime_posture_supplier is None:
            return True
        restricted_ids = self._repository_approval_service.restricted_mode_repository_ids(
            repositories,
        )
        if not restricted_ids:
            return True
        try:
            posture = self._runtime_posture_supplier()
        except Exception:
            self.logger.exception(
                'failed to read runtime posture for task %s; '
                'allowing task to proceed (REP posture gate is '
                'fail-open by design — the approval gate above '
                'already refused unapproved repos)',
                task.id,
            )
            return True
        violations = restricted_mode_posture_violations(posture)
        if not violations:
            return True
        self._log_task_step(
            task.id,
            'restricted execution protocol: refusing task — '
            'restricted-mode repo(s) %s cannot run under current '
            'posture: %s',
            ', '.join(restricted_ids),
            '; '.join(violations),
        )
        exc = RestrictedModePostureViolation(restricted_ids, violations)
        if failure_handler is not None:
            failure_handler(task, exc, None)
        else:
            self.logger.error(
                'restricted execution protocol refused task %s on '
                'posture grounds but no failure handler is wired; '
                'aborting silently',
                task.id,
            )
        return False

    def _run_security_scan(
        self,
        task: Task,
        repositories: list[object],
        *,
        failure_handler: Callable[[Task, Exception, PreparedTaskContext | None], None] | None = None,
    ) -> bool:
        """Run the security scanner against every per-repo workspace clone.

        Returns ``True`` to proceed, ``False`` to abort. Aborts when
        the aggregated report is blocking (CRITICAL/HIGH at default
        threshold). MEDIUM/LOW findings produce a one-line log warning
        but proceed.

        The scanner is workspace-path-based — we run it once per repo
        clone and union the reports. Running it once over the whole
        ``~/.kato/workspaces/<task_id>/`` parent would also work, but
        per-repo gives us cleaner per-repo paths in the findings list,
        which matters when the operator scans the resulting ticket
        comment.

        No-op when no scanner is wired or no repositories were
        resolved (e.g. inventory-less single-clone setups).
        """
        from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
            SecurityScanBlocked,
        )

        if self._security_scanner_service is None:
            return True
        if not getattr(self._security_scanner_service, 'enabled', False):
            return True
        if not repositories:
            return True
        merged_findings: list = []
        merged_runner_errors: list = []
        block_threshold = None
        for repository in repositories:
            workspace_path = str(getattr(repository, 'local_path', '') or '').strip()
            if not workspace_path:
                continue
            try:
                report = self._security_scanner_service.scan_workspace(workspace_path)
            except Exception:
                self.logger.exception(
                    'security scan crashed for task %s repository %s; '
                    'allowing task to proceed (infrastructure flake, '
                    'not a security finding)',
                    task.id, getattr(repository, 'id', '?'),
                )
                continue
            merged_findings.extend(report.findings)
            merged_runner_errors.extend(report.runner_errors)
            block_threshold = report.block_threshold
        if block_threshold is None:
            return True
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import ScanReport
        aggregate = ScanReport(
            findings=tuple(merged_findings),
            blocking=any(
                f.severity.is_at_least(block_threshold) for f in merged_findings
            ),
            block_threshold=block_threshold,
            runner_errors=tuple(merged_runner_errors),
        )
        if not aggregate.blocking:
            if aggregate.findings:
                self._log_task_step(
                    task.id,
                    'security scan: %d non-blocking finding(s); proceeding',
                    len(aggregate.findings),
                )
            return True
        # Blocking: raise into the configured failure handler so the
        # ticket comment + repo-restore + move-to-Open all flow through
        # the existing error path.
        self._log_task_step(
            task.id,
            'security scan: blocking on %d critical/high finding(s)',
            sum(1 for f in aggregate.findings if f.severity.is_at_least(block_threshold)),
        )
        exc = SecurityScanBlocked(aggregate)
        if failure_handler is not None:
            failure_handler(task, exc, None)
        else:
            # No handler wired (tests / legacy callers) — just log.
            self.logger.error(
                'security scan blocked task %s but no failure handler '
                'is wired; aborting silently',
                task.id,
            )
        return False

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
        self._log_active_blocking_comment_once(task.id, blocking_comment)
        return skip_task_result(task.id)

    def _log_active_blocking_comment_once(self, task_id: str, blocking_comment: str) -> None:
        if self._active_blocking_comment_log_state.get(task_id) == blocking_comment:
            return
        self._active_blocking_comment_log_state[task_id] = blocking_comment
        self.logger.info(
            'skipping task %s because a prior Kato %s comment is still active: %s',
            task_id,
            self._blocking_comment_kind(blocking_comment),
            blocking_comment,
        )

    def _clear_blocked_task_log_state(self, task_id: str) -> None:
        self._active_blocking_comment_log_state.pop(task_id, None)

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
            repository_detection_comment(error),
            after_step='added repository detection skip comment',
            failure_log_message='failed to add repository detection comment for task %s',
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
            agents_instructions=repository_agents_instructions_text(list(repositories)),
        )

    def _add_task_comment(
        self,
        task_id: str,
        comment: str,
        *,
        after_step: str = '',
        failure_log_message: str,
    ) -> bool:
        return add_task_comment(
            self._task_service,
            self.logger,
            self._log_task_step,
            task_id,
            comment,
            after_step=after_step,
            failure_log_message=failure_log_message,
        )

    @staticmethod
    def _active_execution_blocking_comment(task: Task) -> str:
        comments = getattr(task, TaskCommentFields.ALL_COMMENTS, [])
        return TicketClientBase.active_execution_blocking_comment(comments)
