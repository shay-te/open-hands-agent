"""Bridge: take a Kato task, run it as a live planning session, return a one-shot-shaped result.

When ``KATO_CLAUDE_BYPASS_PERMISSIONS=false`` and the task carries the
``kato:wait-planning`` tag, the orchestrator uses this helper instead of
the one-shot :class:`ClaudeCliClient.implement_task` path. The helper
spawns a long-lived :class:`StreamingClaudeSession` via the shared
:class:`ClaudeSessionManager`, blocks until the agent emits its terminal
``result`` event, and shapes that into the same ``dict`` the rest of the
orchestration already understands. The browser tab connected to the
session sees the same events stream past in real time and can chat /
approve permissions while the agent works.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    AGENT_SESSION_ID,
    read_session_id_from,
)
from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from claude_core_lib.claude_core_lib.session.manager import (
    SESSION_STATUS_REVIEW,
    SESSION_STATUS_TERMINATED,
    ClaudeSessionManager,
)
from kato_core_lib.data_layers.data.fields import ImplementationFields
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers import agent_prompt_utils
from kato_core_lib.helpers.kato_result_utils import build_openhands_result
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from kato_core_lib.helpers.text_utils import normalized_text


def _coerce_optional_int(value) -> int | None:
    """Parse a positive int from omegaconf-style values; None on anything else."""
    if value in (None, ''):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class SessionStoppedByUserError(RuntimeError):
    """Raised by ``_run_to_terminal`` when the user explicitly stopped the session.

    Distinguished from a crash/failure so the caller can skip moving the
    task back to "Open" (which would cause an immediate re-spawn).
    """


@dataclass
class StreamingSessionDefaults(object):
    """The Claude-config settings the manager needs to spawn a session.

    Stored as a plain dataclass so :class:`KatoCoreLib` can build it from
    its existing config block without coupling the orchestrator to omegaconf.
    """

    binary: str = 'claude'
    model: str = ''
    permission_mode: str = 'acceptEdits'
    permission_prompt_tool: str = ''
    allowed_tools: str = ''
    disallowed_tools: str = ''
    max_turns: int | None = None
    effort: str = ''
    architecture_doc_path: str = ''
    lessons_path: str = ''
    # Set from ``KATO_CLAUDE_DOCKER`` at boot. When True, every spawned
    # streaming session wraps the Claude subprocess in the hardened
    # Docker sandbox. Independent of ``permission_mode`` — docker is
    # the *containment* layer; permission_mode is the *prompt* layer.
    docker_mode_on: bool = False


class PlanningSessionRunner(object):
    """Run one task end-to-end via a streaming Claude session.

    Drop-in replacement for the one-shot ``implement_task`` call when the
    task is tagged ``kato:wait-planning``. The returned dict matches what
    :func:`build_openhands_result` would produce, so the existing publish
    flow (commit, push, PR, review-state transition) works unchanged.
    """

    DEFAULT_MAX_WAIT_SECONDS = 60 * 60 * 4   # generous: humans plan slowly
    DEFAULT_DRAIN_TIMEOUT_SECONDS = 0.25

    @classmethod
    def from_config(
        cls,
        open_cfg,
        agent_backend: str,
        session_manager: ClaudeSessionManager | None,
        *,
        docker_mode_on: bool = False,
        hook_runner=None,
    ) -> 'PlanningSessionRunner | None':
        """Build the runner (or return None) from the kato config block.

        Returns None when the active backend has no streaming model (e.g.
        OpenHands) or when the session manager wasn't created — the rest of
        the orchestration is wired to fall back to the one-shot path in
        those cases.
        """
        if str(agent_backend or '').strip().lower() != 'claude':
            return None
        if session_manager is None:
            return None
        claude_cfg = getattr(open_cfg, 'claude', None)
        if claude_cfg is None:
            return None
        defaults = cls._build_defaults(claude_cfg, docker_mode_on=docker_mode_on)
        return cls(
            session_manager=session_manager,
            defaults=defaults,
            hook_runner=hook_runner,
        )

    @staticmethod
    def _build_defaults(
        claude_cfg,
        *,
        docker_mode_on: bool = False,
    ) -> 'StreamingSessionDefaults':
        bypass = bool(getattr(claude_cfg, 'bypass_permissions', False))
        return StreamingSessionDefaults(
            binary=str(getattr(claude_cfg, 'binary', '') or 'claude'),
            model=str(getattr(claude_cfg, 'model', '') or ''),
            permission_mode='bypassPermissions' if bypass else 'acceptEdits',
            allowed_tools=str(getattr(claude_cfg, 'allowed_tools', '') or ''),
            disallowed_tools=str(getattr(claude_cfg, 'disallowed_tools', '') or ''),
            max_turns=_coerce_optional_int(getattr(claude_cfg, 'max_turns', None)),
            effort=str(getattr(claude_cfg, 'effort', '') or ''),
            architecture_doc_path=str(getattr(claude_cfg, 'architecture_doc_path', '') or ''),
            lessons_path=str(getattr(claude_cfg, 'lessons_path', '') or ''),
            docker_mode_on=bool(docker_mode_on),
        )

    def __init__(
        self,
        session_manager: ClaudeSessionManager,
        defaults: StreamingSessionDefaults,
        *,
        max_wait_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        hook_runner=None,
    ) -> None:
        self._session_manager = session_manager
        self._defaults = defaults
        self._max_wait_seconds = (
            max_wait_seconds
            if max_wait_seconds is not None
            else self.DEFAULT_MAX_WAIT_SECONDS
        )
        self._clock = clock
        # Operator-extensibility hooks. ``None`` (no config file at
        # boot) is treated as a silent no-op so existing behaviour
        # is unchanged for kato installs that never adopt hooks.
        self._hook_runner = hook_runner
        self.logger = configure_logger(self.__class__.__name__)

    def resume_session_for_chat(
        self,
        *,
        task_id: str,
        message: str,
        cwd: str = '',
        task_summary: str = '',
        additional_dirs: list[str] | None = None,
        model: str = '',
    ):
        """Spawn a fresh Claude subprocess for ``task_id`` and queue ``message``.

        Used by the webserver when the user sends a chat message to a tab
        whose previous subprocess has already exited. The session manager
        resumes via the persisted ``--resume <session_id>`` if a Claude
        session is on file (kato-meta.json or kato/sessions), otherwise
        starts fresh. Returns the live ``StreamingClaudeSession`` so the
        caller can write follow-up messages, but the spawn itself does not
        block waiting for a result event.
        """
        normalized_task_id = str(task_id or '').strip()
        if not normalized_task_id:
            raise ValueError('task_id is required to resume a chat session')
        normalized_message = str(message or '').strip()
        if not normalized_message:
            raise ValueError('message is required to resume a chat session')
        # When we have a saved session id, ``start_session`` will pass
        # ``--resume <id>`` to Claude — the prior conversation (workspace
        # inventory, continuity, forbidden-repo guardrails, every prior
        # turn) is already loaded by the CLI. Wrapping the user's
        # follow-up in another inventory/continuity block was making
        # Claude treat each respawn as a fresh task and re-explore the
        # workspace from scratch.
        #
        # Only inject the chat-workspace context on the FIRST spawn for
        # this task — when there's no session id to resume from.
        existing_record = (
            self._session_manager.get_record(normalized_task_id)
            if self._session_manager is not None
            else None
        )
        resume_session_id = read_session_id_from(existing_record)
        if resume_session_id:
            initial_prompt = normalized_message
        else:
            initial_prompt = agent_prompt_utils.prepend_chat_workspace_context(
                normalized_message,
                cwd=cwd,
                additional_dirs=additional_dirs,
            )
        # Fire user_prompt_submit BEFORE the spawn. Operator hooks
        # at this point see the raw message + task id and can
        # audit / mirror to Slack / etc. They cannot block (the
        # ``blocked`` semantic only applies to ``pre_tool_use``).
        self._fire_hook('user_prompt_submit', {
            'task_id': normalized_task_id,
            'message': normalized_message,
            'cwd': normalized_text(cwd),
            'resumed': bool(resume_session_id),
        })
        session = self._session_manager.start_session(
            task_id=normalized_task_id,
            task_summary=normalized_text(task_summary),
            initial_prompt=initial_prompt,
            cwd=normalized_text(cwd),
            binary=self._defaults.binary,
            model=model or self._defaults.model,
            permission_mode=self._defaults.permission_mode,
            permission_prompt_tool=self._defaults.permission_prompt_tool,
            allowed_tools=self._defaults.allowed_tools,
            disallowed_tools=self._defaults.disallowed_tools,
            max_turns=self._defaults.max_turns,
            effort=self._defaults.effort,
            expected_branch='',
            architecture_doc_path=self._defaults.architecture_doc_path,
            lessons_path=self._defaults.lessons_path,
            docker_mode_on=self._defaults.docker_mode_on,
            additional_dirs=additional_dirs,
        )
        sid = read_session_id_from(session)
        self.logger.info(
            'task %s: chat session started — %s session id %s',
            normalized_task_id,
            'resuming' if resume_session_id else 'fresh',
            sid or '(unknown)',
        )
        # Fire session_start when a NEW subprocess was spawned (not
        # when reusing a still-alive session). ``resumed`` lets the
        # hook tell "fresh kato spawn" apart from "Claude --resume".
        self._fire_hook('session_start', {
            'task_id': normalized_task_id,
            'cwd': normalized_text(cwd),
            'resumed': bool(resume_session_id),
            AGENT_SESSION_ID: sid,
        })
        return session

    def _fire_hook(self, point: str, event: dict) -> None:
        """Fire a configured hook at ``point``. Never crashes the
        caller — the runner already isolates hook failures.
        """
        runner = self._hook_runner
        if runner is None:
            return
        try:
            # Import lazily so callers that don't use hooks (or
            # tests that don't construct a runner) don't pay the
            # import cost.
            from kato_core_lib.hooks.config import HookPoint
            runner.fire(HookPoint(point), dict(event))
        except Exception:
            # Defensive — should already be handled inside fire(),
            # but never let a hook bug take down the chat flow.
            self.logger.exception('hook firing failed for %s', point)

    def implement_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        branch_name = agent_prompt_utils.task_branch_name(task, prepared_task)
        return self._run_to_terminal(
            task_id=str(task.id),
            task_summary=normalized_text(task.summary),
            cwd=self._working_directory(prepared_task),
            initial_prompt=self._build_implementation_prompt(task, prepared_task),
            branch_name=branch_name,
            default_commit_message=f'Implement {task.id}',
            log_label='planning session',
        )

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        *,
        task_id: str,
        task_summary: str = '',
        repository_local_path: str = '',
    ) -> dict[str, str | bool]:
        """Run a review-comment fix as a streaming session bound to ``task_id``.

        Each review-fix gets a fresh subprocess so the runner has a clean
        ``terminal_event`` to wait on; the persisted Claude session_id
        carries conversation context across the restart. The browser tab
        stays bound to ``task_id``, so the user sees the new turn stream
        in next to the original implementation history.
        """
        return self.fix_review_comments(
            [comment],
            branch_name,
            task_id=task_id,
            task_summary=task_summary,
            repository_local_path=repository_local_path,
        )

    def fix_review_comments(
        self,
        comments: list[ReviewComment],
        branch_name: str,
        *,
        task_id: str,
        task_summary: str = '',
        repository_local_path: str = '',
        mode: str = 'fix',
    ) -> dict[str, str | bool]:
        """Address multiple comments in a single streaming session.

        Same teardown / resume mechanics as the singular path; only
        the prompt is batched. ``len(comments) == 1`` produces an
        identical prompt to ``fix_review_comment``. ``mode='answer'``
        switches the prompt to the question-answering shape.
        """
        if not comments:
            raise ValueError('fix_review_comments requires at least one comment')
        normalized_task_id = str(task_id or '').strip()
        if not normalized_task_id:
            raise ValueError('task_id is required to fix review comments')
        if self._session_manager.get_session(normalized_task_id) is not None:
            self._session_manager.terminate_session(normalized_task_id)
            # The previous subprocess just got killed to make room for
            # the review-fix turn. ``reason='replaced'`` lets hooks
            # tell this apart from a natural finish.
            self._fire_hook('session_end', {
                'task_id': normalized_task_id,
                'reason': 'replaced',
                'log_label': 'review-fix session',
            })
        workspace = normalized_text(repository_local_path)
        prompt = (
            ClaudeCliClient._build_review_prompt(
                comments[0], branch_name, workspace_path=workspace, mode=mode,
            )
            if len(comments) == 1
            else ClaudeCliClient._build_review_comments_batch_prompt(
                comments, branch_name, workspace_path=workspace, mode=mode,
            )
        )
        return self._run_to_terminal(
            task_id=normalized_task_id,
            task_summary=normalized_text(task_summary),
            cwd=normalized_text(repository_local_path),
            initial_prompt=prompt,
            branch_name=normalized_text(branch_name),
            default_commit_message='Address review comments',
            log_label='review-fix session',
        )

    def _run_to_terminal(
        self,
        *,
        task_id: str,
        task_summary: str,
        cwd: str,
        initial_prompt: str,
        branch_name: str,
        default_commit_message: str,
        log_label: str,
    ) -> dict[str, str | bool]:
        """Spawn the streaming session, block until terminal, shape the result.

        Shared by every entrypoint that runs the agent end-to-end. The
        per-call differences (which prompt to send, which commit message
        to default to, which log label to use, ...) are passed in.
        """
        self.logger.info(
            'starting %s for task %s (cwd=%s)', log_label, task_id, cwd or '?',
        )
        session = self._start_session(
            task_id=task_id,
            task_summary=task_summary,
            initial_prompt=initial_prompt,
            cwd=cwd,
            branch_name=branch_name,
        )
        sid = read_session_id_from(session)
        self.logger.info(
            'task %s: %s started — fresh session id %s',
            task_id, log_label, sid or '(unknown)',
        )
        # Hook fires here too — autonomous (non-chat) entrypoints
        # don't go through ``resume_session_for_chat`` so this is
        # the only spot where ``session_start`` sees them.
        self._fire_hook('session_start', {
            'task_id': task_id,
            'cwd': cwd,
            'resumed': False,
            'log_label': log_label,
            AGENT_SESSION_ID: sid,
        })
        terminal = self._wait_for_terminal_event(session, task_id=task_id)
        if terminal is None:
            # Check whether the session ended because the user explicitly
            # clicked Stop (record status already TERMINATED by
            # terminate_session) vs. an unexpected crash.
            record = self._session_manager.get_record(task_id)
            user_stopped = (
                record is not None
                and record.status == SESSION_STATUS_TERMINATED
            )
            if not user_stopped:
                self._session_manager.update_status(task_id, SESSION_STATUS_TERMINATED)
            self._fire_hook('session_end', {
                'task_id': task_id,
                'reason': 'stopped' if user_stopped else 'no_terminal_event',
                'log_label': log_label,
            })
            if user_stopped:
                raise SessionStoppedByUserError(
                    f'{log_label} for task {task_id} stopped by user'
                )
            raise RuntimeError(
                f'{log_label} for task {task_id} ended without a result event'
            )
        try:
            result_text = self._raise_if_terminal_failed(
                terminal, task_id=task_id, log_label=log_label,
            )
        except RuntimeError:
            # ``_raise_if_terminal_failed`` already flipped status to
            # TERMINATED — fire end here too with ``reason='error'``
            # before propagating.
            self._fire_hook('session_end', {
                'task_id': task_id,
                'reason': 'error',
                'log_label': log_label,
            })
            raise
        # Normal terminal — fire session_end before the publish flow
        # starts so observers see "agent finished" distinct from
        # "PR pushed".
        self._fire_hook('session_end', {
            'task_id': task_id,
            'reason': 'completed',
            'log_label': log_label,
            AGENT_SESSION_ID: read_session_id_from(session),
        })

        # Tab back to blue while the orchestrator publishes / waits for review.
        self._session_manager.update_status(task_id, SESSION_STATUS_REVIEW)
        return build_openhands_result(
            {
                ImplementationFields.SUCCESS: True,
                'summary': result_text,
                ImplementationFields.MESSAGE: result_text,
                ImplementationFields.SESSION_ID: sid,
            },
            branch_name=branch_name,
            default_commit_message=default_commit_message,
            default_success=True,
        )

    def _start_session(
        self,
        *,
        task_id: str,
        task_summary: str,
        initial_prompt: str,
        cwd: str,
        branch_name: str,
    ):
        return self._session_manager.start_session(
            task_id=task_id,
            task_summary=task_summary,
            initial_prompt=initial_prompt,
            cwd=cwd,
            binary=self._defaults.binary,
            model=self._defaults.model,
            permission_mode=self._defaults.permission_mode,
            permission_prompt_tool=self._defaults.permission_prompt_tool,
            allowed_tools=self._defaults.allowed_tools,
            disallowed_tools=self._defaults.disallowed_tools,
            max_turns=self._defaults.max_turns,
            effort=self._defaults.effort,
            expected_branch=branch_name,
            architecture_doc_path=self._defaults.architecture_doc_path,
            lessons_path=self._defaults.lessons_path,
            docker_mode_on=self._defaults.docker_mode_on,
        )

    def _raise_if_terminal_failed(
        self,
        terminal,
        *,
        task_id: str,
        log_label: str,
    ) -> str:
        """Translate a terminal ``result`` event into success text or an error."""
        result_payload = terminal.raw or {}
        result_text = normalized_text(result_payload.get('result', ''))
        if bool(result_payload.get('is_error', False)):
            self._session_manager.update_status(task_id, SESSION_STATUS_TERMINATED)
            detail = result_text or f'{log_label} reported an error'
            raise RuntimeError(f'{log_label} failed: {detail}')
        return result_text

    def _wait_for_terminal_event(self, session, *, task_id: str):
        deadline = self._clock() + max(0.0, float(self._max_wait_seconds))
        terminal = None
        while True:
            event = session.poll_event(timeout=self.DEFAULT_DRAIN_TIMEOUT_SECONDS)
            if event is not None:
                if event.is_terminal:
                    terminal = event
                    break
                continue
            if not session.is_alive:
                terminal = session.terminal_event
                break
            if self._clock() >= deadline:
                self.logger.warning(
                    'planning session for task %s exceeded max wait of %.0fs',
                    task_id,
                    self._max_wait_seconds,
                )
                break
        return terminal

    @staticmethod
    def _working_directory(prepared_task: PreparedTaskContext | None) -> str:
        if prepared_task is None:
            return ''
        repositories = list(prepared_task.repositories or [])
        if not repositories:
            return ''
        return normalized_text(getattr(repositories[0], 'local_path', '') or '')

    @staticmethod
    def _build_implementation_prompt(
        task: Task,
        prepared_task: PreparedTaskContext | None,
    ) -> str:
        # Reuse the same prompt the one-shot ClaudeCliClient builds so the
        # planning agent sees identical guardrails and instructions.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        builder = ClaudeCliClient(binary='unused-builder-only')
        return builder._build_implementation_prompt(task, prepared_task)
