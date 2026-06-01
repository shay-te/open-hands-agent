"""Tests for the persistent forgotten-tasks store.

This is what stops a task the operator forgot from being resurrected by the
platform review-comment poll (especially after a restart, which clears the
in-memory processed-comment map). Forget writes the id; the scan skips it;
re-adopt clears it.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers import forgotten_tasks_store as store


class ForgottenTasksStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / 'sub' / 'forgotten_tasks.json')
        ctx = patch.dict(os.environ, {'KATO_FORGOTTEN_TASKS_PATH': self.path})
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(store.forgotten_task_ids(), set())
        self.assertFalse(store.is_forgotten('UNA-1'))

    def test_forget_marks_and_persists_to_disk(self) -> None:
        store.forget('UNA-2536')
        self.assertTrue(store.is_forgotten('UNA-2536'))
        self.assertEqual(store.forgotten_task_ids(), {'UNA-2536'})
        # Persisted — survives a "restart" (the on-disk file is the source of
        # truth, not in-memory state). Parent dir is created on write.
        self.assertIn('UNA-2536', json.loads(Path(self.path).read_text(encoding='utf-8')))

    def test_forget_is_idempotent_and_trims_whitespace(self) -> None:
        store.forget(' UNA-1 ')
        store.forget('UNA-1')
        self.assertEqual(store.forgotten_task_ids(), {'UNA-1'})

    def test_unforget_clears_only_that_task(self) -> None:
        store.forget('UNA-1')
        store.forget('UNA-2')
        store.unforget('UNA-1')
        self.assertFalse(store.is_forgotten('UNA-1'))
        self.assertTrue(store.is_forgotten('UNA-2'))

    def test_unforget_unknown_is_noop(self) -> None:
        store.forget('UNA-1')
        store.unforget('UNA-NOPE')
        self.assertEqual(store.forgotten_task_ids(), {'UNA-1'})

    def test_blank_ids_are_ignored(self) -> None:
        store.forget('')
        store.forget('   ')
        self.assertEqual(store.forgotten_task_ids(), set())
        self.assertFalse(store.is_forgotten(''))
        self.assertFalse(store.is_forgotten(None))

    def test_corrupt_or_non_list_file_reads_as_empty_and_recovers(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text('not json{', encoding='utf-8')
        self.assertEqual(store.forgotten_task_ids(), set())
        # A subsequent forget overwrites the corrupt file cleanly.
        store.forget('UNA-9')
        self.assertEqual(store.forgotten_task_ids(), {'UNA-9'})


if __name__ == '__main__':
    unittest.main()
