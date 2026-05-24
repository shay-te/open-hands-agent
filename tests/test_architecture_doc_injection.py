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

import logging
import os
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
from kato_core_lib.helpers.architecture_doc_utils import read_architecture_doc


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')


class ReadArchitectureDocTests(unittest.TestCase):
    """Unit-level coverage for the directive-builder helper."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def test_returns_empty_when_path_is_blank(self) -> None:
        self.assertEqual(read_architecture_doc(''), '')
        self.assertEqual(read_architecture_doc('   '), '')

    def test_returns_empty_when_file_missing_and_warns(self) -> None:
        logger = logging.getLogger('test-arch-missing')
        with self.assertLogs(logger, level='WARNING') as captured:
            result = read_architecture_doc(
                str(self.tmp_root / 'does-not-exist.md'),
                logger=logger,
            )
        self.assertEqual(result, '')
        self.assertTrue(
            any('not a file' in record.getMessage() for record in captured.records),
            'expected a "not a file" warning in the log output',
        )

    def test_returns_empty_when_path_is_a_directory_and_warns(self) -> None:
        logger = logging.getLogger('test-arch-dir')
        with self.assertLogs(logger, level='WARNING'):
            result = read_architecture_doc(str(self.tmp_root), logger=logger)
        self.assertEqual(result, '')

    def test_directive_includes_path_and_read_tool_instruction(self) -> None:
        """The directive points Claude at the file and tells it to ``Read``.

        File body is NOT inlined — the prior contract (inlining the
        whole doc into ``--append-system-prompt``) tripped Windows'
        CreateProcess args limit on docs > ~30 KB.
        """
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, '# Kato architecture\n\nLayers ...\n')

        result = read_architecture_doc(str(path))

        self.assertIn(str(path), result)
        self.assertIn('Read tool', result)
        # The body must NOT be in the directive.
        self.assertNotIn('# Kato architecture', result)
        self.assertNotIn('Layers ...', result)

    def test_directive_size_is_bounded_regardless_of_file_size(self) -> None:
        """The directive is fixed-size (~700 chars), not the doc size.

        This is the core fix for the Windows CreateProcess overflow:
        a 5 MB architecture doc and a 5 KB one produce the same-sized
        ``--append-system-prompt`` value.
        """
        small = self.tmp_root / 'small.md'
        large = self.tmp_root / 'large.md'
        _write(small, 'x')
        _write(large, 'x' * 5_000_000)  # 5 MB

        small_directive = read_architecture_doc(str(small))
        large_directive = read_architecture_doc(str(large))

        # Path differs, total length differs by ~5 chars (filename
        # difference). Both stay under 2 KB regardless of file size.
        self.assertLess(len(small_directive), 2_000)
        self.assertLess(len(large_directive), 2_000)

    def test_returns_directive_even_for_empty_file(self) -> None:
        """An empty doc still gets the directive — it exists, that's enough.

        The body-trimming check that returned '' on empty content
        was for the inline-the-body design; with the pointer-only
        design, the file's existence is the only signal.
        """
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, '')

        self.assertIn(str(path), read_architecture_doc(str(path)))

    def test_expands_tilde_in_path(self) -> None:
        # ``~/ARCHITECTURE.md`` should resolve to ``$HOME/ARCHITECTURE.md``.
        # Operators commonly drop the doc in their home directory and
        # tilde-expansion is the obvious way to point at it without
        # baking an absolute path into ``.env``.
        original_home = os.environ.get('HOME')
        os.environ['HOME'] = str(self.tmp_root)
        self.addCleanup(self._restore_home, original_home)
        _write(self.tmp_root / 'ARCHITECTURE.md', '# tilde-resolved')

        result = read_architecture_doc('~/ARCHITECTURE.md')

        # Tilde was expanded — the directive's path includes the real
        # tmp_root, not the literal '~'.
        self.assertIn(str(self.tmp_root / 'ARCHITECTURE.md'), result)
        self.assertNotIn('~/', result)

    @staticmethod
    def _restore_home(original_home: str | None) -> None:
        if original_home is None:
            os.environ.pop('HOME', None)
        else:
            os.environ['HOME'] = original_home


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
