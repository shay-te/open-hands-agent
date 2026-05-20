"""Tests for ``codex_one_shot`` / ``make_codex_one_shot``.

Mirror of ``claude_core_lib`` one-shot tests, asserting the real
Codex CLI 0.132.0 invocation shape.
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


def _fake_run_writing_last_message(text: str):
    """Patch helper: simulate codex writing ``text`` to the
    ``--output-last-message`` path so the one-shot helper sees it."""
    def fake_run(command, **kwargs):
        try:
            idx = command.index('-o')
            path = command[idx + 1]
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(text)
        except (ValueError, IndexError, OSError):
            pass
        return _completed(stdout='', stderr='', returncode=0)
    return fake_run


class CodexOneShotTests(unittest.TestCase):
    def test_returns_last_message_file_contents(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=_fake_run_writing_last_message('hello back\n'),
        ):
            out = codex_one_shot('say hi')
        self.assertEqual(out, 'hello back\n')

    def test_falls_back_to_stdout_when_last_message_file_is_empty(self) -> None:
        # Codex may exit 0 without writing any message (e.g. agent
        # had nothing to say); the helper falls back to stdout so
        # callers always see something.
        def fake_run(command, **kwargs):
            return _completed(stdout='from stdout', returncode=0)
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=fake_run,
        ):
            out = codex_one_shot('hi')
        self.assertEqual(out, 'from stdout')

    def test_uses_exec_subcommand(self) -> None:
        # Codex's non-interactive entry is ``codex exec``.
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=_fake_run_writing_last_message(''),
        ) as mock_run:
            codex_one_shot('hi')
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[1], 'exec')

    def test_runs_in_read_only_sandbox_with_no_approval(self) -> None:
        # The lessons subsystem wants pure text completion — no edits,
        # no waiting on human input.
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=_fake_run_writing_last_message(''),
        ) as mock_run:
            codex_one_shot('hi')
        cmd = mock_run.call_args[0][0]
        self.assertIn('--sandbox', cmd)
        self.assertIn('read-only', cmd)
        self.assertIn('--ask-for-approval', cmd)
        self.assertIn('never', cmd)

    def test_includes_skip_git_repo_check_and_output_file(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=_fake_run_writing_last_message(''),
        ) as mock_run:
            codex_one_shot('hi')
        cmd = mock_run.call_args[0][0]
        self.assertIn('--skip-git-repo-check', cmd)
        self.assertIn('-o', cmd)

    def test_passes_model_when_set(self) -> None:
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=_fake_run_writing_last_message(''),
        ) as mock_run:
            codex_one_shot('hi', model='gpt-5-codex')
        cmd = mock_run.call_args[0][0]
        self.assertIn('-m', cmd)
        self.assertIn('gpt-5-codex', cmd)

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
        def fake_run(command, **kwargs):
            return _completed(stderr='auth failed', returncode=1)
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=fake_run,
        ):
            with self.assertRaises(CodexOneShotError) as ctx:
                codex_one_shot('hi')
        self.assertIn('auth failed', str(ctx.exception))


class OneShotFileReadEdgeCases(unittest.TestCase):
    """Coverage for the defensive ``except OSError`` branches around
    the ``--output-last-message`` file in ``codex_one_shot``."""

    def test_last_message_file_unreadable_falls_back_to_stdout(self) -> None:
        # subprocess exits cleanly with stdout "fallback text" and we
        # simulate the file being unreadable (e.g. perms denied between
        # write and read).
        def fake_run(command, **kwargs):
            return _completed(stdout='fallback text', returncode=0)

        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=fake_run,
        ), patch('builtins.open', side_effect=OSError('denied')):
            out = codex_one_shot('hi')
        self.assertEqual(out, 'fallback text')

    def test_unlink_oserror_in_finally_is_swallowed(self) -> None:
        # ``os.unlink`` in the finally block must not propagate — the
        # helper's contract is "return the text or raise CodexOneShotError",
        # not "raise OSError from cleanup".
        def fake_run(command, **kwargs):
            try:
                idx = command.index('-o')
                with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                    handle.write('ok')
            except (ValueError, IndexError, OSError):
                pass
            return _completed(stdout='', returncode=0)

        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=fake_run,
        ), patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.os.unlink',
            side_effect=OSError('cleanup failed'),
        ):
            out = codex_one_shot('hi')
        # The text still comes through even though cleanup failed.
        self.assertEqual(out, 'ok')


class MakeCodexOneShotTests(unittest.TestCase):
    def test_closure_passes_fixed_config(self) -> None:
        call = make_codex_one_shot(binary='/opt/codex', model='codex-x', timeout_seconds=30)
        with patch(
            'codex_core_lib.codex_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=_fake_run_writing_last_message('ok'),
        ) as mock_run:
            out = call('hi')
        self.assertEqual(out, 'ok')
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], '/opt/codex')
        self.assertIn('-m', cmd)
        self.assertIn('codex-x', cmd)
        self.assertEqual(kwargs['timeout'], 30)


if __name__ == '__main__':
    unittest.main()
