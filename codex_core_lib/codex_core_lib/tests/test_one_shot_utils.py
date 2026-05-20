"""Tests for ``codex_one_shot`` / ``make_codex_one_shot``.

Mirror of ``test_claude_one_shot_utils`` — same shape, codex binary.
"""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codex_core_lib.codex_core_lib.helpers.one_shot_utils import (
    CodexOneShotError,
    codex_one_shot,
    make_codex_one_shot,
)


def _completed(stdout: str = '', stderr: str = '', returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class CodexOneShotTests(unittest.TestCase):
    def test_returns_stdout_on_success(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_completed(stdout='ok\n'),
        ):
            out = codex_one_shot('say hi')
        self.assertEqual(out, 'ok\n')

    def test_passes_model_when_set(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_completed(stdout='reply'),
        ) as mock_run:
            codex_one_shot('hi', model='codex-mini')
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertIn('--model', cmd)
        self.assertIn('codex-mini', cmd)

    def test_uses_exec_subcommand_not_p_flag(self) -> None:
        # Sanity: codex's non-interactive entry is ``codex exec``, not
        # ``codex -p`` like claude. If a future refactor mixes them up
        # this test fires.
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_completed(stdout=''),
        ) as mock_run:
            codex_one_shot('hi')
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[1], 'exec')

    def test_timeout_raises_codex_one_shot_error(self) -> None:
        timeout_exc = subprocess.TimeoutExpired(cmd='codex', timeout=120)
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=timeout_exc,
        ):
            with self.assertRaises(CodexOneShotError):
                codex_one_shot('hi')

    def test_oserror_raises_codex_one_shot_error(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=OSError('no codex binary'),
        ):
            with self.assertRaises(CodexOneShotError) as ctx:
                codex_one_shot('hi')
        self.assertIn('no codex binary', str(ctx.exception))

    def test_non_zero_exit_raises_with_stderr(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_completed(stderr='auth failed', returncode=1),
        ):
            with self.assertRaises(CodexOneShotError) as ctx:
                codex_one_shot('hi')
        self.assertIn('auth failed', str(ctx.exception))


class MakeCodexOneShotTests(unittest.TestCase):
    def test_closure_passes_fixed_config(self) -> None:
        call = make_codex_one_shot(binary='/opt/codex', model='codex-x', timeout_seconds=30)
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_completed(stdout='ok'),
        ) as mock_run:
            call('hi')
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], '/opt/codex')
        self.assertIn('--model', cmd)
        self.assertIn('codex-x', cmd)
        self.assertEqual(kwargs['timeout'], 30)


if __name__ == '__main__':
    unittest.main()
