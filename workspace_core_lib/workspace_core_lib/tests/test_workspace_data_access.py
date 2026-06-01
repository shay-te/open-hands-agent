"""Filesystem-level CRUD tests for :class:`WorkspaceDataAccess`."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_ERRORED,
    WorkspaceRecord,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
    WorkspaceDataAccess,
)


class WorkspaceDataAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / 'wks'
        self.data_access = WorkspaceDataAccess(root=self.root)

    def test_constructor_creates_root_dir(self) -> None:
        self.assertTrue(self.root.is_dir())

    def test_constructor_rejects_empty_root(self) -> None:
        with self.assertRaisesRegex(ValueError, 'root is required'):
            WorkspaceDataAccess(root='')

    def test_constructor_rejects_empty_metadata_filename(self) -> None:
        with self.assertRaisesRegex(ValueError, 'metadata_filename is required'):
            WorkspaceDataAccess(root=self.root, metadata_filename='')

    def test_workspace_dir_strips_path_separators(self) -> None:
        # Defensive: a malicious task id can't escape via ``..``.
        path = self.data_access.workspace_dir('evil/../escape')
        self.assertEqual(path.parent, self.root)
        self.assertEqual(path.name, 'evil_.._escape')

    def test_metadata_path_uses_default_filename(self) -> None:
        path = self.data_access.metadata_path('PROJ-1')
        self.assertEqual(path.name, DEFAULT_METADATA_FILENAME)
        self.assertEqual(path.parent, self.root / 'PROJ-1')

    def test_custom_metadata_filename_is_honored(self) -> None:
        # Backwards-compat use case: kato pins ``.kato-meta.json``
        # at construction time so existing on-disk records load.
        da = WorkspaceDataAccess(
            root=self.root, metadata_filename='.kato-meta.json',
        )
        path = da.metadata_path('PROJ-1')
        self.assertEqual(path.name, '.kato-meta.json')

    def test_save_then_get_round_trips_record(self) -> None:
        record = WorkspaceRecord(
            task_id='PROJ-1', task_summary='hello', repository_ids=['r1'],
        )
        self.data_access.save(record)
        loaded = self.data_access.get('PROJ-1')
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.task_summary, 'hello')
        self.assertEqual(loaded.repository_ids, ['r1'])

    def test_save_writes_atomic_json(self) -> None:
        record = WorkspaceRecord(task_id='PROJ-1')
        self.data_access.save(record)
        # Atomic-write leaves no leftover .tmp files in the dir.
        leftovers = [
            p.name for p in (self.root / 'PROJ-1').iterdir()
            if p.name.endswith('.tmp')
        ]
        self.assertEqual(leftovers, [])
        # The file is valid JSON.
        payload = json.loads(self.data_access.metadata_path('PROJ-1').read_text())
        self.assertEqual(payload['task_id'], 'PROJ-1')

    def test_save_requires_task_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            self.data_access.save(WorkspaceRecord(task_id=''))

    def test_get_returns_none_for_missing_workspace(self) -> None:
        self.assertIsNone(self.data_access.get('NOPE'))

    def test_get_returns_errored_record_when_metadata_missing(self) -> None:
        # Folder exists but no metadata — half-initialized workspace
        # from a crash. We surface a synthetic ``errored`` record so
        # UIs can offer Discard.
        (self.root / 'BROKEN').mkdir()
        record = self.data_access.get('BROKEN')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ERRORED)

    def test_get_returns_errored_when_metadata_is_corrupt_json(self) -> None:
        # A torn or hand-mangled file shouldn't crash the listing.
        (self.root / 'CORRUPT').mkdir()
        (self.root / 'CORRUPT' / DEFAULT_METADATA_FILENAME).write_text('{not json')
        record = self.data_access.get('CORRUPT')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ERRORED)

    def test_get_returns_errored_when_metadata_is_not_a_dict(self) -> None:
        # Edge: hand-edited file replaced JSON object with a list.
        (self.root / 'WEIRD').mkdir()
        (self.root / 'WEIRD' / DEFAULT_METADATA_FILENAME).write_text('[1,2,3]')
        record = self.data_access.get('WEIRD')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ERRORED)

    def test_list_all_includes_errored_entries_for_missing_metadata(self) -> None:
        ok = WorkspaceRecord(task_id='OK')
        ok.status = WORKSPACE_STATUS_ACTIVE
        self.data_access.save(ok)
        (self.root / 'BROKEN').mkdir()
        records = self.data_access.list_all()
        statuses = {r.task_id: r.status for r in records}
        self.assertEqual(statuses['OK'], WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(statuses['BROKEN'], WORKSPACE_STATUS_ERRORED)

    def test_list_all_returns_empty_when_root_missing(self) -> None:
        # Constructor created the root, but if a caller deletes it
        # afterwards the listing should still degrade gracefully.
        import shutil
        shutil.rmtree(self.root)
        self.assertEqual(self.data_access.list_all(), [])

    def test_list_all_skips_files_at_root(self) -> None:
        # A stray file under the root (e.g. .DS_Store) shouldn't be
        # treated as a workspace.
        (self.root / 'stray.txt').write_text('ignore me')
        self.assertEqual(self.data_access.list_all(), [])

    def test_list_all_returns_records_sorted_by_folder_name(self) -> None:
        # Determinism: UIs expect a stable ordering.
        for tid in ('PROJ-3', 'PROJ-1', 'PROJ-2'):
            self.data_access.save(WorkspaceRecord(task_id=tid))
        ids = [r.task_id for r in self.data_access.list_all()]
        self.assertEqual(ids, ['PROJ-1', 'PROJ-2', 'PROJ-3'])

    def test_exists_and_has_metadata(self) -> None:
        self.assertFalse(self.data_access.exists('PROJ-1'))
        self.assertFalse(self.data_access.has_metadata('PROJ-1'))
        self.data_access.ensure_workspace_dir('PROJ-1')
        self.assertTrue(self.data_access.exists('PROJ-1'))
        self.assertFalse(self.data_access.has_metadata('PROJ-1'))
        self.data_access.save(WorkspaceRecord(task_id='PROJ-1'))
        self.assertTrue(self.data_access.has_metadata('PROJ-1'))

    def test_delete_removes_folder_and_is_idempotent(self) -> None:
        self.data_access.save(WorkspaceRecord(task_id='PROJ-1'))
        self.assertTrue(self.data_access.exists('PROJ-1'))
        self.data_access.delete('PROJ-1')
        self.assertFalse(self.data_access.exists('PROJ-1'))
        # Second delete is a no-op.
        self.data_access.delete('PROJ-1')

    def test_root_property_returns_configured_root(self) -> None:
        self.assertEqual(self.data_access.root, self.root)

    def test_metadata_filename_property_returns_configured_filename(self) -> None:
        da = WorkspaceDataAccess(
            root=self.root, metadata_filename='.custom.json',
        )
        self.assertEqual(da.metadata_filename, '.custom.json')

    def test_metadata_filename_property_returns_default(self) -> None:
        self.assertEqual(
            self.data_access.metadata_filename, DEFAULT_METADATA_FILENAME,
        )

    def test_ensure_workspace_dir_creates_and_returns_path(self) -> None:
        path = self.data_access.ensure_workspace_dir('NEW-1')
        self.assertTrue(path.is_dir())
        self.assertEqual(path, self.root / 'NEW-1')

    def test_ensure_workspace_dir_is_idempotent(self) -> None:
        path1 = self.data_access.ensure_workspace_dir('IDEM-1')
        path2 = self.data_access.ensure_workspace_dir('IDEM-1')
        self.assertEqual(path1, path2)
        self.assertTrue(path2.is_dir())

    def test_delete_swallows_oserror_during_rmtree(self) -> None:
        # Lines 190-191: ``shutil.rmtree`` fails → log warning, do not raise.
        from unittest.mock import patch
        self.data_access.ensure_workspace_dir('LOCKED-1')
        with patch('shutil.rmtree', side_effect=PermissionError('locked')):
            self.data_access.delete('LOCKED-1')

    def test_delete_on_rm_error_chmods_and_retries(self) -> None:
        # Lines 203-206: ``_on_rm_error`` flips the read-only bit and
        # retries ``func(path)`` successfully. This is the Windows
        # "read-only git pack file" recovery path. Driven via a mock:
        # ``_failing_rmtree`` invokes ``onerror`` with a func that
        # succeeds on the chmod+retry call, so the callback completes
        # without re-raising.
        from unittest.mock import MagicMock, patch
        self.data_access.ensure_workspace_dir('READONLY-1')

        retry_func = MagicMock()  # Succeeds on retry (no side_effect).

        def _failing_rmtree(path, onerror=None):
            # Call onerror once — simulates rmtree hitting a locked
            # file. The callback's chmod-then-retry must succeed and
            # NOT re-raise, so the outer ``try`` block returns
            # cleanly on the same attempt.
            onerror(
                retry_func,
                str(path),
                (PermissionError, PermissionError('locked'), None),
            )

        with patch('shutil.rmtree', side_effect=_failing_rmtree), \
                patch('os.chmod') as mock_chmod:
            self.data_access.delete('READONLY-1')

        # Both legs of the chmod-then-retry recovery fired.
        mock_chmod.assert_called_once()
        retry_func.assert_called_once()

    def test_delete_on_rm_error_reraises_original_when_chmod_fails(self) -> None:
        # Lines 207-209: ``_on_rm_error`` falls into the inner ``except
        # OSError`` and re-raises ``exc_info[1]`` (the ORIGINAL error)
        # so the outer rmtree-retry loop sees a meaningful trace
        # instead of a misleading chmod failure. Patching ``os.chmod``
        # to raise simulates a filesystem that refuses permission
        # changes (e.g. read-only mount). The original PermissionError
        # must surface to the outer loop, which then exhausts its 3
        # attempts and logs.
        from unittest.mock import patch
        self.data_access.ensure_workspace_dir('NOCHMOD-1')

        original_exc = PermissionError('held open')

        def _func_that_raises(_path):
            raise original_exc

        def _failing_rmtree(path, onerror=None):
            # Simulate rmtree hitting a file it can't unlink. Call the
            # error handler exactly as the real shutil.rmtree would.
            onerror(
                _func_that_raises,
                str(path),
                (PermissionError, original_exc, None),
            )

        with patch('shutil.rmtree', side_effect=_failing_rmtree), \
                patch('os.chmod', side_effect=OSError('read-only fs')), \
                patch('time.sleep'):  # speed up the 3-attempt loop
            # Should NOT raise — outer except swallows and logs after 3.
            self.data_access.delete('NOCHMOD-1')

    def test_delete_on_rm_error_handles_os_open_without_crashing(self) -> None:
        # Regression: under POSIX fd-based rmtree, ``onerror``'s ``func`` can be
        # ``os.open`` (used to descend into a directory), which — unlike
        # unlink/rmdir/scandir — needs a ``flags`` arg. The handler used to call
        # ``func(path)`` blindly → ``TypeError: open() missing required argument
        # 'flags'`` that escaped the OSError guard and aborted the whole delete
        # (operator saw a confusing "Couldn't forget …" failure with exactly
        # that message). It must now chmod (best effort) and NOT crash.
        import os
        from unittest.mock import patch
        self.data_access.ensure_workspace_dir('FDOPEN-1')

        def _failing_rmtree(path, onerror=None):
            # Simulate the fd-based rmtree failing to os.open a subdir.
            onerror(os.open, str(path), (OSError, OSError('open failed'), None))

        with patch('shutil.rmtree', side_effect=_failing_rmtree), \
                patch('os.chmod') as mock_chmod:
            # Must NOT raise TypeError ("open() missing required argument …").
            self.data_access.delete('FDOPEN-1')

        # chmod best-effort fired; no blind os.open(path) retry happened.
        mock_chmod.assert_called_once()


if __name__ == '__main__':
    unittest.main()
