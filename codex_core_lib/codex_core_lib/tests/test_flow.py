"""End-to-end flow test for ``codex_core_lib``.

Mirror of ``claude_core_lib/tests/test_flow.py`` — exercises the
factory → ``CodexCliClient`` → orchestration handoff with mocked
subprocess calls, so the test stays hermetic without a real
``codex`` binary on PATH.
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
        model='codex-mini',
        max_turns=10,
        effort='medium',
        allowed_tools='',
        disallowed_tools='',
        bypass_permissions=False,
        timeout_seconds=900,
        model_smoke_test_enabled=False,
        architecture_doc_path='',
        lessons_path='',
    )
    return SimpleNamespace(codex=codex_cfg, repository_root_path='/repos')


def _completed(stdout: str = '', stderr: str = '', returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


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
        # Aliases all collapse to the same enum.
        for alias in ('codex-cli', 'codex_cli', 'openai-codex', 'openai_codex'):
            self.assertEqual(resolve_platform(alias), AgentPlatform.CODEX, alias)

    def test_agent_core_lib_builds_a_codex_backend(self) -> None:
        # Build through the canonical composition root — no shortcuts.
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
        # Codex emits a JSON object on stdout with ``result`` +
        # ``session_id`` + ``success`` — same shape as Claude.
        payload = '{"result": "done editing", "session_id": "sess-99", "success": true}'
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            return_value=_completed(stdout=payload, returncode=0),
        ):
            result = lib.agent.implement_task(_task())

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'done editing')
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'sess-99')

    def test_round_trip_propagates_subprocess_failure(self) -> None:
        lib = AgentCoreLib(
            platform=AgentPlatform.CODEX,
            cfg=_codex_open_cfg(),
            max_retries=1,
            testing=True,
        )
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            return_value=_completed(stderr='auth required', returncode=1),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                lib.agent.implement_task(_task())
        self.assertIn('auth required', str(ctx.exception))

    def test_missing_codex_block_raises_clear_error(self) -> None:
        # Same operator-actionable RuntimeError the Claude side emits
        # when its config block is absent.
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
