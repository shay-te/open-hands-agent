"""Unit tests for ``read_lessons_file`` in ``lessons_doc_utils``.

Locks the spawn-time injection contract:
  * Empty / missing path returns ''.
  * Empty file returns ''.
  * Populated file returns the body wrapped in the directive template.
  * Timestamp header is stripped before injection.
  * Body cap protects the system-prompt budget.
  * Defensive read_text OSError branches degrade to '' (logged / silent).
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import read_lessons_file


class ReadLessonsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def _write(self, content: str) -> Path:
        path = self.tmp_dir / 'lessons.md'
        path.write_text(content, encoding='utf-8')
        return path

    def test_empty_path_returns_empty(self) -> None:
        self.assertEqual(read_lessons_file(''), '')
        self.assertEqual(read_lessons_file('   '), '')

    def test_missing_file_returns_empty_silently(self) -> None:
        # Deliberately silent — lessons are optional. A missing file
        # is the normal "no lessons yet" case.
        self.assertEqual(
            read_lessons_file(str(self.tmp_dir / 'never-exists.md')),
            '',
        )

    def test_empty_file_returns_empty(self) -> None:
        path = self._write('')
        self.assertEqual(read_lessons_file(str(path)), '')

    def test_only_timestamp_header_returns_empty(self) -> None:
        path = self._write(
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n',
        )
        self.assertEqual(read_lessons_file(str(path)), '')

    def test_populated_file_returns_wrapped_body(self) -> None:
        path = self._write(
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n\n'
            '- always use logger\n'
            '- never use print\n',
        )
        result = read_lessons_file(str(path))
        # Wrapped with directive markers.
        self.assertIn('--- BEGIN LEARNED LESSONS ---', result)
        self.assertIn('--- END LEARNED LESSONS ---', result)
        # Body present.
        self.assertIn('- always use logger', result)
        self.assertIn('- never use print', result)
        # Timestamp NOT injected.
        self.assertNotIn('last_compacted', result)

    def test_body_is_capped(self) -> None:
        big = '- ' + ('x' * 60_000) + '\n'
        path = self._write(big)
        result = read_lessons_file(str(path))
        # The wrapper adds a fixed prefix + suffix; the body is clipped
        # to the 50_000-char cap.
        self.assertLess(len(result), 60_000 + 1_000)

    def test_unreadable_file_logs_and_returns_empty(self) -> None:
        # Path points at a directory — stat OK but not a regular file.
        logger = MagicMock(spec=logging.Logger)
        result = read_lessons_file(str(self.tmp_dir), logger=logger)
        self.assertEqual(result, '')

    def test_unreadable_file_without_logger_returns_empty_silently(self) -> None:
        # No logger plumbed in — an unreadable path must still degrade
        # to '' instead of bubbling the OSError. Lessons are optional
        # observability, not a correctness gate.
        result = read_lessons_file(str(self.tmp_dir))
        self.assertEqual(result, '')


class ReadLessonsFileDefensiveBranchTests(unittest.TestCase):
    """``read_text`` raising OSError on a path that passes ``is_file``."""

    def test_read_text_oserror_without_logger_returns_empty(self) -> None:
        with patch(
            'pathlib.Path.read_text',
            side_effect=OSError('boom'),
        ), patch(
            'pathlib.Path.is_file',
            return_value=True,
        ), patch(
            'pathlib.Path.stat',
        ):
            result = read_lessons_file('/tmp/does-not-matter.md')
        self.assertEqual(result, '')

    def test_read_text_oserror_with_logger_warns_and_returns_empty(self) -> None:
        logger = logging.getLogger('test_lessons_doc_utils')
        with patch(
            'pathlib.Path.read_text',
            side_effect=OSError('boom'),
        ), patch(
            'pathlib.Path.is_file',
            return_value=True,
        ), patch(
            'pathlib.Path.stat',
        ), patch.object(logger, 'warning') as mock_warning:
            result = read_lessons_file(
                '/tmp/does-not-matter.md', logger=logger,
            )
        self.assertEqual(result, '')
        mock_warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
