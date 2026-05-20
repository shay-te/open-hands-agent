"""Minimal one-shot Codex CLI invocation.

Mirror of ``claude_core_lib.helpers.one_shot_utils`` — same module
name, same exported names (``codex_one_shot`` ↔ ``claude_one_shot``,
``make_codex_one_shot`` ↔ ``make_claude_one_shot``, ``CodexOneShotError``
↔ ``ClaudeOneShotError``).

The lessons subsystem just needs "send text, get text back" — no
tools, no streaming, no session state. We invoke ``codex exec``
with the prompt on stdin and use ``--output-last-message`` to
capture the agent's final reply cleanly (parsing the ``--json``
JSONL event stream for "the last agent_message" would tie us to
event names that aren't part of the CLI's public contract).

Verified against ``codex-cli 0.132.0``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
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
    """Send ``prompt`` to ``codex exec`` and return the agent's final text.

    No tools, no system prompt, no session id — pure text completion.
    ``model`` is optional; empty leaves Codex on whatever the
    operator's ``~/.codex/config.toml`` declares as the default.
    """
    fd, last_message_file = tempfile.mkstemp(prefix='kato-codex-oneshot-', suffix='.txt')
    os.close(fd)
    try:
        command: list[str] = [
            binary, 'exec',
            # ``read-only`` sandbox + ``never`` approval so the
            # one-shot path can't make accidental edits and never
            # blocks waiting for human input.
            '--sandbox', 'read-only',
            '--ask-for-approval', 'never',
            '--skip-git-repo-check',
            '-o', last_message_file,
        ]
        if model:
            command.extend(['-m', model])
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
        # Prefer the file the CLI wrote. Fallback to stdout for the
        # rare case where ``-o`` produced nothing (e.g. agent had no
        # reply text), so callers always see *something*.
        try:
            with open(last_message_file, 'r', encoding='utf-8') as handle:
                final_message = handle.read()
        except OSError:
            final_message = ''
        if final_message:
            return final_message
        return completed.stdout or ''
    finally:
        try:
            os.unlink(last_message_file)
        except OSError:
            pass


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
