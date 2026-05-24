"""A-Z flow tests for workspace_core_lib.

Each test exercises the full vertical stack — WorkspaceCoreLib →
WorkspaceService → WorkspaceDataAccess → filesystem — with no mocking
of internal components.  Only the filesystem root is temporary.

Flow coverage
-------------
F1   Full lifecycle: create → active → done → delete
F2   Orphan detection and adoption via WorkspaceCoreLib
F3   Concurrent creates for different tasks — all records persisted
F4   Preflight log round-trip with real epoch values
F5   Custom metadata filename end-to-end (legacy deployment scenario)
F6   Custom preflight log filename end-to-end
F8   Idempotent create: second call preserves created_at and merges fields
F9   Partial update chain — multiple updates accumulate correctly
F10  Errored workspace (folder without metadata) visible in list
F11  max_parallel_tasks propagated from WorkspaceCoreLib through service
F12  Path separator in task_id sanitized end-to-end
F13  Multiple tasks, each gets an independent workspace folder
F14  update_resume_on_startup toggles True → False → True
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from workspace_core_lib.workspace_core_lib import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WorkspaceCoreLib,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
)


class F1FullLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_create_then_transition_to_active_then_done_then_delete(self) -> None:
        ws = self.lib.workspaces

        record = ws.create(
            task_id='T-1',
            task_summary='lifecycle',
            repository_ids=['client', 'backend'],
        )
        self.assertEqual(record.status, WORKSPACE_STATUS_PROVISIONING)
        self.assertTrue(ws.exists('T-1'))

        ws.update_status('T-1', WORKSPACE_STATUS_ACTIVE)
        ws.update_agent_session('T-1', agent_session_id='s-uuid', cwd='/work/T-1')
        active = ws.get('T-1')
        assert active is not None
        self.assertEqual(active.status, WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(active.agent_session_id, 's-uuid')

        ws.update_status('T-1', WORKSPACE_STATUS_DONE)
        done = ws.get('T-1')
        assert done is not None
        self.assertEqual(done.status, WORKSPACE_STATUS_DONE)

        ws.delete('T-1')
        self.assertFalse(ws.exists('T-1'))
        self.assertEqual(ws.list_workspaces(), [])


class F2OrphanDetectionAndAdoptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.lib = WorkspaceCoreLib(root=self.root)

    def test_orphan_scan_finds_folders_without_metadata(self) -> None:
        # Registered workspace — not an orphan.
        self.lib.workspaces.create(task_id='REGISTERED')
        # Two orphan folders dropped directly.
        (self.root / 'ORPHAN-A').mkdir()
        (self.root / 'ORPHAN-B').mkdir()

        orphans = self.lib.orphan_scanner.scan()
        ids = [o.task_id for o in orphans]
        self.assertIn('ORPHAN-A', ids)
        self.assertIn('ORPHAN-B', ids)
        self.assertNotIn('REGISTERED', ids)

    def test_adoption_via_create_removes_orphan_from_scan(self) -> None:
        (self.root / 'ADOPT-ME').mkdir()
        self.assertIn('ADOPT-ME', [o.task_id for o in self.lib.orphan_scanner.scan()])

        self.lib.workspaces.create(task_id='ADOPT-ME', task_summary='adopted')
        self.assertEqual(self.lib.orphan_scanner.scan(), [])

    def test_git_repo_dirs_detected_in_orphan(self) -> None:
        orphan = self.root / 'GIT-ORPHAN'
        orphan.mkdir()
        repo = orphan / 'my-repo'
        repo.mkdir()
        (repo / '.git').mkdir()

        orphans = self.lib.orphan_scanner.scan()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0].git_repository_dirs, ('my-repo',))


class F3ConcurrentCreatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_concurrent_creates_all_persisted(self) -> None:
        errors: list[Exception] = []

        def make(i: int) -> None:
            try:
                self.lib.workspaces.create(task_id=f'T-{i:03d}')
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=make, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.lib.workspaces.list_workspaces()), 30)


class F4PreflightLogRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_append_and_read_preserves_messages_with_epochs(self) -> None:
        ws = self.lib.workspaces
        ws.create(task_id='PF-1')

        before = int(time.time())  # log stores int seconds
        ws.append_preflight_log('PF-1', 'step 1')
        ws.append_preflight_log('PF-1', 'step 2')

        entries = ws.read_preflight_log('PF-1')
        self.assertEqual(len(entries), 2)
        messages = [m for _, m in entries]
        self.assertEqual(messages, ['step 1', 'step 2'])
        for epoch, _ in entries:
            self.assertGreaterEqual(epoch, before)

    def test_empty_messages_are_not_appended(self) -> None:
        ws = self.lib.workspaces
        ws.create(task_id='PF-2')
        ws.append_preflight_log('PF-2', '')
        ws.append_preflight_log('PF-2', '   ')
        ws.append_preflight_log('PF-2', 'real')
        entries = ws.read_preflight_log('PF-2')
        self.assertEqual([m for _, m in entries], ['real'])

    def test_missing_log_returns_empty_list(self) -> None:
        self.lib.workspaces.create(task_id='PF-3')
        self.assertEqual(self.lib.workspaces.read_preflight_log('PF-3'), [])


class F5CustomMetadataFilenameTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_create_writes_to_custom_filename(self) -> None:
        lib = WorkspaceCoreLib(root=self.root, metadata_filename='.legacy.json')
        lib.workspaces.create(task_id='L-1', task_summary='legacy test')
        self.assertTrue((self.root / 'L-1' / '.legacy.json').is_file())
        # Default filename should NOT exist.
        self.assertFalse((self.root / 'L-1' / DEFAULT_METADATA_FILENAME).is_file())

    def test_get_reads_from_custom_filename(self) -> None:
        lib = WorkspaceCoreLib(root=self.root, metadata_filename='.legacy.json')
        lib.workspaces.create(task_id='L-2', task_summary='check read')
        record = lib.workspaces.get('L-2')
        assert record is not None
        self.assertEqual(record.task_summary, 'check read')

    def test_orphan_scanner_uses_custom_metadata_filename(self) -> None:
        lib = WorkspaceCoreLib(root=self.root, metadata_filename='.legacy.json')
        # A folder with the DEFAULT filename is not registered under .legacy.json
        # so the scanner sees it as an orphan.
        (self.root / 'O-1').mkdir()
        (self.root / 'O-1' / DEFAULT_METADATA_FILENAME).write_text('{}')
        orphans = lib.orphan_scanner.scan()
        self.assertEqual([o.task_id for o in orphans], ['O-1'])


class F6CustomPreflightLogFilenameTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_preflight_log_uses_custom_filename(self) -> None:
        lib = WorkspaceCoreLib(
            root=self.root,
            preflight_log_filename='.progress.log',
        )
        lib.workspaces.create(task_id='CL-1')
        lib.workspaces.append_preflight_log('CL-1', 'hello')
        self.assertTrue((self.root / 'CL-1' / '.progress.log').is_file())
        entries = lib.workspaces.read_preflight_log('CL-1')
        self.assertEqual([m for _, m in entries], ['hello'])


class F8IdempotentCreateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_second_create_preserves_created_at(self) -> None:
        first = self.lib.workspaces.create(task_id='IDE-1', task_summary='initial')
        second = self.lib.workspaces.create(task_id='IDE-1', task_summary='updated')
        self.assertEqual(second.created_at_epoch, first.created_at_epoch)
        self.assertEqual(second.task_summary, 'updated')

    def test_second_create_without_repos_preserves_existing(self) -> None:
        self.lib.workspaces.create(task_id='IDE-2', repository_ids=['r1', 'r2'])
        second = self.lib.workspaces.create(task_id='IDE-2')
        self.assertEqual(second.repository_ids, ['r1', 'r2'])

    def test_second_create_preserves_session_id_and_cwd(self) -> None:
        self.lib.workspaces.create(task_id='IDE-3')
        self.lib.workspaces.update_agent_session(
            'IDE-3', agent_session_id='s-x', cwd='/x',
        )
        self.lib.workspaces.create(task_id='IDE-3')
        record = self.lib.workspaces.get('IDE-3')
        assert record is not None
        self.assertEqual(record.agent_session_id, 's-x')
        self.assertEqual(record.cwd, '/x')


class F9UpdateChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_multiple_partial_updates_accumulate(self) -> None:
        ws = self.lib.workspaces
        ws.create(task_id='CH-1')
        ws.update_status('CH-1', WORKSPACE_STATUS_ACTIVE)
        ws.update_agent_session('CH-1', agent_session_id='sess-1')
        ws.update_agent_session('CH-1', cwd='/some/path')
        ws.update_repositories('CH-1', ['repo-a', 'repo-b'])
        ws.update_resume_on_startup('CH-1', False)

        record = ws.get('CH-1')
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(record.agent_session_id, 'sess-1')
        self.assertEqual(record.cwd, '/some/path')
        self.assertEqual(record.repository_ids, ['repo-a', 'repo-b'])
        self.assertFalse(record.resume_on_startup)

    def test_update_on_missing_workspace_returns_none(self) -> None:
        result = self.lib.workspaces.update_status('MISSING', WORKSPACE_STATUS_ACTIVE)
        self.assertIsNone(result)


class F10ErroredWorkspaceVisibleInListTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.lib = WorkspaceCoreLib(root=self.root)

    def test_folder_without_metadata_appears_as_errored_in_list(self) -> None:
        self.lib.workspaces.create(task_id='OK-1')
        (self.root / 'BROKEN').mkdir()

        records = self.lib.workspaces.list_workspaces()
        statuses = {r.task_id: r.status for r in records}
        self.assertEqual(statuses.get('OK-1'), WORKSPACE_STATUS_PROVISIONING)
        self.assertEqual(statuses.get('BROKEN'), WORKSPACE_STATUS_ERRORED)

    def test_get_on_folder_without_metadata_returns_errored(self) -> None:
        (self.root / 'HALF').mkdir()
        record = self.lib.workspaces.get('HALF')
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ERRORED)


class F11MaxParallelTasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_max_parallel_tasks_default_is_one(self) -> None:
        lib = WorkspaceCoreLib(root=self.root)
        self.assertEqual(lib.workspaces.max_parallel_tasks, 1)

    def test_max_parallel_tasks_custom_value_propagates(self) -> None:
        lib = WorkspaceCoreLib(root=self.root, max_parallel_tasks=12)
        self.assertEqual(lib.workspaces.max_parallel_tasks, 12)

    def test_max_parallel_tasks_clamped_below_one(self) -> None:
        lib = WorkspaceCoreLib(root=self.root, max_parallel_tasks=0)
        self.assertEqual(lib.workspaces.max_parallel_tasks, 1)


class F12PathSeparatorSanitizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.lib = WorkspaceCoreLib(root=self.root)

    def test_slash_in_task_id_is_replaced(self) -> None:
        record = self.lib.workspaces.create(task_id='evil/../../escape')
        self.assertEqual(record.task_id, 'evil_.._.._escape')
        # The folder stays under root, not escaped to parent.
        self.assertEqual(
            self.lib.workspaces.workspace_path(record.task_id).parent,
            self.root,
        )

    def test_slash_in_repository_id_is_replaced(self) -> None:
        self.lib.workspaces.create(task_id='T-SAFE')
        path = self.lib.workspaces.repository_path('T-SAFE', 'group/repo')
        self.assertEqual(path.name, 'group_repo')
        self.assertEqual(path.parent.parent, self.root)


class F13MultipleTasksIndependentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_five_tasks_each_have_independent_folders_and_records(self) -> None:
        for i in range(5):
            self.lib.workspaces.create(
                task_id=f'MULTI-{i}',
                task_summary=f'task {i}',
                repository_ids=[f'repo-{i}'],
            )
        records = {r.task_id: r for r in self.lib.workspaces.list_workspaces()}
        self.assertEqual(len(records), 5)
        for i in range(5):
            r = records[f'MULTI-{i}']
            self.assertEqual(r.task_summary, f'task {i}')
            self.assertEqual(r.repository_ids, [f'repo-{i}'])

    def test_delete_one_does_not_affect_others(self) -> None:
        for tid in ('A', 'B', 'C'):
            self.lib.workspaces.create(task_id=tid)
        self.lib.workspaces.delete('B')
        ids = {r.task_id for r in self.lib.workspaces.list_workspaces()}
        self.assertEqual(ids, {'A', 'C'})


class F14ResumeOnStartupToggleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = WorkspaceCoreLib(root=Path(self._tmp.name))

    def test_resume_on_startup_defaults_to_true(self) -> None:
        record = self.lib.workspaces.create(task_id='ROS-1')
        self.assertTrue(record.resume_on_startup)

    def test_toggle_true_to_false(self) -> None:
        self.lib.workspaces.create(task_id='ROS-2')
        self.lib.workspaces.update_resume_on_startup('ROS-2', False)
        record = self.lib.workspaces.get('ROS-2')
        assert record is not None
        self.assertFalse(record.resume_on_startup)

    def test_toggle_false_back_to_true(self) -> None:
        self.lib.workspaces.create(task_id='ROS-3')
        self.lib.workspaces.update_resume_on_startup('ROS-3', False)
        self.lib.workspaces.update_resume_on_startup('ROS-3', True)
        record = self.lib.workspaces.get('ROS-3')
        assert record is not None
        self.assertTrue(record.resume_on_startup)

    def test_toggle_persisted_to_disk(self) -> None:
        self.lib.workspaces.create(task_id='ROS-4')
        self.lib.workspaces.update_resume_on_startup('ROS-4', False)
        # Reload from disk by getting the record again.
        record = self.lib.workspaces.get('ROS-4')
        assert record is not None
        self.assertFalse(record.resume_on_startup)


if __name__ == '__main__':
    unittest.main()
