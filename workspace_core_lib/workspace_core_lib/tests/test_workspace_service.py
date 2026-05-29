"""Behavioural tests for the high-level :class:`WorkspaceService`.

These tests cover the public façade hosts use:

* path computation (``workspace_path``, ``repository_path``,
  ``preflight_log_path``)
* lifecycle (``create`` / ``get`` / ``list_workspaces`` / ``delete``)
* partial updates (``update_status`` / ``update_agent_session`` /
  ``update_repositories`` / ``update_resume_on_startup``)
* preflight log append + read
* concurrency / safety
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
    WORKSPACE_STATUS_TERMINATED,
    WorkspaceRecord,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
    WorkspaceDataAccess,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.workspace_service import (
    DEFAULT_PREFLIGHT_LOG_FILENAME,
    WorkspaceService,
)


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data_access = WorkspaceDataAccess(root=self.root)
        self.service = WorkspaceService(
            self.data_access,
            max_parallel_tasks=4,
        )

    # ----- construction -----

    def test_constructor_rejects_missing_data_access(self) -> None:
        with self.assertRaisesRegex(ValueError, 'data_access is required'):
            WorkspaceService(None)  # type: ignore[arg-type]

    def test_constructor_rejects_empty_preflight_filename(self) -> None:
        with self.assertRaisesRegex(
            ValueError, 'preflight_log_filename is required',
        ):
            WorkspaceService(self.data_access, preflight_log_filename='')

    def test_max_parallel_tasks_clamped_to_one(self) -> None:
        s = WorkspaceService(self.data_access, max_parallel_tasks=0)
        self.assertEqual(s.max_parallel_tasks, 1)
        s = WorkspaceService(self.data_access, max_parallel_tasks=-5)
        self.assertEqual(s.max_parallel_tasks, 1)

    def test_root_property_returns_data_access_root(self) -> None:
        self.assertEqual(self.service.root, self.data_access.root)

    # ----- create -----

    def test_create_makes_folder_and_metadata_file(self) -> None:
        record = self.service.create(
            task_id='PROJ-1',
            task_summary='something',
            repository_ids=['client'],
        )
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.repository_ids, ['client'])
        self.assertEqual(record.status, WORKSPACE_STATUS_PROVISIONING)
        workspace = self.root / 'PROJ-1'
        self.assertTrue(workspace.is_dir())
        meta = json.loads(
            (workspace / DEFAULT_METADATA_FILENAME).read_text()
        )
        self.assertEqual(meta['task_id'], 'PROJ-1')
        self.assertEqual(meta['task_summary'], 'something')
        self.assertEqual(meta['repository_ids'], ['client'])
        self.assertTrue(meta['resume_on_startup'])

    def test_create_is_idempotent_and_preserves_created_at(self) -> None:
        first = self.service.create(task_id='PROJ-1', task_summary='one')
        second = self.service.create(task_id='PROJ-1', task_summary='two')
        self.assertEqual(second.task_id, 'PROJ-1')
        self.assertEqual(second.task_summary, 'two')
        self.assertEqual(second.created_at_epoch, first.created_at_epoch)
        self.assertGreaterEqual(second.updated_at_epoch, first.updated_at_epoch)

    def test_create_idempotent_preserves_existing_repository_ids(self) -> None:
        self.service.create(task_id='PROJ-1', repository_ids=['a', 'b'])
        # Second create without repository_ids must not blank them.
        self.service.create(task_id='PROJ-1', task_summary='still here')
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.repository_ids, ['a', 'b'])

    def test_create_idempotent_preserves_session_id_and_cwd(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.service.update_agent_session(
            'PROJ-1', agent_session_id='sess-abc', cwd='/tmp/x',
        )
        # Second create must not blank the session id we set above.
        self.service.create(task_id='PROJ-1')
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.agent_session_id, 'sess-abc')
        self.assertEqual(record.cwd, '/tmp/x')

    # ----- queries -----

    def test_repository_path_is_under_workspace(self) -> None:
        path = self.service.repository_path('PROJ-1', 'client')
        self.assertEqual(path, self.root / 'PROJ-1' / 'client')

    def test_repository_path_rejects_empty_repository_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'repository_id is required'):
            self.service.repository_path('PROJ-1', '')

    def test_workspace_path_doesnt_require_existing_folder(self) -> None:
        path = self.service.workspace_path('NEW-1')
        self.assertEqual(path, self.root / 'NEW-1')
        self.assertFalse(path.exists())  # path computed, folder not created

    def test_get_returns_persisted_record(self) -> None:
        self.service.create(
            task_id='PROJ-2', task_summary='foo', repository_ids=['a', 'b'],
        )
        record = self.service.get('PROJ-2')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.task_summary, 'foo')
        self.assertEqual(record.repository_ids, ['a', 'b'])

    def test_get_returns_errored_record_when_metadata_missing(self) -> None:
        (self.root / 'PROJ-3').mkdir()
        record = self.service.get('PROJ-3')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ERRORED)

    def test_get_returns_none_for_missing_workspace(self) -> None:
        self.assertIsNone(self.service.get('NOPE-1'))

    def test_list_workspaces_returns_every_subfolder(self) -> None:
        self.service.create(task_id='PROJ-1', task_summary='one')
        self.service.create(task_id='PROJ-2', task_summary='two')
        (self.root / 'PROJ-3').mkdir()
        records = self.service.list_workspaces()
        ids = [r.task_id for r in records]
        self.assertIn('PROJ-1', ids)
        self.assertIn('PROJ-2', ids)
        self.assertIn('PROJ-3', ids)
        broken = next(r for r in records if r.task_id == 'PROJ-3')
        self.assertEqual(broken.status, WORKSPACE_STATUS_ERRORED)

    # ----- updates -----

    def test_update_status_persists(self) -> None:
        self.service.create(task_id='PROJ-1', task_summary='one')
        updated = self.service.update_status('PROJ-1', WORKSPACE_STATUS_ACTIVE)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.status, WORKSPACE_STATUS_ACTIVE)
        record_again = self.service.get('PROJ-1')
        assert record_again is not None
        self.assertEqual(record_again.status, WORKSPACE_STATUS_ACTIVE)

    def test_update_status_rejects_unknown_value(self) -> None:
        self.service.create(task_id='PROJ-1')
        with self.assertRaisesRegex(ValueError, 'unknown workspace status'):
            self.service.update_status('PROJ-1', 'totally-made-up')

    def test_update_status_returns_none_for_missing_workspace(self) -> None:
        self.assertIsNone(
            self.service.update_status('NOPE', WORKSPACE_STATUS_ACTIVE),
        )

    def test_update_status_refuses_when_metadata_is_missing(self) -> None:
        # Folder exists, metadata doesn't (errored). We refuse to
        # silently overwrite — the caller must call ``create`` to
        # explicitly seed metadata.
        (self.root / 'BROKEN').mkdir()
        self.assertIsNone(
            self.service.update_status('BROKEN', WORKSPACE_STATUS_ACTIVE),
        )

    def test_update_repositories_persists(self) -> None:
        self.service.create(task_id='PROJ-1', repository_ids=['a'])
        self.service.update_repositories('PROJ-1', ['a', 'b'])
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.repository_ids, ['a', 'b'])

    def test_update_repositories_filters_falsy(self) -> None:
        self.service.create(task_id='PROJ-1', repository_ids=['a'])
        self.service.update_repositories('PROJ-1', ['a', '', None, 'c'])  # type: ignore[list-item]
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.repository_ids, ['a', 'c'])

    def test_update_resume_on_startup_persists(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.service.update_resume_on_startup('PROJ-1', False)
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertFalse(record.resume_on_startup)

    def test_update_agent_session_sets_both_fields(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.service.update_agent_session(
            'PROJ-1', agent_session_id='sess-1', cwd='/tmp/wks',
        )
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.agent_session_id, 'sess-1')
        self.assertEqual(record.cwd, '/tmp/wks')

    def test_update_agent_session_normalizes_session_id(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.service.update_agent_session(
            'PROJ-1', agent_session_id='  sess-1\n',
        )

        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.agent_session_id, 'sess-1')

    def test_update_agent_session_partial_does_not_blank_existing(self) -> None:
        # Setting only ``agent_session_id`` must not erase a
        # previously-recorded ``cwd`` (and vice-versa).
        self.service.create(task_id='PROJ-1')
        self.service.update_agent_session(
            'PROJ-1', agent_session_id='sess-1', cwd='/tmp/wks',
        )
        self.service.update_agent_session(
            'PROJ-1', agent_session_id='sess-2',
        )
        record = self.service.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.agent_session_id, 'sess-2')
        self.assertEqual(record.cwd, '/tmp/wks')

    def test_update_agent_session_returns_none_for_missing_workspace(self) -> None:
        self.assertIsNone(
            self.service.update_agent_session(
                'NOPE', agent_session_id='x', cwd='/tmp',
            ),
        )

    # ----- delete -----

    def test_delete_removes_the_folder(self) -> None:
        self.service.create(task_id='PROJ-1', task_summary='one')
        self.assertTrue((self.root / 'PROJ-1').is_dir())
        self.service.delete('PROJ-1')
        self.assertFalse((self.root / 'PROJ-1').exists())

    def test_delete_is_idempotent(self) -> None:
        self.service.delete('NOPE-1')  # should not raise

    # ----- safe ids -----

    def test_safe_task_id_strips_path_separators(self) -> None:
        record = self.service.create(task_id='evil/../escape')
        self.assertTrue((self.root / 'evil_.._escape').is_dir())
        self.assertEqual(record.task_id, 'evil_.._escape')

    # ----- preflight log -----

    def test_preflight_log_path_uses_default_filename(self) -> None:
        path = self.service.preflight_log_path('PROJ-1')
        self.assertEqual(path.name, DEFAULT_PREFLIGHT_LOG_FILENAME)
        self.assertEqual(path.parent, self.root / 'PROJ-1')

    def test_preflight_log_append_then_read_round_trip(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.service.append_preflight_log('PROJ-1', 'cloning 1/2: client')
        self.service.append_preflight_log('PROJ-1', '✓ cloned 1/2: client')
        entries = self.service.read_preflight_log('PROJ-1')
        messages = [m for _, m in entries]
        self.assertEqual(
            messages,
            ['cloning 1/2: client', '✓ cloned 1/2: client'],
        )

    def test_preflight_log_returns_empty_when_log_missing(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.assertEqual(self.service.read_preflight_log('PROJ-1'), [])

    def test_preflight_log_skips_blank_entries(self) -> None:
        self.service.create(task_id='PROJ-1')
        self.service.append_preflight_log('PROJ-1', '   ')
        self.service.append_preflight_log('PROJ-1', '')
        self.assertEqual(self.service.read_preflight_log('PROJ-1'), [])

    def test_preflight_log_handles_legacy_lines_without_epoch(self) -> None:
        # Hand-edited or pre-format-change lines parse with epoch=0
        # rather than crashing the read.
        self.service.create(task_id='PROJ-1')
        self.service.preflight_log_path('PROJ-1').write_text(
            'no-tab-line\n', encoding='utf-8',
        )
        entries = self.service.read_preflight_log('PROJ-1')
        self.assertEqual(entries, [(0.0, 'no-tab-line')])

    def test_preflight_log_handles_invalid_epoch_text(self) -> None:
        # Garbage in the epoch column → epoch=0, message preserved.
        self.service.create(task_id='PROJ-1')
        self.service.preflight_log_path('PROJ-1').write_text(
            'banana\thello\n', encoding='utf-8',
        )
        entries = self.service.read_preflight_log('PROJ-1')
        self.assertEqual(entries, [(0.0, 'hello')])

    def test_custom_preflight_log_filename_is_honored(self) -> None:
        s = WorkspaceService(
            self.data_access,
            preflight_log_filename='.custom-log',
        )
        path = s.preflight_log_path('PROJ-1')
        self.assertEqual(path.name, '.custom-log')

    # ----- concurrency -----

    def test_concurrent_creates_for_different_tasks_all_succeed(self) -> None:
        # Stress: ensure the lock doesn't serialize incorrectly OR
        # let writes interleave on a torn metadata file.
        errors: list[Exception] = []

        def make(i: int) -> None:
            try:
                self.service.create(
                    task_id=f'PROJ-{i}', task_summary=f's{i}',
                )
            except Exception as exc:  # pragma: no cover — defensive
                errors.append(exc)

        threads = [threading.Thread(target=make, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.service.list_workspaces()), 20)

    # ----- data_access property -----

    def test_data_access_property_returns_the_instance(self) -> None:
        self.assertIs(self.service.data_access, self.data_access)

    # ----- all six status transitions -----

    def test_update_status_to_provisioning(self) -> None:
        self.service.create(task_id='PROJ-10')
        result = self.service.update_status('PROJ-10', WORKSPACE_STATUS_PROVISIONING)
        assert result is not None
        self.assertEqual(result.status, WORKSPACE_STATUS_PROVISIONING)

    def test_update_status_to_review(self) -> None:
        self.service.create(task_id='PROJ-11')
        result = self.service.update_status('PROJ-11', WORKSPACE_STATUS_REVIEW)
        assert result is not None
        self.assertEqual(result.status, WORKSPACE_STATUS_REVIEW)

    def test_update_status_to_done(self) -> None:
        self.service.create(task_id='PROJ-12')
        result = self.service.update_status('PROJ-12', WORKSPACE_STATUS_DONE)
        assert result is not None
        self.assertEqual(result.status, WORKSPACE_STATUS_DONE)

    def test_update_status_to_errored(self) -> None:
        self.service.create(task_id='PROJ-13')
        result = self.service.update_status('PROJ-13', WORKSPACE_STATUS_ERRORED)
        assert result is not None
        self.assertEqual(result.status, WORKSPACE_STATUS_ERRORED)

    def test_update_status_to_terminated(self) -> None:
        self.service.create(task_id='PROJ-14')
        result = self.service.update_status('PROJ-14', WORKSPACE_STATUS_TERMINATED)
        assert result is not None
        self.assertEqual(result.status, WORKSPACE_STATUS_TERMINATED)

    # ----- update_repositories with empty list -----

    def test_update_repositories_with_empty_list(self) -> None:
        self.service.create(task_id='PROJ-20', repository_ids=['a', 'b'])
        result = self.service.update_repositories('PROJ-20', [])
        assert result is not None
        self.assertEqual(result.repository_ids, [])

    # ----- toggle resume_on_startup back to True -----

    def test_update_resume_on_startup_toggle_false_then_true(self) -> None:
        self.service.create(task_id='PROJ-21')
        self.service.update_resume_on_startup('PROJ-21', False)
        record = self.service.get('PROJ-21')
        assert record is not None
        self.assertFalse(record.resume_on_startup)
        self.service.update_resume_on_startup('PROJ-21', True)
        record = self.service.get('PROJ-21')
        assert record is not None
        self.assertTrue(record.resume_on_startup)

    # ----- preflight epoch recorded -----

    def test_preflight_log_entry_has_positive_epoch(self) -> None:
        import time
        before = int(time.time())  # log stores int seconds
        self.service.create(task_id='PROJ-30')
        self.service.append_preflight_log('PROJ-30', 'step one')
        entries = self.service.read_preflight_log('PROJ-30')
        self.assertEqual(len(entries), 1)
        epoch, _ = entries[0]
        self.assertGreaterEqual(epoch, before)

    def test_preflight_log_multiple_entries_are_ordered(self) -> None:
        self.service.create(task_id='PROJ-31')
        for msg in ('a', 'b', 'c'):
            self.service.append_preflight_log('PROJ-31', msg)
        messages = [m for _, m in self.service.read_preflight_log('PROJ-31')]
        self.assertEqual(messages, ['a', 'b', 'c'])

class WorkspaceServicePreflightLogTests(unittest.TestCase):
    """Cover the defensive paths in append/read of the preflight log."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        from workspace_core_lib.workspace_core_lib.data_layers.service.workspace_service import (
            WorkspaceService,
        )
        from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
            WorkspaceDataAccess,
        )
        self.service = WorkspaceService(WorkspaceDataAccess(root=self.root))

    def test_append_preflight_log_swallows_oserror(self) -> None:
        # Lines 249-250: ``Path.open`` raises → log warning, don't propagate.
        from unittest.mock import patch
        self.service.create(task_id='PROJ-7', task_summary='', repository_ids=[])
        with patch.object(
            Path, 'open', side_effect=PermissionError('locked'),
        ):
            self.service.append_preflight_log('PROJ-7', 'a message')  # must not raise

    def test_read_preflight_log_skips_blank_lines(self) -> None:
        # Line 273: blank line in log → skip without appending.
        self.service.create(task_id='PROJ-8', task_summary='', repository_ids=[])
        path = self.service.preflight_log_path('PROJ-8')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('100\tfirst\n\n200\tsecond\n', encoding='utf-8')
        entries = self.service.read_preflight_log('PROJ-8')
        self.assertEqual(len(entries), 2)

    def test_read_preflight_log_handles_lines_without_tab(self) -> None:
        # Line 274-276: line without tab → epoch=0.
        self.service.create(task_id='PROJ-9', task_summary='', repository_ids=[])
        path = self.service.preflight_log_path('PROJ-9')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('legacy-no-tab\n', encoding='utf-8')
        entries = self.service.read_preflight_log('PROJ-9')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], 0.0)
        self.assertEqual(entries[0][1], 'legacy-no-tab')

    def test_read_preflight_log_swallows_oserror_and_returns_empty(self) -> None:
        # Lines 283-288: ``Path.open`` raises → log warning + return [].
        from unittest.mock import patch
        self.service.create(task_id='PROJ-10', task_summary='', repository_ids=[])
        path = self.service.preflight_log_path('PROJ-10')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('100\ta\n', encoding='utf-8')  # so is_file() is True
        # Now patch open to fail.
        real_open = Path.open

        def selective(self_path, *args, **kwargs):
            if self_path == path:
                raise PermissionError('locked')
            return real_open(self_path, *args, **kwargs)

        with patch.object(Path, 'open', selective):
            entries = self.service.read_preflight_log('PROJ-10')
        self.assertEqual(entries, [])


if __name__ == '__main__':
    unittest.main()
