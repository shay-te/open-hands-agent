"""Unit tests for ``LessonsDataAccess``.

Locks the file-layout contract:
  * Per-task lessons live at ``state_dir/lessons/<task-id>.md``.
  * Global lesson file lives at ``state_dir/lessons.md``.
  * The global file's first line is the compaction timestamp.
  * Path-traversal characters in task ids are rejected (no escape from
    the per-task dir).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
    strip_timestamp_header,
)


class LessonsDataAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)

    # ----- per-task -----

    def test_write_then_read_per_task_round_trip(self) -> None:
        ok = self.dao.write_per_task('PROJ-1', '- always use logger\n')
        self.assertTrue(ok)
        self.assertEqual(
            self.dao.read_per_task('PROJ-1'),
            '- always use logger\n',
        )

    def test_write_per_task_appends_trailing_newline_if_missing(self) -> None:
        self.dao.write_per_task('PROJ-1', '- one line no newline')
        self.assertTrue(
            (self.state_dir / 'lessons' / 'PROJ-1.md').read_text().endswith('\n'),
        )

    def test_write_per_task_overwrites_existing(self) -> None:
        self.dao.write_per_task('PROJ-1', '- old')
        self.dao.write_per_task('PROJ-1', '- new')
        self.assertEqual(self.dao.read_per_task('PROJ-1'), '- new\n')

    def test_read_per_task_returns_none_when_missing(self) -> None:
        self.assertIsNone(self.dao.read_per_task('NEVER-EXISTED'))

    def test_delete_per_task_removes_file(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        self.dao.delete_per_task('PROJ-1')
        self.assertIsNone(self.dao.read_per_task('PROJ-1'))

    def test_delete_per_task_is_noop_when_missing(self) -> None:
        # Should not raise.
        self.dao.delete_per_task('NEVER-EXISTED')

    def test_list_per_task_ids_returns_sorted(self) -> None:
        self.dao.write_per_task('PROJ-3', '- a')
        self.dao.write_per_task('PROJ-1', '- b')
        self.dao.write_per_task('PROJ-2', '- c')
        self.assertEqual(
            self.dao.list_per_task_ids(),
            ['PROJ-1', 'PROJ-2', 'PROJ-3'],
        )

    def test_list_per_task_ids_empty_when_dir_missing(self) -> None:
        self.assertEqual(self.dao.list_per_task_ids(), [])

    def test_read_all_per_task_returns_dict(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        self.dao.write_per_task('PROJ-2', '- b')
        all_lessons = self.dao.read_all_per_task()
        self.assertEqual(set(all_lessons.keys()), {'PROJ-1', 'PROJ-2'})
        self.assertEqual(all_lessons['PROJ-1'], '- a\n')

    def test_list_per_task_ids_skips_non_md_files_and_subdirectories(self) -> None:
        # Branch 171->170: ``if entry.is_file() and entry.suffix == '.md':``
        # false branch — non-md files and subdirectories must be
        # silently skipped, not appended to the result list.
        self.dao.write_per_task('PROJ-1', '- a')
        per_task_dir = self.state_dir / 'lessons'
        # A stray non-md file (e.g. a backup or unrelated artifact).
        (per_task_dir / 'README.txt').write_text('noise', encoding='utf-8')
        # A stray subdirectory (e.g. an attic for old lessons).
        (per_task_dir / 'archive').mkdir()

        self.assertEqual(self.dao.list_per_task_ids(), ['PROJ-1'])

    def test_read_all_per_task_skips_entries_whose_content_is_none(self) -> None:
        # Branch 180->178: ``if content is not None:`` false branch —
        # when ``read_per_task`` returns None (e.g. file disappeared
        # between the listing and the read), the task id must be
        # silently skipped in the resulting dict.
        self.dao.write_per_task('PROJ-1', '- a')
        self.dao.write_per_task('PROJ-2', '- b')

        original_read_per_task = self.dao.read_per_task

        def _fake_read(task_id: str):
            if task_id == 'PROJ-1':
                return None
            return original_read_per_task(task_id)

        self.dao.read_per_task = _fake_read  # type: ignore[method-assign]

        result = self.dao.read_all_per_task()
        self.assertEqual(set(result.keys()), {'PROJ-2'})
        self.assertEqual(result['PROJ-2'], '- b\n')

    def test_path_traversal_task_id_is_rejected(self) -> None:
        # Forbidden characters: /, \, .., null. None of these may be
        # used to escape the per-task directory.
        for bad in ('../escape', '/etc/passwd', 'a\\b', 'a\x00b', '.', '..'):
            ok = self.dao.write_per_task(bad, '- malicious')
            self.assertFalse(ok, f'should reject task id {bad!r}')
        self.assertEqual(self.dao.list_per_task_ids(), [])

    def test_empty_or_blank_task_id_is_rejected(self) -> None:
        self.assertFalse(self.dao.write_per_task('', '- a'))
        self.assertFalse(self.dao.write_per_task('   ', '- a'))
        self.assertIsNone(self.dao.read_per_task(''))

    # ----- global -----

    def test_write_then_read_global_round_trip(self) -> None:
        ok = self.dao.write_global('- core lesson 1\n- core lesson 2')
        self.assertTrue(ok)
        body = self.dao.read_global_body()
        self.assertIn('- core lesson 1', body)
        self.assertIn('- core lesson 2', body)

    def test_write_global_prepends_timestamp_header(self) -> None:
        fixed = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        self.dao.write_global('- a', compacted_at=fixed)
        raw = self.dao.read_global()
        self.assertTrue(raw.startswith('<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->'))

    def test_write_global_strips_existing_header_in_input(self) -> None:
        # If a caller passes content that ALREADY has a header (e.g. they
        # passed the raw read), we strip it before writing the new one.
        # Otherwise we'd end up with two headers.
        self.dao.write_global(
            '<!-- last_compacted: 2025-01-01T00:00:00+00:00 -->\n\n- a',
        )
        raw = self.dao.read_global()
        # Exactly one header line.
        self.assertEqual(raw.count('<!-- last_compacted:'), 1)

    def test_last_compacted_at_parses_header(self) -> None:
        fixed = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        self.dao.write_global('- a', compacted_at=fixed)
        parsed = self.dao.last_compacted_at()
        self.assertEqual(parsed, fixed)

    def test_last_compacted_at_none_when_file_missing(self) -> None:
        self.assertIsNone(self.dao.last_compacted_at())

    def test_last_compacted_at_none_when_header_absent(self) -> None:
        (self.state_dir / 'lessons.md').write_text(
            '- bare lesson without header\n', encoding='utf-8',
        )
        self.assertIsNone(self.dao.last_compacted_at())

    def test_read_global_body_strips_header(self) -> None:
        fixed = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        self.dao.write_global('- core 1\n- core 2', compacted_at=fixed)
        body = self.dao.read_global_body()
        self.assertNotIn('last_compacted', body)
        self.assertIn('- core 1', body)


class StripTimestampHeaderTests(unittest.TestCase):
    def test_strips_when_present(self) -> None:
        text = (
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n\n- a\n'
        )
        # ``splitlines`` drops the trailing newline; that's fine for
        # body text destined for system-prompt injection.
        self.assertEqual(strip_timestamp_header(text), '- a')

    def test_passes_through_when_absent(self) -> None:
        self.assertEqual(strip_timestamp_header('- a\n'), '- a\n')

    def test_empty_input(self) -> None:
        self.assertEqual(strip_timestamp_header(''), '')

    def test_only_header(self) -> None:
        self.assertEqual(
            strip_timestamp_header('<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->'),
            '',
        )


if __name__ == '__main__':
    unittest.main()
