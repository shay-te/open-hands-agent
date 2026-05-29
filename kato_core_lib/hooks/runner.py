"""Fire configured hooks at the right lifecycle points.

The runner is given a ``HookConfig`` at construction and exposes
one method per lifecycle point. Each method:

  1. Filters the configured hooks for that point by their ``match``
     predicates against the event payload.
  2. For each surviving hook, runs the shell command via
     ``subprocess.run`` with the event JSON piped on stdin and
     placeholder substitution applied to the command string.
  3. Captures return code + stdout/stderr; returns a list of
     ``HookResult`` records so the caller can inspect them.

For ``pre_tool_use`` only: if any hook exits non-zero, the runner
returns a result with ``blocked=True``. The caller (kato's permission
flow) is expected to treat that as DENY for the tool call.

Hooks NEVER crash the caller. A failure to spawn the shell, a
timeout, or a placeholder-substitution error is captured and logged
as a HookResult; the rest of kato keeps running.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass

from kato_core_lib.hooks.config import (
    HookConfig,
    HookDefinition,
    HookPoint,
)


@dataclass(frozen=True)
class HookResult(object):
    """Outcome of one hook firing.

    ``ok`` is True when the shell command completed with exit code
    zero. ``blocked`` is True only for ``pre_tool_use`` hooks that
    exited non-zero (signalling "block this tool call").
    """

    point: HookPoint
    command: str
    ok: bool
    blocked: bool
    returncode: int | None
    stdout: str
    stderr: str
    error: str = ''


# Placeholder syntax: ``${name}``. Only alphanumeric + underscore
# inside the braces. Missing keys are replaced with an empty
# string so a hook that references ``${file_path}`` for a
# session_start event (no file path) doesn't crash.
_PLACEHOLDER_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


def _substitute(command: str, event: dict) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1)
        value = event.get(key, '')
        return '' if value is None else str(value)
    return _PLACEHOLDER_RE.sub(repl, command)


class HookRunner(object):
    """Fires hooks at lifecycle points.

    Stateless beyond the immutable ``HookConfig``; safe to share
    across threads.
    """

    def __init__(
        self,
        config: HookConfig,
        *,
        logger: logging.Logger | None = None,
        subprocess_run=subprocess.run,
    ) -> None:
        # ``subprocess_run`` is injectable for tests so we don't
        # actually spawn shells.
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._run = subprocess_run

    def fire(self, point: HookPoint, event: dict) -> list[HookResult]:
        """Fire every hook at ``point`` whose match predicate is
        satisfied by ``event``. Returns one HookResult per fired
        hook.
        """
        hooks = self._config.for_point(point)
        if not hooks:
            return []
        results: list[HookResult] = []
        for hook in hooks:
            if not hook.matches(event):
                continue
            results.append(self._run_one(hook, event))
        return results

    def is_blocked(self, results) -> bool:
        """Convenience: True when any hook in ``results`` blocked
        the operation. Only ``pre_tool_use`` hooks produce
        ``blocked=True``.
        """
        return any(getattr(r, 'blocked', False) for r in results)

    @staticmethod
    def _error_result(
        hook: HookDefinition,
        error: str,
        *,
        blocked: bool,
    ) -> HookResult:
        """Build a failed (non-spawned / aborted) HookResult.

        Shared by every ``_run_one`` failure path — they all report
        ``ok=False`` with no return code or captured output, differing
        only in the ``error`` text and whether the failure ``blocked``.
        """
        return HookResult(
            point=hook.point,
            command=hook.command,
            ok=False,
            blocked=blocked,
            returncode=None,
            stdout='', stderr='',
            error=error,
        )

    def _run_one(self, hook: HookDefinition, event: dict) -> HookResult:
        try:
            rendered = _substitute(hook.command, event)
        except Exception as exc:  # pragma: no cover - defensive
            return self._error_result(
                hook,
                f'placeholder substitution failed: {exc}',
                blocked=False,
            )
        # Pipe the event as JSON on stdin so structured hooks can
        # parse it. ``default=str`` so anything non-JSON-native
        # (e.g. paths) round-trips through stringification.
        stdin_payload = json.dumps(event, default=str)
        try:
            completed = self._run(
                ['/bin/sh', '-c', rendered],
                input=stdin_payload,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=hook.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._logger.warning(
                'kato hook timed out: point=%s command=%r timeout=%ss',
                hook.point.value, hook.command, hook.timeout_seconds,
            )
            return self._error_result(
                hook,
                f'timed out after {hook.timeout_seconds}s',
                blocked=hook.point == HookPoint.PRE_TOOL_USE,
            )
        except OSError as exc:
            self._logger.warning(
                'kato hook could not spawn: point=%s command=%r error=%s',
                hook.point.value, hook.command, exc,
            )
            # Fail-safe for pre_tool_use: if we can't even spawn the
            # hook, we don't know what its decision would have been.
            # Default to BLOCK so the operator notices a misconfigured
            # hook rather than silently bypassing the guard they set up.
            return self._error_result(
                hook,
                f'spawn failed: {exc}',
                blocked=hook.point == HookPoint.PRE_TOOL_USE,
            )
        ok = completed.returncode == 0
        return HookResult(
            point=hook.point,
            command=hook.command,
            ok=ok,
            # Non-zero exit on pre_tool_use is the documented "block"
            # signal. Other points capture the return code for logs
            # but never block.
            blocked=(not ok) and hook.point == HookPoint.PRE_TOOL_USE,
            returncode=completed.returncode,
            stdout=completed.stdout or '',
            stderr=completed.stderr or '',
        )
