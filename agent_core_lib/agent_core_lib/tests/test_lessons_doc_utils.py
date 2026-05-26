"""Tests for ``read_lessons_file`` in ``lessons_doc_utils``.

Focused on the small defensive branches that the main coverage path
doesn't exercise (e.g. OSError during ``read_text`` when no logger
was supplied).
"""
from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import (
    read_lessons_file,
)


class ReadLessonsFileTests(unittest.TestCase):
    def test_read_text_oserror_without_logger_returns_empty(self) -> None:
        # Branch 54->56: ``read_text`` raises OSError and ``logger`` is
        # None — skip the warning call, return '' silently.
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
        # Cover the alternate branch (54->55) so this test file stands
        # on its own without relying on other suites.
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
