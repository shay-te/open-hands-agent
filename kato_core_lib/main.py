from __future__ import annotations

import os
import signal
import threading
import time

import hydra
from omegaconf import DictConfig

from kato_core_lib.helpers import agent_prompt_utils
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.shell_status_utils import (
    sleep_with_countdown_spinner,
    supports_inline_status,
    sleep_with_warmup_countdown,
)
from kato_core_lib.helpers.status_broadcaster_utils import (
    StatusBroadcaster,
    install_status_broadcast_handler,
)
from sandbox_core_lib.sandbox_core_lib.tls_pin import (
    TlsPinError,
    validate_anthropic_tls_pin_or_refuse,
)
from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    read_session_id_from,
)
from kato_core_lib.validate_env import validate_environment
from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
    BypassPermissionsRefused,
    print_security_posture,
    validate_bypass_permissions,
    validate_read_only_tools_requires_docker,
)


_STATUS_BROADCASTER = StatusBroadcaster()
install_status_broadcast_handler(_STATUS_BROADCASTER)

# Shared between the scan loop thread and the Flask webserver thread.
# _FORCE_SCAN_EVENT: set by POST /api/scan/trigger to wake the idle loop early.
# _SCAN_IN_PROGRESS: set while the scan job is running so the endpoint can
# report the current state without doing anything.
_FORCE_SCAN_EVENT: threading.Event = threading.Event()
_SCAN_IN_PROGRESS: threading.Event = threading.Event()


class _ProcessAssignedTasksJobProxy:
    def __call__(self):
        from kato_core_lib.jobs.process_assigned_tasks import ProcessAssignedTasksJob as _ProcessAssignedTasksJob

        return _ProcessAssignedTasksJob()


class _KatoInstanceProxy:
    @staticmethod
    def init(core_lib_cfg: DictConfig) -> None:
        from kato_core_lib.kato_instance import KatoInstance as _KatoInstance

        _KatoInstance.init(core_lib_cfg)

    @staticmethod
    def get():
        from kato_core_lib.kato_instance import KatoInstance as _KatoInstance

        return _KatoInstance.get()


ProcessAssignedTasksJob = _ProcessAssignedTasksJobProxy()
KatoInstance = _KatoInstanceProxy()


