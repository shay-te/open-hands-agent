from omegaconf import DictConfig

from core_lib.core_lib import CoreLib

from agent_core_lib.agent_core_lib import AgentCoreLib
from agent_core_lib.agent_core_lib.client.agent_client_factory import resolve_platform
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping
# ClaudeSessionManager is the only Claude-specific surface kato
# still touches directly — it owns the planning UI's chat-time
# streaming sessions, which has no equivalent in OpenHands and
# therefore intentionally lives outside ``AgentProvider``.
from claude_core_lib.claude_core_lib import ClaudeSessionManager
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.implementation_service import (
    ImplementationService,
)
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
)
from kato_core_lib.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato_core_lib.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.testing_service import TestingService
from kato_core_lib.data_layers.service.parallel_task_runner import ParallelTaskRunner
from kato_core_lib.data_layers.service.triage_service import (
    TriageService,
    build_claude_triage_investigator,
)
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from kato_core_lib.data_layers.service.workspace_manager import WorkspaceManager
from kato_core_lib.data_layers.service.workspace_provisioning_service import (
    provision_task_workspace_clones,
)
from kato_core_lib.data_layers.service.workspace_recovery_service import (
    WorkspaceRecoveryService,
)
from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
)
from kato_core_lib.data_layers.service.lessons_service import LessonsService
from claude_core_lib.claude_core_lib.helpers.one_shot_utils import make_claude_one_shot
from kato_core_lib.helpers.runtime_identity_utils import runtime_source_fingerprint
from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
    is_docker_mode_enabled,
    is_read_only_tools_enabled,
)
from kato_core_lib.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato_core_lib.validation.branch_push import TaskBranchPushValidator
from kato_core_lib.validation.model_access import TaskModelAccessValidator
from kato_core_lib.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato_core_lib.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.kato_config_utils import (
    resolved_agent_backend,
    skip_testing_enabled,
)
from task_core_lib.task_core_lib.platform import Platform
from task_core_lib.task_core_lib.task_core_lib import TaskCoreLib

logger = configure_logger('KatoCoreLib')
ISSUE_PLATFORM_CONFIG_NAMES = {
    Platform.YOUTRACK: 'youtrack',
    Platform.JIRA: 'jira',
    Platform.GITHUB: 'github_issues',
    Platform.GITHUB_ISSUES: 'github_issues',
    Platform.GITLAB: 'gitlab_issues',
    Platform.GITLAB_ISSUES: 'gitlab_issues',
    Platform.BITBUCKET: 'bitbucket_issues',
    Platform.BITBUCKET_ISSUES: 'bitbucket_issues',
}


class _EmailCoreLibProxy:
    def __call__(self, *args, **kwargs):
        from email_core_lib.email_core_lib import EmailCoreLib as _EmailCoreLib

        return _EmailCoreLib(*args, **kwargs)


EmailCoreLib = _EmailCoreLibProxy()


class KatoCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self.logger = configure_logger(cfg.core_lib.app.name)
        self._validate_runtime_source_fingerprint(cfg.kato)
        # Operator hooks must exist before sub-services are built so
        # the PlanningSessionRunner (and any other lifecycle caller)
        # can receive a real runner instead of None. The runner is
        # always installed — empty config produces a silent no-op.
        self.hooks_config, self.hook_runner = self._load_hooks(self.logger)
        self.service = self._build_agent_service(cfg.kato)
        # Wire the ``<KATO_TASK_DONE>`` sentinel callback after both
        # AgentService and SessionManager exist. Every Claude session
        # spawned from now on (planning chats, autonomous turns, the
        # boot-time resumes) carries the callback automatically; when
        # Claude prints the sentinel, kato runs the publish flow.
        if self.session_manager is not None:
            self.session_manager.set_done_callback(
                self.service.finish_task_planning_session,
            )
        self.service.validate_connections()

    @staticmethod
    def _load_hooks(logger):
        """Load ``~/.kato/hooks.json`` and build a HookRunner.

        Refuses startup on schema errors so config bugs surface at
        boot, not on the first hook fire. No file → empty config →
        no-op runner that returns ``[]`` for every fire().
        """
        from kato_core_lib.hooks.config import HookConfigError, load_hooks_config
        from kato_core_lib.hooks.runner import HookRunner
        try:
            config = load_hooks_config()
        except HookConfigError as exc:
            logger.error('hooks config rejected: %s', exc)
            raise
        runner = HookRunner(config, logger=logger)
        if not config.is_empty():
            points = sorted(p.value for p, hs in config.hooks_by_point.items() if hs)
            logger.info(
                'kato hooks loaded: %d point(s) configured (%s)',
                len(points), ', '.join(points),
            )
        return config, runner

    def _build_agent_service(self, open_cfg: DictConfig) -> AgentService:
        retry_cfg = open_cfg.retry
        agent_backend = resolved_agent_backend(open_cfg)
        # Read once at boot. Threaded through every Claude spawn point so
        # the sandbox-wrap decision is uniform across one-shot and
        # streaming paths and survives a rename without a sweep.
        docker_mode_on = is_docker_mode_enabled()
        # ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS`` — pre-approves the
        # hardcoded read-only Bash allowlist so the operator isn't
        # prompted for grep/cat/ls/etc. The startup gate has already
        # refused the flag when docker is off (see
        # ``validate_read_only_tools_requires_docker``); by the time
        # this reads, an enabled read-only flag implies docker is on
        # too. Threaded through the same fan-out as ``docker_mode_on``.
        read_only_tools_on = is_read_only_tools_enabled()
        self.session_manager = ClaudeSessionManager.from_config(
            open_cfg, agent_backend,
        )
        # Per-task workspace folders (one clone-set per ticket id) are
        # backend-agnostic. Both Claude and OpenHands flows use them for
        # isolation + parallelism.
        self.workspace_manager = WorkspaceManager.from_config(
            open_cfg, agent_backend,
        )
        # Lessons subsystem: per-task lesson capture + periodic compact.
        # The Claude clients re-read ``lessons_path`` on every spawn so
        # newly-extracted or freshly-compacted lessons take effect on
        # the next turn without restarting kato. ``LessonsService`` is
        # also handed to ``AgentService`` so the done-callback can
        # extract a lesson when the operator marks a task done.
        self.lessons_service = self._build_lessons_service(open_cfg)
        if self.session_manager is not None and self.workspace_manager is not None:
            self.session_manager.attach_workspace_manager(self.workspace_manager)
        # Worker pool sized to KATO_MAX_PARALLEL_TASKS. With max=1 the
        # behavior is identical to the previous synchronous loop —
        # submit-then-block — so single-task setups don't pay any cost.
        self.parallel_task_runner = ParallelTaskRunner(
            max_workers=self.workspace_manager.max_parallel_tasks,
        )
        planning_session_runner = PlanningSessionRunner.from_config(
            open_cfg, agent_backend, self.session_manager,
            docker_mode_on=docker_mode_on,
            hook_runner=self.hook_runner,
        )
        self.planning_session_runner = planning_session_runner
        self.logger.info('using agent backend: %s', agent_backend)
        issue_platform, ticket_cfg = self._resolve_ticket_platform_config(open_cfg)
        ticket_client = TaskCoreLib(
            issue_platform,
            ticket_cfg,
            retry_cfg.max_retries,
        ).issue
        implementation_service = ImplementationService(
            self._build_agent_client(
                open_cfg,
                retry_cfg.max_retries,
                docker_mode_on=docker_mode_on,
                read_only_tools_on=read_only_tools_on,
            )
        )
        testing_service = TestingService(
            self._build_agent_client(
                open_cfg,
                retry_cfg.max_retries,
                testing=True,
                docker_mode_on=docker_mode_on,
                read_only_tools_on=read_only_tools_on,
            )
        )
        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        task_service = TaskService(ticket_cfg, task_data_access)
        task_state_service = TaskStateService(ticket_cfg, task_data_access)
        repository_service = RepositoryService(open_cfg, retry_cfg.max_retries)
        notification_service = self._build_notification_service(open_cfg)
        state_registry = AgentStateRegistry()
        repository_connections_validator = RepositoryConnectionsValidator(repository_service)
        startup_validator = StartupDependencyValidator(
            repository_connections_validator,
            task_service,
            implementation_service,
            testing_service,
            skip_testing_enabled(open_cfg.openhands),
            agent_backend=agent_backend,
        )
        task_model_access_validator = TaskModelAccessValidator(
            implementation_service,
        )
        task_branch_push_validator = TaskBranchPushValidator(repository_service)
        task_branch_publishability_validator = TaskBranchPublishabilityValidator(
            repository_service
        )
        # Bind the workspace provisioner here so the preflight service
        # stays free of WorkspaceManager coupling. The lambda closes over
        # the manager + repository service; calling it on a task returns
        # repos with rewritten ``local_path`` pointing at the per-task
        # workspace clones.
        workspace_provisioner = (
            (lambda task, repos: provision_task_workspace_clones(
                self.workspace_manager, repository_service, task, repos,
            ))
            if self.workspace_manager is not None
            else None
        )
        # Pre-execution security scanner. Reads its config from the
        # ``security_scanner`` block; falls back to defaults when the
        # block is missing so existing operator deployments keep
        # working without yaml edits.
        security_scanner_service = self._build_security_scanner_service(open_cfg)
        # Restricted Execution Protocol gate. Default-on; opt-out via
        # ``KATO_RESTRICTED_EXECUTION_PROTOCOL_ENABLED=false``. The
        # service consults a per-operator JSON sidecar at
        # ``~/.kato/approved-repositories.json`` so first-time agent
        # runs against a previously-unseen repo require explicit
        # approval via the ``./kato approve-repo`` picker.
        repository_approval_service = self._build_repository_approval_service(open_cfg)
        # Posture supplier — captures the *current* global posture so
        # the REP gate can refuse RESTRICTED-mode repos when the
        # operator runs with weaker-than-required defaults. Read at
        # gate-time (not boot-time) so an operator who restarts kato
        # with safer settings doesn't have to also re-instantiate
        # services to pick up the change.
        claude_cfg_for_posture = getattr(open_cfg, 'claude', None)
        bypass_at_boot = bool(
            getattr(claude_cfg_for_posture, 'bypass_permissions', False)
            if claude_cfg_for_posture is not None
            else False
        )
        runtime_posture_supplier = self._build_runtime_posture_supplier(
            security_scanner_service=security_scanner_service,
            bypass_permissions=bypass_at_boot,
            docker_mode_on=docker_mode_on,
        )
        task_preflight_service = TaskPreflightService(
            task_model_access_validator=task_model_access_validator,
            task_service=task_service,
            repository_service=repository_service,
            task_branch_push_validator=task_branch_push_validator,
            task_branch_publishability_validator=task_branch_publishability_validator,
            workspace_provisioner=workspace_provisioner,
            security_scanner_service=security_scanner_service,
            repository_approval_service=repository_approval_service,
            runtime_posture_supplier=runtime_posture_supplier,
        )
        task_failure_handler = TaskFailureHandler(
            task_service=task_service,
            task_state_service=task_state_service,
            repository_service=repository_service,
            notification_service=notification_service,
        )
        task_publisher = TaskPublisher(
            task_service=task_service,
            task_state_service=task_state_service,
            repository_service=repository_service,
            notification_service=notification_service,
            state_registry=state_registry,
            failure_handler=task_failure_handler,
            publish_max_retries=TaskPublisher.max_retries_from_config(open_cfg),
        )
        review_comment_service = ReviewCommentService(
            task_service=task_service,
            implementation_service=implementation_service,
            repository_service=repository_service,
            state_registry=state_registry,
            planning_session_runner=planning_session_runner,
            # Always stream review-fixes through the planning UI when the
            # streaming runner is wired (Claude backend). The user's tag
            # decides what gets executed, not bypass mode.
            use_streaming_for_review_fixes=planning_session_runner is not None,
            # Per-task workspace clones isolate parallel review-fix
            # workers from each other's git state on the shared repo.
            workspace_manager=self.workspace_manager,
        )
        # Stash recovery so main.py can invoke it once after startup
        # validation — adopting orphan workspace folders is opt-in, runs
        # exactly once per process, and never blocks the scan loop.
        self.workspace_recovery_service = (
            WorkspaceRecoveryService(
                workspace_manager=self.workspace_manager,
                task_service=task_service,
                repository_service=repository_service,
            )
            if self.workspace_manager is not None
            else None
        )
        return AgentService(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=implementation_service,
            testing_service=testing_service,
            repository_service=repository_service,
            notification_service=notification_service,
            state_registry=state_registry,
            review_comment_service=review_comment_service,
            task_failure_handler=task_failure_handler,
            task_publisher=task_publisher,
            repository_connections_validator=repository_connections_validator,
            startup_validator=startup_validator,
            task_preflight_service=task_preflight_service,
            skip_testing=skip_testing_enabled(open_cfg.openhands),
            planning_session_runner=planning_session_runner,
            session_manager=self.session_manager,
            workspace_manager=self.workspace_manager,
            parallel_task_runner=self.parallel_task_runner,
            wait_planning_service=WaitPlanningService(
                session_manager=self.session_manager,
                repository_service=repository_service,
                task_state_service=task_state_service,
                workspace_manager=self.workspace_manager,
                planning_session_runner=planning_session_runner,
            ),
            triage_service=TriageService(
                task_service=task_service,
                # Hand the investigator only when we have a Claude
                # backend that can answer free-form prompts. For
                # OpenHands or no-backend setups, TriageService still
                # lives — it just posts an "unavailable" comment when
                # a triage tag arrives, instead of silently ignoring it.
                triage_investigator=build_claude_triage_investigator(
                    implementation_service,
                ),
            ),
            review_workspace_ttl_seconds=float(
                getattr(open_cfg, 'review_workspace_ttl_seconds', 3600)
            ),
            lessons_service=self.lessons_service,
        )


    def _build_security_scanner_service(self, open_cfg):
        """Construct the ``SecurityScannerService`` from operator config.

        Falls through to ``default_config()`` when no ``security_scanner``
        block is set — keeps deployments that haven't yet adopted the
        new yaml block working with sane defaults (all runners on,
        block on critical/high). When the block sets ``enabled: false``,
        we still construct a service (so the wiring stays uniform) but
        with no runners — calls to ``scan_workspace`` short-circuit.
        """
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import Severity
        from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
            RunnerConfig,
            SecurityScannerConfig,
            SecurityScannerService,
            default_config,
        )

        scanner_cfg = getattr(open_cfg, 'security_scanner', None)
        if scanner_cfg is None:
            # Operator hasn't opted in; default-on with the standard
            # block-on-critical/high threshold matches the security
            # posture documented in BYPASS_PROTECTIONS.md.
            return SecurityScannerService(default_config())
        enabled = bool(getattr(scanner_cfg, 'enabled', True))
        block_severities_raw = getattr(scanner_cfg, 'block_on_severity', None)
        if block_severities_raw is None:
            # Defensive fallback matches the YAML default. Critical-only
            # so the scanner doesn't refuse a task on routine
            # transitive-dep CVE noise. Operators tighten via YAML
            # when their codebase is clean enough that HIGH+ should
            # never appear.
            block_on_severity = (Severity.CRITICAL,)
        else:
            block_on_severity = tuple(
                Severity.from_string(str(s)) for s in block_severities_raw
            )
        # Honour per-runner enable flags. We rebuild the runner list
        # off the default set, dropping anything turned off.
        runner_toggles = getattr(scanner_cfg, 'runners', None)
        toggles = {}
        if runner_toggles is not None:
            for name in (
                'env_file', 'detect_secrets', 'bandit', 'safety', 'npm_audit',
            ):
                toggles[name.replace('_', '-')] = bool(
                    getattr(runner_toggles, name, True),
                )
        timeouts = getattr(scanner_cfg, 'timeouts', None)
        timeout_overrides = {}
        if timeouts is not None:
            for kato_name, runner_name in (
                ('secrets', 'detect-secrets'),
                ('dependencies', 'safety'),
                ('code_patterns', 'bandit'),
            ):
                value = getattr(timeouts, kato_name, None)
                if value is not None:
                    timeout_overrides[runner_name] = int(value)
            # ``dependencies`` covers both safety + npm-audit.
            if 'safety' in timeout_overrides:
                timeout_overrides.setdefault(
                    'npm-audit', timeout_overrides['safety'],
                )
        runners = []
        for runner in default_config().runners:
            if not toggles.get(runner.name, True):
                continue
            runners.append(RunnerConfig(
                name=runner.name,
                fn=runner.fn,
                timeout_seconds=timeout_overrides.get(
                    runner.name, runner.timeout_seconds,
                ),
                enabled=True,
            ))
        config = SecurityScannerConfig(
            enabled=enabled,
            block_on_severity=block_on_severity,
            runners=runners,
        )
        return SecurityScannerService(config)

    @staticmethod
    def _build_runtime_posture_supplier(
        *,
        security_scanner_service,
        bypass_permissions: bool,
        docker_mode_on: bool,
    ):
        """Return a ``() -> RuntimePosture`` callable for the REP gate.

        Snapshot of the boot-time posture knobs; the gate reads it
        every task so a kato restart with stricter env vars takes
        effect on the next scan tick.

        ``security_scanner_service`` may be ``None`` in legacy
        deployments — in that case the scanner-blocks-at-medium check
        is treated as failing (the operator opted out of the scanner,
        so we cannot guarantee MEDIUM-severity blocks).
        """
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import Severity
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RuntimePosture,
        )

        def supplier() -> RuntimePosture:
            scanner_blocks_at_medium = False
            if security_scanner_service is not None:
                config = getattr(security_scanner_service, '_config', None)
                if config is not None:
                    block_list = getattr(config, 'block_on_severity', ())
                    scanner_blocks_at_medium = Severity.MEDIUM in block_list
            return RuntimePosture(
                bypass_permissions=bool(bypass_permissions),
                docker_mode_on=bool(docker_mode_on),
                scanner_blocks_at_medium=bool(scanner_blocks_at_medium),
            )

        return supplier

    def _build_repository_approval_service(self, open_cfg):
        """Construct the ``RepositoryApprovalService`` for REP.

        REP is always on — there is no toggle. The only knob is
        ``storage_path``, sourced from yaml or
        ``KATO_APPROVED_REPOSITORIES_PATH`` (used by tests).
        """
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )

        rep_cfg = getattr(open_cfg, 'restricted_execution', None)
        storage_path = getattr(rep_cfg, 'storage_path', None) if rep_cfg is not None else None
        return RepositoryApprovalService(storage_path=storage_path or None)

    @staticmethod
    def _resolve_ticket_platform_config(
        open_cfg: DictConfig,
    ) -> tuple[Platform, DictConfig]:
        raw = str(open_cfg.issue_platform or 'youtrack').strip().lower()
        platform = Platform(raw)
        config_name = ISSUE_PLATFORM_CONFIG_NAMES.get(platform)
        ticket_cfg = getattr(open_cfg, config_name, None) if config_name else None
        if ticket_cfg is None:
            raise ValueError(f'missing issue platform config for: {platform.value}')
        return platform, ticket_cfg

    def _build_notification_service(self, open_cfg: DictConfig) -> NotificationService:
        return NotificationService(
            app_name=self.config.core_lib.app.name,
            email_core_lib=EmailCoreLib(self.config),
            failure_email_cfg=open_cfg.failure_email,
            completion_email_cfg=open_cfg.completion_email,
        )

    def _build_lessons_service(self, open_cfg: DictConfig) -> LessonsService:
        """Construct ``LessonsService`` and kick off a startup compact if due.

        Resolves the lessons file path: explicit ``KATO_LESSONS_PATH``
        wins, else defaults to ``~/.kato/lessons.md``. The state-dir
        for per-task pending files is the parent of that file.
        ``KATO_CLAUDE_BINARY`` and ``KATO_CLAUDE_MODEL`` thread into
        the one-shot LLM helper so extract / compact reuse the
        operator-configured Claude install.
        """
        from pathlib import Path

        claude_cfg = getattr(open_cfg, 'claude', None)
        configured = ''
        if claude_cfg is not None:
            configured = str(getattr(claude_cfg, 'lessons_path', '') or '').strip()
        lessons_path = Path(configured).expanduser() if configured else (
            Path.home() / '.kato' / 'lessons.md'
        )
        state_dir = lessons_path.parent
        data_access = LessonsDataAccess(state_dir)
        binary = ''
        model = ''
        if claude_cfg is not None:
            binary = str(getattr(claude_cfg, 'binary', '') or '').strip() or 'claude'
            model = str(getattr(claude_cfg, 'model', '') or '').strip()
        else:
            binary = 'claude'
        llm_one_shot = make_claude_one_shot(binary=binary, model=model)
        service = LessonsService(data_access, llm_one_shot)
        self._kick_startup_compact(service)
        return service

    @staticmethod
    def _kick_startup_compact(service: LessonsService) -> None:
        """Run a compact in the background if one is due.

        Non-blocking: kato boot finishes regardless. If the compact
        fails the previous lessons file is preserved (the service
        catches its own exceptions).
        """
        import threading

        if not service.should_compact():
            return

        def _run() -> None:
            try:
                service.compact()
            except Exception:
                # Service already logs; swallow so the worker thread
                # never crashes the orchestrator.
                pass

        worker = threading.Thread(
            target=_run,
            name='kato-lessons-startup-compact',
            daemon=True,
        )
        worker.start()

    def _validate_runtime_source_fingerprint(self, open_cfg: DictConfig) -> None:
        expected_source_fingerprint = text_from_mapping(open_cfg, 'source_fingerprint')
        if not expected_source_fingerprint:
            return

        current_source_fingerprint = runtime_source_fingerprint()
        if current_source_fingerprint == expected_source_fingerprint:
            return

        raise RuntimeError(
            'startup dependency validation failed: '
            'Kato source fingerprint mismatch: '
            f'expected {expected_source_fingerprint}, '
            f'got {current_source_fingerprint}; '
            'rebuild the Kato image before running'
        )

    @classmethod
    def _build_agent_client(
        cls,
        open_cfg: DictConfig,
        max_retries: int,
        *,
        testing: bool = False,
        docker_mode_on: bool = False,
        read_only_tools_on: bool = False,
    ):
        """Pick the configured backend through ``agent_core_lib``.

        Backend-selection logic + per-backend construction now
        lives in ``agent_core_lib`` (the same way ticket-platform
        selection lives in ``task_core_lib``). Kato hands the
        config + the runtime knobs to the factory and gets back
        an ``AgentProvider`` it can call by interface — no more
        if-claude-else-openhands branches in this file.
        """
        platform = resolve_platform(getattr(open_cfg, 'agent_backend', '') or '')
        return AgentCoreLib(
            platform,
            open_cfg,
            max_retries,
            testing=testing,
            docker_mode_on=docker_mode_on,
            read_only_tools_on=read_only_tools_on,
        ).agent
