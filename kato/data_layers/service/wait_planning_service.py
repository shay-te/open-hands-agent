"""Wait-planning short-circuit handling.

When a task is tagged ``kato:wait-planning``, the orchestrator skips
implementation/testing/publishing entirely and instead:

1. Resolves which repositories the task touches.
2. Provisions a per-task workspace folder + clones the repos into it.
3. Checks out the task branch on every cloned repo.
4. Spawns a long-lived Claude planning session in ``--permission-mode plan``
   so the user can drive the conversation in the planning UI.
5. Moves the ticket to "In Progress".

This whole flow lived on :class:`AgentService` and crowded that god-class
with 11 wait-planning-specific methods. Pulling it out gives each class
a single reason to change: ``AgentService`` is the top-level scan-loop
orchestrator, ``WaitPlanningService`` is the wait-planning workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kato.data_layers.data.fields import TaskTags
from kato.data_layers.data.task import Task
from kato.data_layers.service.workspace_manager import (
    provision_task_workspace_clones,
)
from kato.helpers.logging_utils import configure_logger
from kato.helpers.task_execution_utils import skip_task_result
from kato.helpers.text_utils import text_from_attr


# Fields the streaming runner exposes that ``start_session`` accepts.
# Strings get an empty-string fallback (avoid ``None`` slipping through
# to subprocess args); ``max_turns`` is passed through verbatim because
# ``None`` is the legitimate "no cap" sentinel.
_SESSION_STRING_FIELDS = (
    'binary',
    'model',
    'permission_mode',
    'permission_prompt_tool',
    'allowed_tools',
    'disallowed_tools',
    'effort',
)


@dataclass(frozen=True)
class _PlanningContext(object):
    """The cwd + branch the chat session opens on."""

    cwd: str
    expected_branch: str


class WaitPlanningService(object):
    """Owns the ``kato:wait-planning`` short-circuit lifecycle."""

    def __init__(
        self,
        *,
        session_manager,
        repository_service,
        task_state_service,
        workspace_manager=None,
        planning_session_runner=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._repository_service = repository_service
        self._task_state_service = task_state_service
        self._workspace_manager = workspace_manager
        self._planning_session_runner = planning_session_runner
        self.logger = logger or configure_logger(self.__class__.__name__)

    # ----- public API -----

    @staticmethod
    def task_has_wait_planning_tag(task: Task) -> bool:
        tags = getattr(task, 'tags', None) or []
        target = TaskTags.WAIT_PLANNING.lower()
        for tag in tags:
            if str(tag or '').strip().lower() == target:
                return True
        return False

    def handle_task(self, task: Task) -> dict[str, object] | None:
        """If ``task`` is tagged ``kato:wait-planning``, register the chat tab and stop.

        The orchestrator does no implementation/testing/publishing for
        these tasks — the human drives the conversation in the UI.
        Returns ``None`` to let the autonomous flow run; returns a skip
        result when the wait-planning short-circuit took the wheel.
        """
        if not self.task_has_wait_planning_tag(task):
            return None
        if self._session_manager is None:
            # No streaming backend (e.g. OpenHands) — nothing to register.
            self.logger.info(
                'task %s has %s but the active backend has no streaming UI; skipping',
                task.id,
                TaskTags.WAIT_PLANNING,
            )
            return skip_task_result(task.id, [])
        if self._is_chat_already_alive(task):
            return skip_task_result(task.id, [])
        context = self._resolve_planning_context(task)
        self._spawn_planning_session(task, context)
        # Planning is real work — move the ticket out of the inbox so
        # it doesn't get picked up by another agent / scanned again as
        # "needs to start". Idempotent on the ticket side, and only
        # called on the fresh-spawn branch (the alive guard above
        # protects the steady state).
        self._move_to_in_progress(task)
        return skip_task_result(task.id, [])

    # ----- internals -----

    def _is_chat_already_alive(self, task: Task) -> bool:
        existing = self._session_manager.get_session(str(task.id))
        return existing is not None and existing.is_alive

    def _spawn_planning_session(
        self,
        task: Task,
        context: _PlanningContext,
    ) -> None:
        # Belt-and-suspenders: the prompt explicitly forbids tool use,
        # AND the CLI runs in ``--permission-mode plan`` so Claude can't
        # execute even if it tries. Removing the tag flips back to the
        # configured permission mode via the autonomous path.
        spawn_defaults = self._session_starter_defaults()
        spawn_defaults['permission_mode'] = 'plan'
        try:
            self._session_manager.start_session(
                task_id=str(task.id),
                task_summary=str(task.summary or ''),
                # ``claude -p --input-format stream-json`` stays alive
                # across multiple user messages, but it must receive at
                # least one prompt at startup — empty stdin makes it
                # exit with an error and the scan loop would respawn it
                # forever. The contextual prompt below puts Claude in
                # "ready, waiting" state without kicking off any work.
                initial_prompt=self._build_planning_prompt(task),
                cwd=context.cwd,
                expected_branch=context.expected_branch,
                **spawn_defaults,
            )
            self.logger.info(
                'task %s tagged %s — registered planning chat (cwd=%s); '
                'remove the tag to let the agent run autonomously',
                task.id,
                TaskTags.WAIT_PLANNING,
                context.cwd or '?',
            )
        except Exception:
            self.logger.exception(
                'failed to register planning session for task %s', task.id,
            )

    def _move_to_in_progress(self, task: Task) -> None:
        """Best-effort ticket-state move. Failures log but never block the chat."""
        try:
            self._task_state_service.move_task_to_in_progress(task.id)
            self.logger.info(
                'task %s moved to in progress for planning session', task.id,
            )
        except Exception:
            self.logger.exception(
                'failed to move planning task %s to in progress', task.id,
            )

    def _resolve_planning_context(self, task: Task) -> _PlanningContext:
        """Resolve + clone + check-out branches; return ``(cwd, branch)``.

        Best-effort: any failure (no repo match, git fetch error, etc.)
        falls back to a more conservative result so the chat tab still
        opens — the user sees an empty Files / Changes pane and can
        investigate, but the conversation isn't blocked.
        """
        repositories = self._resolve_repositories(task)
        if not repositories:
            return _PlanningContext(cwd='', expected_branch='')
        repositories = self._provision_workspace(task, repositories)
        repositories = self._prepare_repositories(task, repositories)
        if not repositories:
            return _PlanningContext(cwd='', expected_branch='')
        primary = repositories[0]
        cwd = text_from_attr(primary, 'local_path')
        branch_name = self._build_branch_name(task, primary)
        if not branch_name:
            return _PlanningContext(cwd=cwd, expected_branch='')
        if not self._check_out_branches(task, repositories, branch_name):
            return _PlanningContext(cwd=cwd, expected_branch='')
        return _PlanningContext(cwd=cwd, expected_branch=branch_name)

    def _resolve_repositories(self, task: Task) -> list:
        return self._safe_call(
            task,
            'resolve repositories for wait-planning task %s',
            fallback=[],
            action=lambda: list(
                self._repository_service.resolve_task_repositories(task) or [],
            ),
        )

    def _provision_workspace(self, task: Task, repositories: list) -> list:
        # Distinct fallback: if cloning fails we'd rather keep going with
        # the inventory clones than open the chat with no repos at all.
        return self._safe_call(
            task,
            'provision workspace clones for wait-planning task %s; '
            'falling back to inventory clones',
            fallback=repositories,
            action=lambda: provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task,
                repositories,
            ),
        )

    def _prepare_repositories(self, task: Task, repositories: list) -> list:
        return self._safe_call(
            task,
            'prepare repositories for wait-planning task %s',
            fallback=[],
            action=lambda: list(
                self._repository_service.prepare_task_repositories(repositories) or [],
            ),
        )

    def _build_branch_name(self, task: Task, primary_repository) -> str:
        return self._safe_call(
            task,
            'derive branch name for wait-planning task %s',
            fallback='',
            action=lambda: str(
                self._repository_service.build_branch_name(task, primary_repository) or '',
            ).strip(),
        )

    def _safe_call(self, task: Task, action_label: str, *, fallback, action):
        """Run ``action()``; log + return ``fallback`` on any exception.

        Wait-planning is a best-effort flow: every git step has a
        sensible degradation (empty cwd, empty branch name, etc) so the
        chat tab still opens. Centralizing the boilerplate keeps each
        step tiny and readable.
        """
        try:
            return action()
        except Exception:
            self.logger.exception(action_label, task.id)
            return fallback

    def _check_out_branches(
        self,
        task: Task,
        repositories: list,
        branch_name: str,
    ) -> bool:
        repository_branches = {repo.id: branch_name for repo in repositories}
        try:
            self._repository_service.prepare_task_branches(
                repositories, repository_branches,
            )
        except Exception:
            self.logger.exception(
                'failed to check out task branch for wait-planning task %s; '
                'chat will open on whatever branch is currently checked out',
                task.id,
            )
            return False
        return True

    def _session_starter_defaults(self) -> dict[str, object]:
        """Forward the streaming runner's defaults to start_session(...)."""
        runner = self._planning_session_runner
        if runner is None:
            return {}
        defaults = getattr(runner, '_defaults', None)
        if defaults is None:
            return {}
        result: dict[str, object] = {
            field: (getattr(defaults, field, '') or '')
            for field in _SESSION_STRING_FIELDS
        }
        result['max_turns'] = getattr(defaults, 'max_turns', None)
        return result

    @staticmethod
    def _build_planning_prompt(task: Task) -> str:
        """Initial prompt for a wait-planning chat tab.

        Three jobs at once:
          1. Hand Claude the full task description so it has context.
          2. Hard-stop any tool use — wait-planning is **planning only**.
             We have to be explicit because the agent's default behavior
             when handed a task is to start working on it.
          3. Avoid empty stdin (which makes ``claude -p`` exit with an
             error and the scan loop would respawn it forever).
        """
        task_id = text_from_attr(task, 'id')
        summary = text_from_attr(task, 'summary')
        description = text_from_attr(task, 'description')
        header = f'YouTrack ticket {task_id}' if task_id else 'this task'

        sections = [
            f"You're pair-planning with the user on {header}.",
            '',
            '## Task summary',
            summary or '(no summary provided)',
        ]
        if description:
            sections.extend(['', '## Task description', description])
        sections.extend([
            '',
            '## Operating rules — READ CAREFULLY',
            '- This is a **planning-only** session. DO NOT call any tools.',
            '- DO NOT read, edit, write, or run anything.',
            '- DO NOT touch the filesystem, the shell, or the network.',
            '- Your job is to discuss the task with the user, ask clarifying '
            'questions, and help them refine the approach in plain text.',
            '- The user will explicitly tell you when planning is done. Until '
            'then, every reply is a discussion message — no tool calls.',
            '',
            'Briefly acknowledge that you understand and are ready to plan. '
            'Then wait for the user to drive the conversation.',
        ])
        return '\n'.join(sections)
