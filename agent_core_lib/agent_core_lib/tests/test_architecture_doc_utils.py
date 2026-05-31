"""Unit tests for ``architecture_doc_utils.read_architecture_doc``.

The directive is a fixed-size POINTER at the file (Read-tool
instruction), never the inlined body — the inline-the-doc design
tripped Windows' CreateProcess args limit on large docs.
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.architecture_doc_utils import (
    read_architecture_doc,
)


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
        # The directive points the agent at the file and tells it to
        # ``Read``. The body is NOT inlined.
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, '# Project architecture\n\nLayers ...\n')

        result = read_architecture_doc(str(path))

        self.assertIn(str(path), result)
        self.assertIn('Read tool', result)
        self.assertNotIn('# Project architecture', result)
        self.assertNotIn('Layers ...', result)

    def test_directive_size_is_bounded_regardless_of_file_size(self) -> None:
        # The directive is fixed-size, not the doc size — the core fix
        # for the Windows CreateProcess overflow.
        small = self.tmp_root / 'small.md'
        large = self.tmp_root / 'large.md'
        _write(small, 'x')
        _write(large, 'x' * 5_000_000)  # 5 MB

        small_directive = read_architecture_doc(str(small))
        large_directive = read_architecture_doc(str(large))

        self.assertLess(len(small_directive), 2_000)
        self.assertLess(len(large_directive), 2_000)

    def test_returns_directive_even_for_empty_file(self) -> None:
        # An empty doc still gets the directive — its existence is the
        # only signal in the pointer-only design.
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, '')

        self.assertIn(str(path), read_architecture_doc(str(path)))

    def test_expands_tilde_in_path(self) -> None:
        # ``~/ARCHITECTURE.md`` should resolve to ``$HOME/ARCHITECTURE.md``.
        original_home = os.environ.get('HOME')
        os.environ['HOME'] = str(self.tmp_root)
        self.addCleanup(self._restore_home, original_home)
        _write(self.tmp_root / 'ARCHITECTURE.md', '# tilde-resolved')

        result = read_architecture_doc('~/ARCHITECTURE.md')

        self.assertIn(str(self.tmp_root / 'ARCHITECTURE.md'), result)
        self.assertNotIn('~/', result)

    @staticmethod
    def _restore_home(original_home: str | None) -> None:
        if original_home is None:
            os.environ.pop('HOME', None)
        else:
            os.environ['HOME'] = original_home


if __name__ == '__main__':
    unittest.main()
