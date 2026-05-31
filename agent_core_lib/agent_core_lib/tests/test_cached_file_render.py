"""Tests for ``cached_file_render`` — the shared mtime+size file cache.

Both the architecture-doc and lessons readers route through this, so
every behaviour they rely on is pinned here: non-file short-circuits,
compute-once on a cache hit, mtime/size invalidation, distinct-path
isolation, and the "don't cache an empty render" rule.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_core_lib.agent_core_lib.helpers.cached_file_render import cached_file_render


class CachedFileRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.compute_calls: list[Path] = []

        def _render(file_path: Path) -> str:
            self.compute_calls.append(file_path)
            return file_path.read_text(encoding='utf-8').upper()

        self._render = _render

    def _write(self, name: str, body: str) -> Path:
        path = self.tmpdir / name
        path.write_text(body, encoding='utf-8')
        return path

    def test_returns_empty_for_blank_path_without_rendering(self) -> None:
        self.assertEqual(cached_file_render('', self._render), '')
        self.assertEqual(cached_file_render('   ', self._render), '')
        self.assertEqual(self.compute_calls, [])

    def test_returns_empty_for_missing_file(self) -> None:
        self.assertEqual(
            cached_file_render(str(self.tmpdir / 'nope.txt'), self._render), '',
        )
        self.assertEqual(self.compute_calls, [])

    def test_returns_empty_for_directory_path(self) -> None:
        self.assertEqual(cached_file_render(str(self.tmpdir), self._render), '')
        self.assertEqual(self.compute_calls, [])

    def test_warns_on_non_file_when_message_and_logger_given(self) -> None:
        logger = MagicMock(spec=logging.Logger)
        cached_file_render(
            str(self.tmpdir / 'nope.txt'), self._render,
            logger=logger, stat_error_message='%s is not a file',
        )
        logger.warning.assert_called_once()

    def test_first_call_invokes_renderer(self) -> None:
        target = self._write('x.txt', 'hello')
        self.assertEqual(cached_file_render(str(target), self._render), 'HELLO')
        self.assertEqual(len(self.compute_calls), 1)

    def test_unchanged_file_skips_render_on_subsequent_calls(self) -> None:
        target = self._write('x.txt', 'hello')
        cached_file_render(str(target), self._render)
        cached_file_render(str(target), self._render)
        cached_file_render(str(target), self._render)
        self.assertEqual(len(self.compute_calls), 1)

    def test_size_change_invalidates_cache(self) -> None:
        target = self._write('x.txt', 'hello')
        cached_file_render(str(target), self._render)
        self._write('x.txt', 'hello world')
        self.assertEqual(
            cached_file_render(str(target), self._render), 'HELLO WORLD',
        )
        self.assertEqual(len(self.compute_calls), 2)

    def test_mtime_change_invalidates_cache_even_when_size_unchanged(self) -> None:
        target = self._write('x.txt', 'hello')
        cached_file_render(str(target), self._render)
        # Same length, different bytes — bump mtime so the change is
        # visible regardless of filesystem timestamp resolution.
        self._write('x.txt', 'world')
        future = time.time() + 5
        os.utime(target, (future, future))
        self.assertEqual(cached_file_render(str(target), self._render), 'WORLD')
        self.assertEqual(len(self.compute_calls), 2)

    def test_distinct_paths_have_distinct_cache_entries(self) -> None:
        a = self._write('a.txt', 'aaa')
        b = self._write('b.txt', 'bbb')
        self.assertEqual(cached_file_render(str(a), self._render), 'AAA')
        self.assertEqual(cached_file_render(str(b), self._render), 'BBB')
        # Second hit on each is a cache hit.
        cached_file_render(str(a), self._render)
        cached_file_render(str(b), self._render)
        self.assertEqual(len(self.compute_calls), 2)

    def test_empty_render_is_not_cached(self) -> None:
        # A renderer that returns '' must NOT be cached — the next call
        # re-checks the file (so an empty/unreadable body re-renders
        # once it gets content).
        target = self._write('x.txt', 'whatever')
        empty_calls: list[Path] = []

        def _empty(file_path: Path) -> str:
            empty_calls.append(file_path)
            return ''

        self.assertEqual(cached_file_render(str(target), _empty), '')
        self.assertEqual(cached_file_render(str(target), _empty), '')
        self.assertEqual(len(empty_calls), 2)


if __name__ == '__main__':
    unittest.main()
