"""Tests for the lessons-path resolver — the wiring that decides whether the
agent actually sees the lessons kato captures.

The regression these pin: the lesson WRITER (LessonsService/LessonsDataAccess)
and the lesson READER (the agent client, which reads ``claude.lessons_path``)
must resolve to the SAME file. They used to diverge when ``KATO_LESSONS_PATH``
was unset — the writer defaulted to ``~/.kato/lessons.md`` while the reader got
'' and read nothing, so the agent "learned nothing".
"""
from pathlib import Path
from types import SimpleNamespace
import unittest

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
)
from kato_core_lib.helpers.lessons_path_utils import (
    DEFAULT_LESSONS_PATH,
    resolve_and_sync_lessons_path,
    resolve_lessons_path,
)


class ResolveLessonsPathTests(unittest.TestCase):
    def test_none_config_uses_default(self):
        self.assertEqual(resolve_lessons_path(None), DEFAULT_LESSONS_PATH)

    def test_empty_lessons_path_uses_default(self):
        self.assertEqual(resolve_lessons_path(SimpleNamespace(lessons_path='')), DEFAULT_LESSONS_PATH)

    def test_whitespace_lessons_path_uses_default(self):
        self.assertEqual(resolve_lessons_path(SimpleNamespace(lessons_path='   ')), DEFAULT_LESSONS_PATH)

    def test_missing_attr_uses_default(self):
        self.assertEqual(resolve_lessons_path(SimpleNamespace()), DEFAULT_LESSONS_PATH)

    def test_explicit_path_is_used(self):
        cfg = SimpleNamespace(lessons_path='/srv/state/lessons.md')
        self.assertEqual(resolve_lessons_path(cfg), Path('/srv/state/lessons.md'))

    def test_tilde_is_expanded(self):
        cfg = SimpleNamespace(lessons_path='~/custom/lessons.md')
        self.assertEqual(resolve_lessons_path(cfg), Path.home() / 'custom' / 'lessons.md')

    def test_default_is_under_kato_home(self):
        self.assertEqual(DEFAULT_LESSONS_PATH, Path.home() / '.kato' / 'lessons.md')


class ResolveAndSyncTests(unittest.TestCase):
    def test_sync_writes_resolved_default_back_into_config(self):
        cfg = SimpleNamespace(lessons_path='')
        path = resolve_and_sync_lessons_path(cfg)
        self.assertEqual(path, DEFAULT_LESSONS_PATH)
        # The reader (agent client) reads cfg.lessons_path — it now holds the
        # resolved absolute path instead of ''.
        self.assertEqual(cfg.lessons_path, str(DEFAULT_LESSONS_PATH))

    def test_sync_writes_expanded_explicit_path_back(self):
        cfg = SimpleNamespace(lessons_path='~/custom/lessons.md')
        path = resolve_and_sync_lessons_path(cfg)
        self.assertEqual(cfg.lessons_path, str(Path.home() / 'custom' / 'lessons.md'))
        self.assertEqual(path, Path.home() / 'custom' / 'lessons.md')

    def test_none_config_does_not_crash(self):
        self.assertEqual(resolve_and_sync_lessons_path(None), DEFAULT_LESSONS_PATH)

    def test_read_only_config_is_tolerated(self):
        class ReadOnlyCfg:
            lessons_path = '/srv/lessons.md'

            def __setattr__(self, name, value):
                raise AttributeError('read only')

        cfg = ReadOnlyCfg()
        # Resolves fine; the write-back is swallowed (config keeps its value).
        path = resolve_and_sync_lessons_path(cfg)
        self.assertEqual(path, Path('/srv/lessons.md'))
        self.assertEqual(cfg.lessons_path, '/srv/lessons.md')


class WriterReaderAgreeTests(unittest.TestCase):
    """The core regression: the file the writer writes IS the file the reader
    reads — for both the unconfigured default and an explicit path."""

    def _assert_agree(self, cfg):
        # Reader side: the agent client reads cfg.lessons_path after sync.
        resolved = resolve_and_sync_lessons_path(cfg)
        # Writer side: LessonsDataAccess writes the global to <state_dir>/lessons.md.
        writer_global = LessonsDataAccess(resolved.parent)._global_path
        self.assertEqual(
            str(writer_global), cfg.lessons_path,
            'writer global file must equal the path the agent client reads',
        )
        self.assertEqual(writer_global, resolved)

    def test_unconfigured_writer_and_reader_agree(self):
        # The bug case: KATO_LESSONS_PATH unset → config lessons_path ''.
        self._assert_agree(SimpleNamespace(lessons_path=''))

    def test_configured_writer_and_reader_agree(self):
        self._assert_agree(SimpleNamespace(lessons_path='/var/kato-state/lessons.md'))


if __name__ == '__main__':
    unittest.main()