@hydra.main(
    version_base=None,
    config_path='config',
    config_name='kato_core_lib',
)
def main(cfg: DictConfig) -> int:
    logger = configure_logger(cfg.core_lib.app.name)
    try:
        validate_environment(mode='all')
    except ValueError as exc:
        logger.error('%s', exc)
        return 1
    try:
        validate_bypass_permissions()
    except BypassPermissionsRefused as exc:
        logger.error('%s', exc)
        return 1
    # Read-only-tools pre-approval requires the sandbox boundary.
    # ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` without
    # ``KATO_CLAUDE_DOCKER=true`` would let pre-approved ``grep``/
    # ``cat``/``find`` run on the host with the operator's full
    # file-system access — defeating the purpose of pre-approving
    # only "safe" tools. Refused at startup the same way bypass
    # without docker is.
    try:
        validate_read_only_tools_requires_docker()
    except BypassPermissionsRefused as exc:
        logger.error('%s', exc)
        return 1
    # OG4 — TLS pin validator for api.anthropic.com. Strict-by-default
    # when ``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256`` is set; opt-out
    # via ``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true``. A network failure at
    # boot does NOT promote to refusal (operator may be offline /
    # in a build env without connectivity); only a successful
    # connection with a pin mismatch fails-closed. See
    # ``sandbox_core_lib.sandbox_core_lib.tls_pin``.
    try:
        validate_anthropic_tls_pin_or_refuse(logger=logger)
    except TlsPinError as exc:
        logger.error('%s', exc)
        return 1
    # Docker mode wraps every Claude spawn in the hardened sandbox
    # (workspace bind-mount only, default-DROP egress firewall,
    # capability drop, read-only rootfs, audit log). When the operator
    # opts into it via ``KATO_CLAUDE_DOCKER=true``, the Docker daemon
    # MUST be available — falling back to host execution silently
    # would defeat the point of the flag. The same is true for
    # ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` (which requires docker
    # by the constraint enforced in ``validate_bypass_permissions``);
    # by the time this gate runs, bypass-without-docker has already
    # been refused, so checking ``is_docker_mode_enabled()`` alone
    # is sufficient.
    from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import is_docker_mode_enabled
    if is_docker_mode_enabled():
        from sandbox_core_lib.sandbox_core_lib.manager import (
            check_docker_or_exit,
            check_gvisor_or_exit,
            docker_running_rootless,
            gvisor_runtime_available,
        )
        check_docker_or_exit()
        # gVisor is required by default for any docker-mode spawn —
        # refuses to start without it unless the operator explicitly
        # accepts the residual via KATO_SANDBOX_ALLOW_NO_GVISOR=true.
        # The check applies regardless of bypass; docker-only mode
        # gets the same kernel-CVE-isolation floor as the original
        # bypass mode.
        check_gvisor_or_exit()
        if gvisor_runtime_available():
            logger.info(
                'sandbox: gVisor (runsc) runtime detected — using it '
                'for syscall-level isolation on top of namespaces',
            )
        else:
            logger.warning(
                'sandbox: starting WITHOUT gVisor (operator override '
                'KATO_SANDBOX_ALLOW_NO_GVISOR=true). Container relies '
                'on the host kernel for isolation; a kernel CVE could '
                'be used to escape. Other 8 sandbox layers still apply.',
            )
        if not docker_running_rootless():
            logger.info(
                'sandbox: Docker daemon is running in rooted mode. For '
                'stricter isolation (a container escape stays in your '
                'user account, not full root on the host) consider '
                'rootless Docker: https://docs.docker.com/engine/security/rootless/',
            )
    print_security_posture()
    try:
        KatoInstance.init(cfg)
    except RuntimeError as exc:
        if str(exc).startswith('startup dependency validation failed:') or str(exc).startswith('[Error] '):
            logger.error('%s', exc)
            return 1
        raise
    app = KatoInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('Starting kato agent')
    _load_hooks_or_refuse(app, logger)
    _recover_orphan_workspaces(app)
    _reconcile_workspace_branches(app)
    _reset_stuck_workspace_statuses(app)
    _requeue_stuck_comments(app)
    _log_known_session_ids(app)
    _cleanup_done_tasks_at_boot(app)
    # Sessions are lazy after restart: opening a tab replays the disk
    # JSONL via SSE so the conversation history is visible immediately.
    # Claude is re-spawned (with ``--resume <id>``) only when the operator
    # actually sends a follow-up message — same UX as VS Code Claude Code.
    # The old autoresume sent ``_RESUME_CONTINUE_PROMPT`` to every active
    # workspace at boot, which burned tokens and made the chat look like
    # Claude was starting over.
    _start_planning_webserver_if_enabled(app)
    _start_pending_comment_work_after_ui(app)
    _start_resume_prompt_watcher(app)
    _register_shutdown_hook(app)
    startup_delay_seconds, scan_interval_seconds = _task_scan_settings(cfg)
    _warm_up_repository_inventory(app)
    _run_task_scan_loop(
        app,
        startup_delay_seconds=startup_delay_seconds,
        scan_interval_seconds=scan_interval_seconds,
        force_scan_event=_FORCE_SCAN_EVENT,
    )
    return 0


def _reconcile_workspace_branches(app) -> None:
    """Walk every workspace and align each clone to its task branch.

    Per-task workspace clones are *supposed* to live on the branch
    named after the task (kato's convention: ``branch_name == task_id``).
    A previous kato session can leave them on ``master`` if it crashed
    mid-publish, or if a manual recovery left the clone in a weird
    state. This runs once at boot, idempotent and best-effort —
    workspaces that are dirty / can't be cleanly checked out are
    skipped with a warning so kato still boots.
    """
    workspace_manager = getattr(app, 'workspace_manager', None)
    if workspace_manager is None:
        return
    try:
        from kato_webserver.git_diff_utils import (
            current_branch,
            ensure_branch_checked_out,
        )
    except ImportError:
        # Webserver not on the path — kato can run headless without it.
        return
    try:
        records = workspace_manager.list_workspaces()
    except Exception:
        app.logger.exception('failed to list workspaces during branch reconcile')
        return
    realigned = 0
    skipped: list[str] = []
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        for repository_id in (getattr(record, 'repository_ids', []) or []):
            try:
                clone_path = workspace_manager.repository_path(
                    task_id, str(repository_id),
                )
            except Exception:
                continue
            if not clone_path.is_dir() or not (clone_path / '.git').is_dir():
                continue
            cwd = str(clone_path)
            on = current_branch(cwd)
            if on == task_id:
                continue
            if ensure_branch_checked_out(cwd, task_id):
                app.logger.info(
                    'workspace %s/%s realigned: %s -> %s',
                    task_id, repository_id, on or '<unknown>', task_id,
                )
                realigned += 1
            else:
                skipped.append(f'{task_id}/{repository_id} (on {on or "<unknown>"})')
    if realigned:
        app.logger.info(
            'realigned %d workspace clone(s) to their task branch',
            realigned,
        )
    if skipped:
        app.logger.warning(
            'could not realign %d workspace clone(s) to task branch '
            '(dirty tree or missing branch): %s',
            len(skipped), ', '.join(skipped[:10]),
        )


