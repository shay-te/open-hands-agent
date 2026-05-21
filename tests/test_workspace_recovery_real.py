"""End-to-end workspace orphan recovery against REAL disk + REAL workspace_manager.

The existing ``WorkspaceRecoveryServiceTests`` (in
``tests/test_services_medium_coverage.py``) use ``MagicMock()`` for
the workspace_manager and just set ``.root = tempdir``. That works
for testing the orphan-folder *finder*, but it does NOT verify that
``recover_orphan_workspaces()`` ends up writing real
``.kato-meta.json`` files via the real ``WorkspaceService`` →
``WorkspaceDataAccess`` chain.

This file fills the gap: real WorkspaceService, real disk layout
(``<root>/<task_id>/<repo>/.git/``), real task + repository service
stand-ins shaped exactly like the production interfaces. The only
patch is ``find_session_id_for_cwd`` (it would otherwise scan
``~/.claude/projects`` on the operator's machine — not something a
test should depend on).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_core_lib.data_layers.service.workspace_recovery_service import (
    WorkspaceRecoveryService,
)
from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_ACTIVE,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
)

from tests.chaos_lib import (
    CHAOS_TASK_IDS_SAFE,
    build_real_workspace_service,
    impatient_title,
)


class _RealishTaskService(object):
    """Real-shaped TaskService stand-in (mirrors the production interface)."""

    def __init__(
        self,
        assigned: list | None = None,
        review: list | None = None,
        assigned_exc: BaseException | None = None,
        review_exc: BaseException | None = None,
    ) -> None:
        self._assigned = list(assigned or [])
        self._review = list(review or [])
        self._assigned_exc = assigned_exc
        self._review_exc = review_exc

    def get_assigned_tasks(self) -> list:
        if self._assigned_exc is not None:
            raise self._assigned_exc
        return list(self._assigned)

    def get_review_tasks(self) -> list:
        if self._review_exc is not None:
            raise self._review_exc
        return list(self._review)


class _RealishRepoService(object):
    """Real-shaped RepositoryService stand-in for resolve_task_repositories only."""

    def __init__(self, mapping: dict | None = None,
                 raise_exc: BaseException | None = None) -> None:
        self._mapping = dict(mapping or {})
        self._exc = raise_exc

    def resolve_task_repositories(self, task) -> list:
        if self._exc is not None:
            raise self._exc
        return list(self._mapping.get(str(task.id), []))


def _make_task(task_id: str, *, summary: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        summary=summary or impatient_title(seed=hash(task_id)),
        tags=[],
    )


def _make_orphan(
    root: Path, task_id: str, repos: list[str], *, with_metadata: bool = False,
) -> Path:
    """Create ``<root>/<task_id>/<repo>/.git/`` on real disk.

    ``with_metadata=True`` writes the REAL workspace metadata filename
    (``DEFAULT_METADATA_FILENAME``) so the test marks the folder as
    "already managed" using the same sentinel ``WorkspaceDataAccess``
    actually writes — not a leftover misspelling.
    """
    folder = root / task_id
    folder.mkdir(parents=True, exist_ok=True)
    if with_metadata:
        (folder / DEFAULT_METADATA_FILENAME).write_text('{}', encoding='utf-8')
    for repo in repos:
        (folder / repo / '.git').mkdir(parents=True)
    return folder


class WorkspaceRecoveryRealDiskTests(unittest.TestCase):
    """Recovery walks real folders, writes real metadata via real WorkspaceService."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-recovery-real-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace_service = build_real_workspace_service(self.root)
        # Don't depend on ~/.claude/projects — the session-id lookup is
        # a trivial last-mile dependency that's not the point under test.
        self._session_patch = patch(
            'kato_core_lib.data_layers.service.workspace_recovery_service'
            '.find_session_id_for_cwd',
            return_value='',
        )
        self._session_patch.start()
        self.addCleanup(self._session_patch.stop)

    def _build(self, task_service, repo_service) -> WorkspaceRecoveryService:
        return WorkspaceRecoveryService(
            workspace_manager=self.workspace_service,
            task_service=task_service,
            repository_service=repo_service,
        )

    def test_recovers_real_orphan_with_real_workspace_metadata_on_disk(self) -> None:
        # Real disk: <root>/PROJ-1/repo-a/.git
        _make_orphan(self.root, 'PROJ-1', ['repo-a'])
        task_service = _RealishTaskService(assigned=[_make_task(
            'PROJ-1', summary='fix this thing',
        )])
        repo_service = _RealishRepoService(
            mapping={'PROJ-1': [SimpleNamespace(id='repo-a')]},
        )
        service = self._build(task_service, repo_service)

        adopted = service.recover_orphan_workspaces()
        self.assertEqual(len(adopted), 1)
        self.assertEqual(adopted[0].task_id, 'PROJ-1')

        # Real metadata file now exists; real WorkspaceService.list_workspaces
        # picks the recovered workspace up.
        managed = self.workspace_service.list_workspaces()
        self.assertEqual([w.task_id for w in managed], ['PROJ-1'])
        recovered = managed[0]
        self.assertEqual(recovered.status, WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(recovered.repository_ids, ['repo-a'])
        # Metadata file is on disk under the real filename.
        meta_path = self.root / 'PROJ-1' / DEFAULT_METADATA_FILENAME
        self.assertTrue(meta_path.is_file())
        # And the raw JSON has every field we said we'd persist —
        # don't trust the service's own readback, read the bytes.
        import json
        on_disk = json.loads(meta_path.read_text(encoding='utf-8'))
        self.assertEqual(on_disk['task_id'], 'PROJ-1')
        self.assertEqual(on_disk['task_summary'], 'fix this thing')
        self.assertEqual(on_disk['status'], WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(on_disk['repository_ids'], ['repo-a'])

    def test_recovers_multi_repo_orphan_in_one_pass(self) -> None:
        _make_orphan(self.root, 'BIG-1', ['client', 'backend', 'shared'])
        task_service = _RealishTaskService(assigned=[_make_task('BIG-1')])
        repo_service = _RealishRepoService(
            mapping={'BIG-1': [
                SimpleNamespace(id='client'),
                SimpleNamespace(id='backend'),
                SimpleNamespace(id='shared'),
            ]},
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(len(adopted), 1)
        # All three repos recovered, persisted on disk.
        recovered = self.workspace_service.get('BIG-1')
        self.assertEqual(
            sorted(recovered.repository_ids),
            ['backend', 'client', 'shared'],
        )

    def test_case_insensitive_folder_match_against_repository_ids(self) -> None:
        # Folder name is mixed-case, repo id is lower-case — recovery
        # tolerates either ordering of the case mismatch.
        _make_orphan(self.root, 'PROJ-2', ['Client-Repo'])
        task_service = _RealishTaskService(assigned=[_make_task('PROJ-2')])
        repo_service = _RealishRepoService(
            mapping={'PROJ-2': [SimpleNamespace(id='client-repo')]},
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(len(adopted), 1)
        recovered = self.workspace_service.get('PROJ-2')
        self.assertEqual(recovered.repository_ids, ['client-repo'])

    def test_skips_managed_folders_already_carrying_kato_metadata(self) -> None:
        _make_orphan(self.root, 'MANAGED', ['repo-a'], with_metadata=True)
        _make_orphan(self.root, 'PROJ-3', ['repo-a'])
        task_service = _RealishTaskService(
            assigned=[_make_task('MANAGED'), _make_task('PROJ-3')],
        )
        repo_service = _RealishRepoService(
            mapping={
                'MANAGED': [SimpleNamespace(id='repo-a')],
                'PROJ-3': [SimpleNamespace(id='repo-a')],
            },
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        # Only the orphan was adopted; the managed one was left alone.
        self.assertEqual([a.task_id for a in adopted], ['PROJ-3'])

    def test_skips_orphan_with_no_matching_repository_id(self) -> None:
        _make_orphan(self.root, 'PROJ-4', ['unknown-folder'])
        task_service = _RealishTaskService(assigned=[_make_task('PROJ-4')])
        repo_service = _RealishRepoService(
            mapping={'PROJ-4': [SimpleNamespace(id='different-repo')]},
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(adopted, [])
        # No metadata file was written — the folder is still an orphan.
        self.assertFalse((self.root / 'PROJ-4' / DEFAULT_METADATA_FILENAME).is_file())

    def test_orphan_with_no_git_subdirectories_is_skipped(self) -> None:
        # Just an empty folder under root — no .git anywhere.
        (self.root / 'PROJ-5').mkdir()
        task_service = _RealishTaskService(assigned=[_make_task('PROJ-5')])
        repo_service = _RealishRepoService(
            mapping={'PROJ-5': [SimpleNamespace(id='repo-a')]},
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(adopted, [])

    def test_orphan_with_no_matching_live_task_is_skipped(self) -> None:
        # Folder exists, but the task list doesn't include its id.
        _make_orphan(self.root, 'ghost-task', ['repo-a'])
        task_service = _RealishTaskService(assigned=[_make_task('OTHER-1')])
        repo_service = _RealishRepoService(
            mapping={'OTHER-1': [SimpleNamespace(id='repo-a')]},
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(adopted, [])
        self.assertFalse((self.root / 'ghost-task' / DEFAULT_METADATA_FILENAME).is_file())

    def test_returns_empty_when_task_service_fetches_fail_for_both_kinds(self) -> None:
        # If assigned + review BOTH explode, recovery bails out gracefully.
        _make_orphan(self.root, 'PROJ-6', ['repo-a'])
        task_service = _RealishTaskService(
            assigned_exc=RuntimeError('platform down'),
            review_exc=RuntimeError('platform down'),
        )
        repo_service = _RealishRepoService()

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(adopted, [])
        # No metadata was written — the orphan is still unmanaged.
        self.assertFalse((self.root / 'PROJ-6' / DEFAULT_METADATA_FILENAME).is_file())

    def test_per_orphan_failure_does_not_abort_recovery_of_the_rest(self) -> None:
        # First orphan's repo lookup explodes; second orphan recovers fine.
        _make_orphan(self.root, 'PROJ-BAD', ['repo-a'])
        _make_orphan(self.root, 'PROJ-OK', ['repo-a'])
        task_service = _RealishTaskService(assigned=[
            _make_task('PROJ-BAD'),
            _make_task('PROJ-OK'),
        ])

        def resolve(task):
            if task.id == 'PROJ-BAD':
                raise RuntimeError('lookup failure for PROJ-BAD')
            return [SimpleNamespace(id='repo-a')]

        repo_service = SimpleNamespace(resolve_task_repositories=resolve)
        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        # Bad one skipped, good one adopted.
        self.assertEqual([a.task_id for a in adopted], ['PROJ-OK'])
        # On disk: only PROJ-OK has a real metadata file. PROJ-BAD's
        # folder still exists (it's a leftover orphan) so it shows up
        # as a synthetic `errored` record in list_workspaces(), but its
        # metadata file was never written.
        self.assertTrue((self.root / 'PROJ-OK' / DEFAULT_METADATA_FILENAME).is_file())
        self.assertFalse((self.root / 'PROJ-BAD' / DEFAULT_METADATA_FILENAME).is_file())

    def test_assigned_takes_precedence_over_review_for_same_task_id(self) -> None:
        # Same id in both lists — recovery should still adopt once.
        _make_orphan(self.root, 'DUP-1', ['repo-a'])
        task_service = _RealishTaskService(
            assigned=[_make_task('DUP-1', summary='from assigned list')],
            review=[_make_task('DUP-1', summary='from review list')],
        )
        repo_service = _RealishRepoService(
            mapping={'DUP-1': [SimpleNamespace(id='repo-a')]},
        )

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(len(adopted), 1)
        recovered = self.workspace_service.get('DUP-1')
        # First-write-wins on summary, so 'from assigned list' is kept.
        self.assertEqual(recovered.task_summary, 'from assigned list')

    def test_orphan_recovery_handles_a_burst_of_chaos_task_ids(self) -> None:
        # Stress: every safe chaos task id, each with one repo on disk.
        tasks = [_make_task(tid) for tid in CHAOS_TASK_IDS_SAFE]
        mapping = {}
        for tid in CHAOS_TASK_IDS_SAFE:
            _make_orphan(self.root, tid, ['repo-a'])
            mapping[tid] = [SimpleNamespace(id='repo-a')]
        task_service = _RealishTaskService(assigned=tasks)
        repo_service = _RealishRepoService(mapping=mapping)

        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        adopted_ids = {a.task_id for a in adopted}
        for tid in CHAOS_TASK_IDS_SAFE:
            self.assertIn(tid, adopted_ids)
        # Every adopted workspace has the expected metadata on disk.
        managed = {w.task_id: w for w in self.workspace_service.list_workspaces()}
        for tid in CHAOS_TASK_IDS_SAFE:
            self.assertIn(tid, managed)
            self.assertEqual(managed[tid].repository_ids, ['repo-a'])

    def test_recovering_twice_is_a_no_op_on_the_second_pass(self) -> None:
        """Regression for the metadata-filename mismatch bug.

        Before fix: recovery checked for ``.kato-meta.json`` while the
        data layer wrote ``.workspace-meta.json``. The marker file the
        recovery looked for never appeared, so every boot re-adopted
        every already-managed workspace. After fix: the second pass
        sees the workspace metadata file and returns ``[]``.
        """
        _make_orphan(self.root, 'PROJ-RT', ['repo-a'])
        task_service = _RealishTaskService(assigned=[_make_task('PROJ-RT')])
        repo_service = _RealishRepoService(
            mapping={'PROJ-RT': [SimpleNamespace(id='repo-a')]},
        )
        service = self._build(task_service, repo_service)

        first = service.recover_orphan_workspaces()
        self.assertEqual([a.task_id for a in first], ['PROJ-RT'])
        # Now the workspace metadata file exists on disk.
        self.assertTrue(
            (self.root / 'PROJ-RT' / DEFAULT_METADATA_FILENAME).is_file(),
        )

        # Second recovery pass: workspace is no longer an orphan.
        second = service.recover_orphan_workspaces()
        self.assertEqual(second, [],
                         'second recovery should be a no-op — managed '
                         'workspaces must not be re-adopted')

    def test_second_recovery_does_not_revert_review_workspace_to_active(self) -> None:
        """The bug's actual blast radius: REVIEW workspaces flipping to ACTIVE.

        When the metadata-filename mismatch caused re-adoption every
        boot, ``_recover_one`` would call
        ``workspace_manager.update_status(..., WORKSPACE_STATUS_ACTIVE)``
        on every managed workspace — silently bumping anything in
        REVIEW (or any other status) back to ACTIVE. That violates
        the documented "never delete a review clone" invariant
        because the cleanup classifier protects REVIEW separately.
        """
        from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
            WORKSPACE_STATUS_REVIEW,
        )

        # Adopt PROJ-REV once, then move it to REVIEW (mirroring the
        # publish path that promotes a finished workspace to review).
        _make_orphan(self.root, 'PROJ-REV', ['repo-a'])
        task_service = _RealishTaskService(assigned=[_make_task('PROJ-REV')])
        repo_service = _RealishRepoService(
            mapping={'PROJ-REV': [SimpleNamespace(id='repo-a')]},
        )
        service = self._build(task_service, repo_service)
        first = service.recover_orphan_workspaces()
        self.assertEqual(len(first), 1)
        # Move it to REVIEW the way the real publish path does.
        self.workspace_service.update_status('PROJ-REV', WORKSPACE_STATUS_REVIEW)

        # Now run recovery again. The pre-fix code would re-adopt and
        # bump back to ACTIVE; the post-fix code must leave it alone.
        second = service.recover_orphan_workspaces()
        self.assertEqual(second, [])
        live = self.workspace_service.get('PROJ-REV')
        self.assertEqual(
            live.status, WORKSPACE_STATUS_REVIEW,
            'recovery silently dragged a REVIEW workspace back to ACTIVE — '
            'the metadata-filename mismatch bug must have regressed',
        )

    def test_workspace_root_does_not_exist_returns_empty_list_gracefully(self) -> None:
        # Wipe the workspace root before recovery runs.
        import shutil
        shutil.rmtree(self.root)
        task_service = _RealishTaskService(assigned=[_make_task('X')])
        repo_service = _RealishRepoService()

        # WorkspaceDataAccess recreates the root on init, so this is
        # effectively "root present but empty". Still no orphans.
        # (We deliberately re-instantiate to mirror a fresh boot.)
        self.workspace_service = build_real_workspace_service(self.root)
        adopted = self._build(task_service, repo_service).recover_orphan_workspaces()
        self.assertEqual(adopted, [])


if __name__ == '__main__':
    unittest.main()
