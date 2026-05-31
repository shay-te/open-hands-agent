"""End-to-end coverage for the ``KATO_ARCHITECTURE_DOC_PATH`` flow.

Pins down the contract: when an architecture-doc path is configured,
kato appends a short *pointer directive* (~700 chars) to Claude's
system prompt instructing it to ``Read`` the file at the start of
every task. The file body is **not** inlined into the directive —
50K+ docs would push the spawn argv past Windows' CreateProcess
limit (~32K chars). Both the one-shot client (``ClaudeCliClient``,
used by the autonomous backend) and the long-lived streaming
wrapper (``StreamingClaudeSession``, used by planning + chat
respawn) must honor the flag identically so new and resumed
conversations share the same project context.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from claude_core_lib.claude_core_lib.session.streaming import StreamingClaudeSession
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')


class ClaudeCliClientArchitectureDocTests(unittest.TestCase):
    """``ClaudeCliClient`` wires the directive into ``--append-system-prompt``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc_path = Path(self._tmp.name) / 'ARCHITECTURE.md'

    def test_command_includes_only_workspace_addendum_when_no_path_configured(self) -> None:
        # The workspace-scope addendum is always appended (it's not
        # operator-configurable), so the flag is always present even
        # without an arch doc.
        client = ClaudeCliClient(binary='claude')

        cmd = client._build_command(additional_dirs=[], agent_session_id='')

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        self.assertIn('Workspace scope', cmd[index + 1])

    def test_command_includes_only_workspace_addendum_when_doc_file_is_missing(self) -> None:
        # Path set but file doesn't exist on disk: behaves the same as
        # "no path configured" — we don't fail the spawn just because
        # the operator pointed at a doc that hasn't been created yet.
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(additional_dirs=[], agent_session_id='')

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        self.assertIn('Workspace scope', cmd[index + 1])

    def test_command_appends_directive_with_path_when_doc_exists(self) -> None:
        _write(self.doc_path, '# Kato architecture\n\nLayers...')
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(additional_dirs=[], agent_session_id='')

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        prompt = cmd[index + 1]
        # Path is in the directive so Claude knows what to Read.
        self.assertIn(str(self.doc_path), prompt)
        # Doc body is NOT inlined (the whole point of the new contract).
        self.assertNotIn('# Kato architecture', prompt)
        self.assertNotIn('Layers...', prompt)

    def test_smoke_test_command_skips_doc_directive(self) -> None:
        """Boot-time smoke test omits the doc directive — see fix for
        ``[WinError 206]`` on Windows. Real spawns still get it.
        """
        _write(self.doc_path, 'irrelevant')
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(
            additional_dirs=[], agent_session_id='',
            include_system_prompt=False,
        )

        self.assertNotIn('--append-system-prompt', cmd)


class StreamingClaudeSessionArchitectureDocTests(unittest.TestCase):
    """``StreamingClaudeSession`` honors the same flag for live planning sessions."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc_path = Path(self._tmp.name) / 'ARCHITECTURE.md'

    def _build_session(self, **overrides) -> StreamingClaudeSession:
        kwargs = {'task_id': 'PROJ-1', 'binary': 'claude'}
        kwargs.update(overrides)
        return StreamingClaudeSession(**kwargs)

    def test_command_includes_only_workspace_addendum_when_no_path_configured(self) -> None:
        session = self._build_session()

        cmd = session._build_command()

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        self.assertIn('Workspace scope', cmd[index + 1])

    def test_command_includes_only_workspace_addendum_when_doc_file_is_missing(self) -> None:
        session = self._build_session(architecture_doc_path=str(self.doc_path))

        cmd = session._build_command()

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        self.assertIn('Workspace scope', cmd[index + 1])

    def test_command_appends_directive_with_path_when_doc_exists(self) -> None:
        _write(self.doc_path, '# Kato architecture\n\nLayers...')
        session = self._build_session(architecture_doc_path=str(self.doc_path))

        cmd = session._build_command()

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        prompt = cmd[index + 1]
        self.assertIn(str(self.doc_path), prompt)
        # Body is not inlined.
        self.assertNotIn('# Kato architecture', prompt)
        self.assertNotIn('Layers...', prompt)


class ResumedSessionStillReceivesDocTests(unittest.TestCase):
    """Resume + architecture-doc must coexist on the same command line."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc_path = Path(self._tmp.name) / 'ARCHITECTURE.md'
        _write(self.doc_path, 'shared context')

    def test_streaming_session_with_resume_keeps_append_system_prompt(self) -> None:
        session = StreamingClaudeSession(
            task_id='PROJ-1',
            binary='claude',
            architecture_doc_path=str(self.doc_path),
            resume_session_id='abc-123',
        )

        cmd = session._build_command()

        self.assertIn('--append-system-prompt', cmd)
        self.assertIn('--resume', cmd)
        self.assertIn('abc-123', cmd)
        # The directive points at the doc path; the body is not inlined.
        self.assertIn(
            str(self.doc_path),
            cmd[cmd.index('--append-system-prompt') + 1],
        )

    def test_cli_client_with_resume_keeps_append_system_prompt(self) -> None:
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(additional_dirs=[], agent_session_id='abc-123')

        self.assertIn('--append-system-prompt', cmd)
        self.assertIn('--resume', cmd)
        self.assertIn(
            str(self.doc_path),
            cmd[cmd.index('--append-system-prompt') + 1],
        )


class PlanningSessionRunnerArchitectureDocTests(unittest.TestCase):
    """Pin the chat-respawn flow: resume_session_for_chat forwards the doc."""

    def setUp(self) -> None:
        self.session_manager = MagicMock()
        self.defaults = StreamingSessionDefaults(
            binary='claude',
            architecture_doc_path='/path/to/ARCHITECTURE.md',
        )
        self.runner = PlanningSessionRunner(
            session_manager=self.session_manager,
            defaults=self.defaults,
        )

    def test_resume_session_for_chat_passes_architecture_doc_path(self) -> None:
        self.runner.resume_session_for_chat(
            task_id='PROJ-1',
            message='hello',
            cwd='/tmp/repo',
        )

        self.session_manager.start_session.assert_called_once()
        kwargs = self.session_manager.start_session.call_args.kwargs
        self.assertEqual(
            kwargs['architecture_doc_path'], '/path/to/ARCHITECTURE.md',
        )

    def test_resume_session_with_no_doc_path_passes_empty_string(self) -> None:
        runner = PlanningSessionRunner(
            session_manager=self.session_manager,
            defaults=StreamingSessionDefaults(binary='claude'),
        )

        runner.resume_session_for_chat(
            task_id='PROJ-1',
            message='hello',
            cwd='/tmp/repo',
        )

        self.session_manager.start_session.assert_called_once()
        kwargs = self.session_manager.start_session.call_args.kwargs
        self.assertEqual(kwargs['architecture_doc_path'], '')


if __name__ == '__main__':
    unittest.main()