def _cleanup_done_tasks_at_boot(app) -> None:
    """Prune tabs for tickets already done/closed — once, at startup.

    Cleanup otherwise only runs on a scan tick (inside
    ``get_new_pull_request_comments``), so a restart would render a
    tab for any task whose ``~/.kato/sessions/<id>.json`` is still on
    disk even though the ticket moved to done — it'd only vanish
    ~30s later on the first scan. Running the prune here, BEFORE the
    planning webserver starts serving the tab list, means a restart
    never resurrects a done task even briefly. Connections were
    already validated at boot, so the ticket-platform call this
    needs is safe. Best-effort: any failure is logged and boot
    continues (the scan-loop cleanup is still the backstop).
    """
    service = getattr(app, 'service', None)
    cleanup = getattr(service, 'cleanup_done_tasks', None)
    if not callable(cleanup):
        return
    try:
        cleanup()
    except Exception:
        app.logger.exception(
            'boot-time done-task cleanup failed; the scan loop will '
            'retry it on the next tick',
        )


def _reset_stuck_workspace_statuses(app) -> None:
    """Promote PROVISIONING workspaces that have valid git repos to ACTIVE.

    A kato crash during provisioning leaves the workspace status stuck at
    PROVISIONING even though all or some repos may already be cloned. The
    on-demand chat-respawn path needs an ACTIVE workspace to attach to;
    promoting the ones that have at least one valid .git clone gives them
    a correct ACTIVE label so the lazy session spawn (on the operator's
    first follow-up message) lands in a workspace that's actually ready.

    Workspaces in ERRORED state are flagged with a visible warning so the
    operator knows they need attention (re-run the task or discard the
    workspace). Best-effort: any per-workspace failure is logged and skipped.
    """
    workspace_manager = getattr(app, 'workspace_manager', None)
    if workspace_manager is None:
        return
    try:
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
            WORKSPACE_STATUS_ERRORED,
            WORKSPACE_STATUS_PROVISIONING,
        )
    except ImportError:
        return
    try:
        records = workspace_manager.list_workspaces()
    except Exception:
        app.logger.exception('failed to list workspaces during status reset')
        return
    promoted = 0
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        status = str(getattr(record, 'status', '') or '')
        if status == WORKSPACE_STATUS_ERRORED:
            app.logger.warning(
                'workspace %s is in errored state from a previous run — '
                'operator may need to re-run the task or discard the workspace',
                task_id,
            )
            continue
        if status != WORKSPACE_STATUS_PROVISIONING:
            continue
        has_valid_repo = _provisioning_workspace_has_git_repo(
            workspace_manager, task_id, record,
        )
        if has_valid_repo:
            try:
                workspace_manager.update_status(task_id, WORKSPACE_STATUS_ACTIVE)
                app.logger.info(
                    'workspace %s promoted from provisioning to active '
                    '(repos were cloned before the previous kato process stopped)',
                    task_id,
                )
                promoted += 1
            except Exception:
                app.logger.exception(
                    'failed to promote workspace %s from provisioning to active',
                    task_id,
                )
        else:
            app.logger.warning(
                'workspace %s is stuck in provisioning state with no valid '
                'git repos — the previous clone was incomplete. '
                'Re-run the task to provision it correctly.',
                task_id,
            )
    if promoted:
        app.logger.info(
            'promoted %d workspace(s) from provisioning to active at boot',
            promoted,
        )


def _provisioning_workspace_has_git_repo(
    workspace_manager, task_id: str, record,
) -> bool:
    """True when at least one repo clone under the workspace has a ``.git`` dir."""
    for repo_id in (getattr(record, 'repository_ids', []) or []):
        try:
            repo_path = workspace_manager.repository_path(task_id, str(repo_id))
        except Exception:
            continue
        if repo_path.is_dir() and (repo_path / '.git').exists():
            return True
    return False


