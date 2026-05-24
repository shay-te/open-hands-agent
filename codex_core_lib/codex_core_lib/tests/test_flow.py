"""End-to-end flow test for ``codex_core_lib``.

Mirror of ``claude_core_lib/tests/test_flow.py`` — exercises the
factory → ``CodexCliClient`` → orchestration handoff with mocked
subprocess calls, so the test stays hermetic without a real
``codex`` binary on PATH.

Codex's output channel is split between JSONL events (stdout) and
the ``--output-last-message`` file, so the mock writes BOTH paths
the same way the real CLI does.
"""

from __future__ import annotations

import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.agent_core_lib import AgentCoreLib
from agent_core_lib.agent_core_lib.client.agent_client_factory import resolve_platform
from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.platform import AgentPlatform
from codex_core_lib.codex_core_lib.cli_client import CodexCliClient


def _codex_open_cfg() -> SimpleNamespace:
    codex_cfg = SimpleNamespace(
        binary='codex',
        model='gpt-5-codex',
        max_turns=None,           # codex has no per-spawn turn cap
        effort='',                # codex routes effort via config.toml
        allowed_tools='',         # codex has no allow-tools flag
        disallowed_tools='',      # codex has no deny-tools flag
        bypass_permissions=False,
        timeout_seconds=900,
        model_smoke_test_enabled=False,
        architecture_doc_path='',
        lessons_path='',
    )
    return SimpleNamespace(codex=codex_cfg, repository_root_path='/repos')


def _completed(stdout: str = '', stderr: str = '', returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _fake_codex_run(*, jsonl: str = '', last_message: str = '',
                    stderr: str = '', returncode: int = 0):
    """Mock subprocess.run that writes ``last_message`` to the
    ``--output-last-message`` path and returns ``jsonl`` on stdout —
    matching what the real codex CLI does."""

    def fake_run(command, **kwargs):
        try:
            idx = command.index('-o')
            path = command[idx + 1]
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(last_message)
        except (ValueError, IndexError, OSError):
            pass
        return _completed(stdout=jsonl, stderr=stderr, returncode=returncode)

    return fake_run


def _task(task_id: str = 'PROJ-1') -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        summary='Add feature',
        description='Description.',
        branch_name=f'feature/{task_id.lower()}',
        repository_branches={},
        repositories=[],
    )


class CodexFlowTests(unittest.TestCase):
    """resolve → build → use, end to end with mocked subprocess."""

    def test_resolve_platform_recognises_codex(self) -> None:
        self.assertEqual(resolve_platform('codex'), AgentPlatform.CODEX)
        for alias in ('codex-cli', 'codex_cli', 'openai-codex', 'openai_codex'):
            self.assertEqual(resolve_platform(alias), AgentPlatform.CODEX, alias)

    def test_agent_core_lib_builds_a_codex_backend(self) -> None:
        lib = AgentCoreLib(
            platform=AgentPlatform.CODEX,
            cfg=_codex_open_cfg(),
            max_retries=1,
            testing=True,
        )
        self.assertIsInstance(lib.agent, CodexCliClient)

    def test_full_implement_task_round_trip(self) -> None:
        lib = AgentCoreLib(
            platform=AgentPlatform.CODEX,
            cfg=_codex_open_cfg(),
            max_retries=1,
            testing=True,
        )
        # Real codex 0.132 JSONL shape: thread_id on a thread.started
        # event, plus the agent reply written to the file passed via
        # ``--output-last-message``.
        jsonl = (
            '{"type":"thread.started","thread_id":"sess-99"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"done editing"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}\n'
        )
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=_fake_codex_run(jsonl=jsonl, last_message='done editing'),
        ):
            result = lib.agent.implement_task(_task())

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'done editing')
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'sess-99')

    def test_resume_uses_subcommand_form_in_the_argv(self) -> None:
        lib = AgentCoreLib(
            platform=AgentPlatform.CODEX,
            cfg=_codex_open_cfg(),
            max_retries=1,
            testing=True,
        )
        seen: list[list[str]] = []

        def capture(command, **kwargs):
            seen.append(list(command))
            try:
                idx = command.index('-o')
                with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                    handle.write('ok')
            except (ValueError, IndexError, OSError):
                pass
            return _completed(returncode=0)

        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=capture,
        ):
            lib.agent.implement_task(_task(), agent_session_id='resume-me')
        self.assertTrue(seen)
        cmd = seen[0]
        # Subcommand form, NOT a --resume flag.
        self.assertNotIn('--resume', cmd)
        self.assertIn('resume', cmd[:5])
        self.assertIn('resume-me', cmd)

    def test_round_trip_propagates_subprocess_failure(self) -> None:
        lib = AgentCoreLib(
            platform=AgentPlatform.CODEX,
            cfg=_codex_open_cfg(),
            max_retries=1,
            testing=True,
        )
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=_fake_codex_run(stderr='auth required', returncode=1),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                lib.agent.implement_task(_task())
        self.assertIn('auth required', str(ctx.exception))

    def test_jsonl_error_event_raises_clear_runtime_error(self) -> None:
        lib = AgentCoreLib(
            platform=AgentPlatform.CODEX,
            cfg=_codex_open_cfg(),
            max_retries=1,
            testing=True,
        )
        # Heuristic match: any event whose ``type`` contains
        # ``error`` or ``fail`` flips is_error in the parser.
        # ``task_failed`` is what kato assumes today; a concrete
        # codex error event hasn't been observed in the wild yet.
        jsonl = '{"type":"task_failed","message":"rate limit"}\n'
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=_fake_codex_run(jsonl=jsonl, returncode=0),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                lib.agent.implement_task(_task())
        self.assertIn('rate limit', str(ctx.exception))

    def test_missing_codex_block_raises_clear_error(self) -> None:
        cfg = types.SimpleNamespace()  # no .codex
        with self.assertRaises(RuntimeError) as ctx:
            AgentCoreLib(
                platform=AgentPlatform.CODEX,
                cfg=cfg,
                max_retries=1,
                testing=True,
            )
        self.assertIn('codex', str(ctx.exception).lower())


if __name__ == '__main__':
    unittest.main()
