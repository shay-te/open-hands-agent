"""Shared subprocess-spawn helpers for the Claude one-shot + streaming paths.

``ClaudeCliClient`` (one-shot ``claude -p``) and ``StreamingClaudeSession``
(long-lived ``claude -p --output-format stream-json``) build nearly the
same argv and the same subprocess environment. The pieces that are
genuinely identical live here so the two stay in lockstep; the bits that
legitimately differ (leading ``-p`` / stream flags, resume-vs-session-id
pinning, the read-only allowlist merge) stay per-class.

No peer core-lib imports at module load: the ``sandbox_core_lib`` calls
are imported lazily inside the functions so the tests that patch
``sandbox_core_lib.sandbox_core_lib.manager.<name>`` /
``...system_prompt.compose_system_prompt`` keep working unchanged.
"""

from __future__ import annotations

import os

from agent_core_lib.agent_core_lib.helpers.text_utils import normalized_text


def build_claude_subprocess_env(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Copy ``os.environ`` and apply the headless-Claude invariant.

    Returns a fresh dict: ``os.environ`` copied, then ``overrides``
    applied (streaming threads per-session env down this way), then
    ``CLAUDE_CODE_NONINTERACTIVE`` set-default'd to ``'1'`` so Claude
    forces JSON to stdout and never blocks on a TTY. ``setdefault``
    runs last so an explicit override of that key is honoured.
    """
    env = os.environ.copy()
    if overrides:
        env.update(overrides)
    env.setdefault('CLAUDE_CODE_NONINTERACTIVE', '1')
    return env


def append_model_effort_flags(
    command: list[str],
    *,
    model: str,
    max_turns: int | None,
    effort: str,
) -> None:
    """Append ``--model`` / ``--max-turns`` / ``--effort`` when set.

    ``max_turns`` is only emitted when present AND positive (the
    one-shot client coerces non-positive values to ``None`` up front,
    so this guard is a no-op there and the real filter for the
    streaming path which accepts a raw int).
    """
    if model:
        command.extend(['--model', model])
    if max_turns is not None and max_turns > 0:
        command.extend(['--max-turns', str(max_turns)])
    if effort:
        command.extend(['--effort', effort])


def build_appended_system_prompt(
    *,
    architecture_doc_path: str,
    lessons_path: str,
    docker_mode_on: bool,
    logger,
) -> str:
    """Compose the single ``--append-system-prompt`` value.

    Joins the architecture doc, learned lessons, and (when docker mode
    is on) the sandbox addendum into one string — the Claude CLI takes
    a single ``--append-system-prompt``. Returns ``''`` when the
    composer produces nothing. Identical wiring for both spawn paths.
    """
    from agent_core_lib.agent_core_lib.helpers.architecture_doc_utils import (
        read_architecture_doc,
    )
    from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import (
        read_lessons_file,
    )
    from sandbox_core_lib.sandbox_core_lib.system_prompt import compose_system_prompt

    architecture_doc = read_architecture_doc(architecture_doc_path, logger=logger)
    lessons_text = read_lessons_file(lessons_path, logger=logger)
    return compose_system_prompt(
        architecture_doc,
        docker_mode_on=docker_mode_on,
        lessons=lessons_text,
    )


def append_additional_dirs(command: list[str], additional_dirs) -> None:
    """Append a ``--add-dir <path>`` pair per non-blank directory."""
    for directory in additional_dirs or []:
        normalized_dir = normalized_text(str(directory))
        if normalized_dir:
            command.extend(['--add-dir', normalized_dir])


def wrap_spawn_for_docker(
    command: list[str],
    *,
    workspace_path: str,
    task_id: str,
    logger,
) -> list[str]:
    """Run the six sandbox pre-spawn steps and return the docker argv.

    Identical containment sequence for both spawn paths: ensure the
    image, rate-check spawns, name the container, refuse committed
    workspace secrets, wrap the command, then audit-log the spawn.
    Each ``SandboxError`` is re-raised as a ``RuntimeError`` with the
    same operator-facing message the inline blocks used.

    Callers compute ``workspace_path`` differently (the one-shot client
    falls back through cwd → repository root → ``os.getcwd()``; the
    streaming session uses its bound cwd), so it is a parameter here.
    The caller is responsible for setting ``spawn_cwd=None`` afterwards
    — the docker WORKDIR is ``/workspace`` and the host cwd is
    irrelevant to the docker client itself.
    """
    from sandbox_core_lib.sandbox_core_lib.manager import (
        SandboxError,
        check_spawn_rate,
        ensure_image,
        enforce_no_workspace_secrets,
        make_container_name,
        record_spawn,
        wrap_command,
    )

    try:
        ensure_image(logger=logger)
    except SandboxError as exc:
        raise RuntimeError(
            f'failed to prepare Claude sandbox image: {exc}',
        ) from exc
    try:
        check_spawn_rate()
    except SandboxError as exc:
        raise RuntimeError(
            f'sandbox spawn rate-limited: {exc}',
        ) from exc
    container_name = make_container_name(task_id)
    try:
        enforce_no_workspace_secrets(workspace_path, logger=logger)
    except SandboxError as exc:
        raise RuntimeError(
            f'sandbox spawn blocked: {exc}',
        ) from exc
    wrapped = wrap_command(
        command,
        workspace_path=workspace_path,
        container_name=container_name,
        task_id=task_id,
    )
    try:
        record_spawn(
            task_id=task_id,
            container_name=container_name,
            workspace_path=workspace_path,
            logger=logger,
        )
    except SandboxError as exc:
        raise RuntimeError(
            f'sandbox audit log required but failed: {exc}',
        ) from exc
    return wrapped