_RESUME_CONTINUE_PROMPT = (
    "kato has been restarted while this task workspace was still active. "
    "Resume the interrupted task now. First inspect the existing worktree "
    "and conversation context so you do not duplicate or overwrite work, "
    "then continue from the last safe point. If the task is already complete, "
    "say so and end with the normal Kato completion token."
)

_RESUME_WAIT_PROMPT = (
    "kato has been restarted. This is a system notice — no user "
    "action requested. Please reply with one short line "
    "acknowledging you're ready to continue, then wait for the "
    "operator's next message."
)


def _resume_streaming_sessions(app) -> None:
    """Re-spawn Claude sessions for every active workspace at boot.

    Without this, restarting kato leaves every previously-open chat
    tab in a "Claude: sleeping" state until the operator types into
    it. We walk the workspace registry and call ``start_session`` for
    each ``active`` workspace; the session manager's existing
    resume-id plumbing reuses the saved ``agent_session_id`` so the
    chat picks up where it left off. A short system-notice prompt is
    sent so the Claude CLI doesn't exit on empty stdin (it requires
    at least one message at startup).

    Best-effort: any per-task failure is logged and skipped — the tab
    falls back to the existing "operator types to wake it" path.
    """
    session_manager = getattr(app, 'session_manager', None)
    workspace_manager = getattr(app, 'workspace_manager', None)
    runner = getattr(app, 'planning_session_runner', None)
    if session_manager is None or workspace_manager is None:
        return
    attach = getattr(session_manager, 'attach_workspace_manager', None)
    if callable(attach):
        attach(workspace_manager)
    try:
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
            WORKSPACE_STATUS_PROVISIONING,
        )
    except ImportError:
        return
    try:
        records = workspace_manager.list_workspaces()
    except Exception:
        app.logger.exception('failed to list workspaces during session resume')
        return
    spawn_defaults = _planning_spawn_defaults(runner)
    architecture_doc_path = (
        os.environ.get('KATO_ARCHITECTURE_DOC_PATH', '') or ''
    )
    resumed = 0
    skipped: list[str] = []
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        status = str(getattr(record, 'status', '') or '')
        if status not in (WORKSPACE_STATUS_ACTIVE, WORKSPACE_STATUS_PROVISIONING):
            continue
        cwd = str(getattr(record, 'cwd', '') or '')
        if not cwd:
            # Fall back to the first repo clone if cwd wasn't recorded.
            for repo_id in (getattr(record, 'repository_ids', []) or []):
                try:
                    candidate = workspace_manager.repository_path(
                        task_id, str(repo_id),
                    )
                except Exception:
                    continue
                if candidate.is_dir():
                    cwd = str(candidate)
                    break
        if not cwd:
            skipped.append(f'{task_id} (no cwd)')
            continue
        try:
            initial_prompt = _resume_prompt_for_workspace(record)
            session_manager.start_session(
                task_id=task_id,
                task_summary=str(getattr(record, 'task_summary', '') or ''),
                initial_prompt=initial_prompt,
                cwd=cwd,
                expected_branch=task_id,
                architecture_doc_path=architecture_doc_path,
                **spawn_defaults,
            )
            resumed += 1
        except Exception as exc:
            app.logger.warning(
                'could not resume Claude session for %s: %s '
                '(operator can send a message to wake the tab manually)',
                task_id, exc,
            )
            skipped.append(task_id)
    if resumed:
        app.logger.info(
            'resumed %d Claude session(s) from previous kato run',
            resumed,
        )
    if skipped:
        app.logger.info(
            'skipped resume for %d session(s): %s',
            len(skipped), ', '.join(skipped[:10]),
        )


def _planning_spawn_defaults(runner) -> dict[str, object]:
    """Mirror ``WaitPlanningService._session_starter_defaults`` so the
    resumed sessions use the same binary / model / permission-mode the
    autonomous flow uses.
    """
    if runner is None:
        return {}
    defaults = getattr(runner, '_defaults', None)
    if defaults is None:
        return {}
    fields = (
        'binary',
        'model',
        'permission_mode',
        'permission_prompt_tool',
        'allowed_tools',
        'disallowed_tools',
        'effort',
    )
    result: dict[str, object] = {
        field: (getattr(defaults, field, '') or '') for field in fields
    }
    result['max_turns'] = getattr(defaults, 'max_turns', None)
    return result


def _resume_prompt_for_workspace(record) -> str:
    if bool(getattr(record, 'resume_on_startup', True)):
        return agent_prompt_utils.prepend_forbidden_repository_guardrails(
            _RESUME_CONTINUE_PROMPT,
        )
    return agent_prompt_utils.prepend_forbidden_repository_guardrails(_RESUME_WAIT_PROMPT)


