"""Minimal one-shot Codex CLI invocation.

Mirror of ``claude_core_lib.helpers.one_shot_utils`` — same module
name, same exported names (``codex_one_shot`` ↔ ``claude_one_shot``,
``make_codex_one_shot`` ↔ ``make_claude_one_shot``, ``CodexOneShotError``
↔ ``ClaudeOneShotError``). The function bodies invoke ``codex exec``
instead of ``claude -p`` but the contract (send text, get text back,
raise on failure) is identical so the lessons subsystem can plug in
either backend.
"""

from __future__ import annotations

import subprocess
from typing import Callable


_DEFAULT_TIMEOUT_SECONDS = 120


class CodexOneShotError(RuntimeError):
    """Raised when the one-shot Codex invocation fails or times out."""


def codex_one_shot(
    prompt: str,
    *,
    binary: str = 'codex',
    model: str = '',
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``codex exec`` and return stdout.

    No allowed-tools list, no system prompt, no session id — pure
    text completion. ``model`` is optional; empty leaves Codex on
    its configured default.
    """
    command: list[str] = [binary, 'exec']
    if model:
        command.extend(['--model', model])
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexOneShotError(
            f'codex one-shot did not finish within {timeout_seconds}s'
        ) from exc
    except OSError as exc:
        raise CodexOneShotError(
            f'failed to invoke codex binary "{binary}": {exc}'
        ) from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or '').strip()
        raise CodexOneShotError(
            f'codex one-shot exited {completed.returncode}: {stderr or "<no stderr>"}'
        )
    return completed.stdout or ''


def make_codex_one_shot(
    *,
    binary: str = 'codex',
    model: str = '',
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], str]:
    """Return a closure that calls :func:`codex_one_shot` with fixed config."""
    def _call(prompt: str) -> str:
        return codex_one_shot(
            prompt,
            binary=binary,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    return _call