def _load_hooks_or_refuse(app, logger) -> None:
    """Ensure ``app.hook_runner`` is installed; load on demand if not.

    In the normal boot path, :class:`KatoCoreLib.__init__` already
    loaded hooks (so sub-services see a real runner at construction
    time) and this call is just a presence check. In test setups
    that bypass ``KatoInstance.init`` (mocking it out and supplying
    a SimpleNamespace ``app``), the loader runs here for the first
    time. Either way we end with ``app.hook_runner`` populated.

    Refuses startup on schema errors so config bugs surface at boot.
    """
    if getattr(app, 'hook_runner', None) is not None:
        return
    from kato_core_lib.hooks.config import HookConfigError, load_hooks_config
    from kato_core_lib.hooks.runner import HookRunner
    try:
        config = load_hooks_config()
    except HookConfigError as exc:
        logger.error('hooks config rejected: %s', exc)
        raise SystemExit(1) from exc
    app.hooks_config = config
    app.hook_runner = HookRunner(config, logger=logger)
    if not config.is_empty():
        points = sorted(p.value for p, hs in config.hooks_by_point.items() if hs)
        logger.info(
            'kato hooks loaded: %d point(s) configured (%s)',
            len(points), ', '.join(points),
        )


def _recover_orphan_workspaces(app) -> None:
    """Adopt out-of-band task folders dropped under ``KATO_WORKSPACES_ROOT``.

    Best-effort, runs exactly once per kato process. Failures are logged
    and swallowed so a flaky filesystem can't block startup.
    """
    recovery = getattr(app, 'workspace_recovery_service', None)
    if recovery is None:
        return
    try:
        adopted = recovery.recover_orphan_workspaces()
    except Exception:
        app.logger.exception('workspace recovery failed; continuing without it')
        return
    if adopted:
        app.logger.info(
            'recovered %d orphan workspace%s during startup',
            len(adopted),
            '' if len(adopted) == 1 else 's',
        )


def _start_planning_webserver_if_enabled(app) -> None:
    """Boot the Flask planning UI in a daemon thread inside this process.

    We run kato + webserver in the same Python process so they share the
    in-memory :class:`ClaudeSessionManager`. The webserver lives in a
    separate package (``webserver/``) but is imported here so the live
    sessions the orchestrator creates are the same ones the browser tabs
    talk to.
    """
    import os
    import threading

    if str(os.environ.get('KATO_WEBSERVER_DISABLED', '')).strip().lower() in {'1', 'true', 'yes', 'on'}:
        app.logger.info('planning webserver disabled via KATO_WEBSERVER_DISABLED')
        return

    session_manager = getattr(app, 'session_manager', None)
    workspace_manager = getattr(app, 'workspace_manager', None)
    planning_session_runner = getattr(app, 'planning_session_runner', None)
    if session_manager is None and workspace_manager is None:
        # Both backends now use a workspace manager, so this only fires
        # in stripped-down setups (tests, embedded use). Nothing to
        # render → keep the webserver off.
        app.logger.info(
            'planning webserver skipped (no session_manager / workspace_manager wired)'
        )
        return

    try:
        from kato_webserver.app import create_app as _create_webserver_app
    except ImportError as exc:
        app.logger.warning(
            'planning webserver not available (%s); install ./webserver to enable', exc,
        )
        return

    host = os.environ.get('KATO_WEBSERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('KATO_WEBSERVER_PORT', '5050'))
    flask_app = _create_webserver_app(
        session_manager=session_manager,
        workspace_manager=workspace_manager,
        planning_session_runner=planning_session_runner,
        status_broadcaster=_STATUS_BROADCASTER,
        agent_service=getattr(app, 'service', None),
        force_scan_event=_FORCE_SCAN_EVENT,
        scan_in_progress_event=_SCAN_IN_PROGRESS,
        hook_runner=getattr(app, 'hook_runner', None),
    )

    # Silence Werkzeug's per-request access log — the planning UI polls
    # /api/sessions every 5s and that drowns the kato terminal in noise.
    # Errors and tracebacks still come through (they go to stderr).
    import logging as _logging
    _logging.getLogger('werkzeug').setLevel(_logging.ERROR)

    def _serve() -> None:
        try:
            flask_app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        except Exception:
            app.logger.exception('planning webserver crashed')

    thread = threading.Thread(
        target=_serve,
        name='kato-planning-webserver',
        daemon=True,
    )
    thread.start()
    url = f'http://{host}:{port}'
    app.planning_webserver_url = url
    app.logger.info('planning webserver listening on %s', url)
    _open_browser_when_ready(url, app.logger)


def _open_browser_when_ready(url: str, logger) -> None:
    """Wait until the planning webserver answers, then open the browser tab.

    Off by default in CI / headless setups: set ``KATO_OPEN_BROWSER=0`` to
    skip. The poll runs in a daemon thread so kato's main loop never waits
    on the browser opening.
    """
    import os
    import threading
    import webbrowser

    if str(os.environ.get('KATO_OPEN_BROWSER', '1')).strip().lower() in {'0', 'false', 'no', 'off'}:
        return

    def _wait_and_open() -> None:
        if not _wait_for_planning_ui_healthz(url, logger=logger):
            logger.warning('planning webserver never answered /healthz; not opening browser')
            return
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            logger.exception('failed to open planning UI in browser')

    threading.Thread(
        target=_wait_and_open,
        name='kato-open-browser',
        daemon=True,
    ).start()


def _register_shutdown_hook(app) -> None:
    def _shutdown(signum, frame):
        app.logger.info('shutting down kato agent (signal %s)', signum)
        watcher = getattr(app, 'resume_prompt_watcher', None)
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                app.logger.exception(
                    'error stopping resume_prompt watcher',
                )
        try:
            app.service.shutdown()
        except Exception:
            app.logger.exception('error during shutdown cleanup')
        raise SystemExit(0)

    # SIGINT works on every supported platform. SIGTERM works on POSIX
    # but Windows refuses to install a Python handler for it (and
    # delivers TerminateProcess instead) — register defensively so
    # kato boots cleanly on Windows shells too.
    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):
        app.logger.debug(
            'SIGTERM handler not installable on this platform; '
            'relying on SIGINT for graceful shutdown',
        )


def _requeue_stuck_comments(app) -> None:
    """Re-queue local comments orphaned IN_PROGRESS by the last restart.

    Without this a comment whose agent was mid-run when kato stopped
    stays IN_PROGRESS forever: the scan-loop drain only re-dispatches
    QUEUED comments, and lazy resume (see the boot comment above
    ``_start_planning_webserver_if_enabled``) means the chat session
    is not respawned at boot — so the thread sits unaddressed and its
    tab stays "Claude: sleeping". Flipping it back to QUEUED lets the
    next scan tick pick it up and respawn the session. Best-effort:
    a failure here must never abort boot.
    """
    service = getattr(app, 'service', None)
    requeue = getattr(service, 'requeue_stuck_in_progress_comments', None)
    if not callable(requeue):
        return
    try:
        requeued = requeue()
    except Exception:
        app.logger.exception(
            'failed to requeue stuck in-progress comments at boot',
        )
        return
    if requeued:
        app.logger.info(
            'requeued %d comment(s) stuck in-progress from the previous '
            'run; _start_pending_comment_work will dispatch them next',
            len(requeued),
        )


def _log_known_session_ids(app) -> None:
    """Log every known Claude session id at kato startup.

    Operators use these IDs to cross-reference ``claude /status``
    output, find JSONL histories on disk, and diagnose stale state.
    Best-effort: any failure here must never abort boot.
    """
    service = getattr(app, 'service', None)
    session_manager = getattr(service, '_session_manager', None)
    if session_manager is None:
        return
    try:
        records = session_manager.list_records()
    except Exception:
        app.logger.exception('failed to list session records at boot')
        return
    if not records:
        app.logger.info('no known Claude session ids at startup')
        return
    lines = []
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '').strip()
        sid = read_session_id_from(record)
        if task_id and sid:
            lines.append(f'  task {task_id}: session id {sid}')
    if lines:
        app.logger.info(
            'known Claude session ids at startup:\n%s', '\n'.join(lines),
        )
    else:
        app.logger.info('no Claude session ids recorded at startup')


def _start_pending_comment_work(app) -> None:
    """At boot, immediately start the agent on every task that has a
    queued comment — don't make the operator wait for the first scan
    tick (startup delay + scan interval).

    Runs straight after ``_requeue_stuck_comments`` so a comment
    orphaned IN_PROGRESS by the previous run (now flipped back to
    QUEUED) is dispatched in the same boot pass: kato spawns/resumes
    the session and works on the comment right away instead of the
    tab sitting on "kato working" until a scan tick happens to come
    round. Best-effort: a failure here must never abort boot — the
    scan loop's own drain remains the backstop.
    """
    service = getattr(app, 'service', None)
    drain = getattr(service, 'drain_all_queued_task_comments', None)
    if not callable(drain):
        return
    try:
        started = drain()
    except Exception:
        app.logger.exception(
            'failed to dispatch queued comments at boot',
        )
        return
    if started:
        app.logger.info(
            'started agent work on %d task(s) with queued comments at boot',
            len(started),
        )


def _start_pending_comment_work_after_ui(app) -> None:
    """Dispatch queued comments after the planning UI has had first shot.

    The actual drain can spawn Claude sessions. Keep it off the boot
    path so a restart serves the UI first, then resumes queued local
    comment work in the background.
    """
    thread = threading.Thread(
        target=lambda: _start_pending_comment_work_when_ui_ready(app),
        name='kato-start-pending-comments',
        daemon=True,
    )
    thread.start()


def _start_pending_comment_work_when_ui_ready(app) -> None:
    url = str(getattr(app, 'planning_webserver_url', '') or '')
    if url and not _wait_for_planning_ui_healthz(url, logger=app.logger):
        app.logger.warning(
            'planning UI did not answer /healthz before queued-comment '
            'startup drain; dispatching queued comments anyway',
        )
    _start_pending_comment_work(app)


def _wait_for_planning_ui_healthz(
    url: str,
    *,
    timeout: float = 15.0,
    logger,
) -> bool:
    """Poll ``<url>/healthz`` until it answers or ``timeout`` elapses.

    Returns True once the endpoint responds, False on timeout. Callers
    decide what to log / do on timeout.
    """
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f'{url}/healthz', timeout=1):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _warm_up_repository_inventory(app) -> None:
    service = getattr(app, 'service', None)
    warm_up = getattr(service, 'warm_up_repository_inventory', None)
    if callable(warm_up):
        warm_up()


def _start_resume_prompt_watcher(app) -> None:
    """Background writer for per-task ``<workspace>/resume_prompt.md``.

    Polls live sessions every few seconds; whenever a Claude turn
    ends (a fresh ``result`` event lands in the session's recent-
    events buffer) it rewrites the markdown snapshot. The file is
    paste-into-Cursor-ready, so the operator can hand off the task
    to another agent without losing context.

    Best-effort: failure to start the watcher logs but doesn't
    block kato boot — kato runs fine without resume_prompt.md.
    """
    session_manager = getattr(app, 'session_manager', None)
    workspace_manager = getattr(app, 'workspace_manager', None)
    if session_manager is None or workspace_manager is None:
        app.logger.info(
            'resume_prompt watcher not started: session or workspace '
            'manager is not wired (headless / partial init)',
        )
        return
    try:
        from kato_core_lib.data_layers.service.resume_prompt_watcher import (
            build_and_start_resume_prompt_watcher,
        )
        watcher = build_and_start_resume_prompt_watcher(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
        )
    except Exception:
        app.logger.exception(
            'failed to start resume_prompt watcher; kato will continue '
            'without auto-updated resume_prompt.md files',
        )
        return
    # Stash on the app so the shutdown hook can stop it cleanly.
    app.resume_prompt_watcher = watcher


def _task_scan_settings(cfg: DictConfig) -> tuple[float, float]:
    task_scan_cfg = cfg.kato.get('task_scan', {}) or {}
    return (
        float(task_scan_cfg.get('startup_delay_seconds', 5.0)),
        # Default 180s (3 min) matches the yaml. Slow enough that
        # parallel PR-lookups across (task × repo) don't trip
        # Bitbucket / GitHub / GitLab rate limits; fast enough that
        # review-comment pickup feels responsive. ``0`` disables the
        # autonomous loop (operator must manually trigger scans).
        float(task_scan_cfg.get('scan_interval_seconds', 180.0)),
    )


def _run_task_scan_loop(
    app,
    *,
    startup_delay_seconds: float,
    scan_interval_seconds: float,
    sleep_fn=time.sleep,
    max_cycles: int | None = None,
    force_scan_event: threading.Event | None = None,
) -> None:
    job = ProcessAssignedTasksJob()
    job.initialized(app)
    # Manual-scan mode: ``scan_interval_seconds <= 0`` means "no
    # auto-poll". Kato stays alive and serves the webserver, but
    # no autonomous scan ever runs — every task pickup / review-
    # comment check has to be triggered by the operator via the
    # UI ("Scan now" in the tab strip, "Sync" on a task header,
    # or POST /api/scan/trigger). This is the default: the auto-
    # poll was hammering Bitbucket / GitHub / GitLab every 30s
    # with N parallel PR-lookups per (task × repo) and tripping
    # rate limits on multi-repo accounts. The operator can opt
    # the loop back in by setting ``scan_interval_seconds > 0``
    # in config or via the ``KATO_SCAN_INTERVAL_SECONDS`` env.
    #
    # The force-scan path (POST /api/scan/trigger) still works
    # without the loop because the webserver invokes
    # ``ProcessAssignedTasksJob.run()`` directly on the request
    # thread — see ``_register_scan_trigger_route``.
    if scan_interval_seconds <= 0:
        app.logger.info(
            'Autonomous scan loop disabled (scan_interval_seconds=%s). '
            'Use the UI "Scan now" / "Sync" buttons or '
            'POST /api/scan/trigger to run a scan on demand.',
            scan_interval_seconds,
        )
        return
    if startup_delay_seconds > 0:
        if supports_inline_status():
            sleep_with_warmup_countdown(
                startup_delay_seconds,
                sleep_fn=sleep_fn,
            )
        else:
            app.logger.info(
                'Waiting %s before scanning tasks while Kato warms up',
                _formatted_duration_text(startup_delay_seconds),
            )
            sleep_fn(startup_delay_seconds)

    cycles = 0
    while True:
        if force_scan_event is not None:
            force_scan_event.clear()
        _SCAN_IN_PROGRESS.set()
        app.logger.info('Scanning for new tasks and reviews')
        try:
            job.run()
            app.logger.info('Scan complete')
        except Exception:
            app.logger.warning(
                'task scan failed; retrying in %s seconds',
                scan_interval_seconds,
            )
        finally:
            _SCAN_IN_PROGRESS.clear()

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        _idle_with_heartbeat(
            scan_interval_seconds,
            logger=app.logger,
            sleep_fn=sleep_fn,
            force_scan_event=force_scan_event,
        )


def _idle_with_heartbeat(
    interval_seconds: float,
    *,
    logger,
    sleep_fn=time.sleep,
    heartbeat_seconds: float = 5.0,
    force_scan_event: threading.Event | None = None,
) -> None:
    """Sleep ``interval_seconds`` between scan ticks.

    The terminal sees a single inline-status line that updates in place
    via carriage-return; no new lines per heartbeat. The planning UI's
    SSE feed sees one heartbeat entry per ``heartbeat_seconds`` chunk so
    the status bar shows a live countdown — published directly to the
    broadcaster (bypassing the Python logger so it doesn't double-print
    to stderr).

    The loop is driven by chunk count, not wall-clock, so a mocked
    ``sleep_fn`` in tests doesn't have to also patch ``time.monotonic``.
    """
    del logger  # unused: we publish to the broadcaster directly now
    total = float(interval_seconds)
    if total <= 0:
        return
    step = max(1.0, float(heartbeat_seconds))
    use_spinner = supports_inline_status()
    remaining = total
    while remaining > 0:
        if force_scan_event is not None and force_scan_event.is_set():
            break
        chunk = step if remaining >= step else remaining
        countdown = int(round(remaining))
        # Push the heartbeat to the SSE feed only — the broadcaster bypasses
        # the Python logger so no new line lands on the terminal.
        _STATUS_BROADCASTER.publish(
            level='INFO',
            logger_name='kato.heartbeat',
            message=f'Idle · next scan in {countdown}s',
        )
        if use_spinner:
            # Carriage-return spinner with countdown — single inline line
            # the terminal updates in place every chunk.
            sleep_with_countdown_spinner(
                chunk,
                status_text='Idle · next scan in',
                countdown_seconds=countdown,
                sleep_fn=sleep_fn,
            )
        else:
            # Use event.wait so a force-scan request wakes the loop
            # immediately instead of waiting out the full chunk.
            if force_scan_event is not None:
                force_scan_event.wait(timeout=chunk)
            else:
                sleep_fn(chunk)
        remaining -= chunk


def _formatted_duration_text(seconds: float) -> str:
    normalized_seconds = float(seconds)
    rounded_seconds = int(normalized_seconds)
    if normalized_seconds == rounded_seconds:
        seconds_label = 'second' if rounded_seconds == 1 else 'seconds'
        return f'{rounded_seconds} {seconds_label}'
    return f'{normalized_seconds:.1f} seconds'


if __name__ == '__main__':
    raise SystemExit(main())
