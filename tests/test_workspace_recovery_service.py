"""Unit tests for kato.data_layers.service.workspace_recovery_service."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_core_lib.agent_core_lib.helpers.session_id_utils import AGENT_SESSION_ID
from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ACTIVE,
)
from kato_core_lib.data_layers.service.workspace_recovery_service import (
    WorkspaceRecoveryService,
)
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib


class WorkspaceRecoveryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._workspaces_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._workspaces_tmp.cleanup)
        self.workspaces_root = Path(self._workspaces_tmp.name)
        # The recovery service runs against kato's workspace flavour:
        # ``.kato-meta.json`` filenames (kato deployments have these
        # on disk; the lib defaults to ``.workspace-meta.json``). Pin
        # the kato names here so the metadata-file assertions further
        # down hit the right path.
        self._lib = WorkspaceCoreLib(
            root=self.workspaces_root,
            max_parallel_tasks=4,
            metadata_filename='.kato-meta.json',
            preflight_log_filename='.kato-preflight.log',
        )
        self.workspace_manager = self._lib.workspaces

        self.repo = SimpleNamespace(id='client', summary='client repo')
        self.task = SimpleNamespace(id='PROJ-1', summary='do the thing', tags=[])

        self.task_service = MagicMock()
        self.task_service.get_assigned_tasks.return_value = [self.task]
        self.task_service.get_review_tasks.return_value = []

        self.repository_service = MagicMock()
        self.repository_service.resolve_task_repositories.return_value = [self.repo]

        self.service = WorkspaceRecoveryService(
            workspace_manager=self.workspace_manager,
            task_service=self.task_service,
            repository_service=self.repository_service,
        )

    def _stage_orphan(self, task_id: str = 'PROJ-1', repo_id: str = 'client') -> Path:
        orphan_dir = self.workspaces_root / task_id
        repo_dir = orphan_dir / repo_id
        repo_dir.mkdir(parents=True)
        (repo_dir / '.git').mkdir()
        (repo_dir / 'README.md').write_text('changes')
        return orphan_dir

    def test_adopts_orphan_with_matching_task_and_repo(self) -> None:
        orphan_dir = self._stage_orphan()

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(len(adopted), 1)
        record = adopted[0]
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.repository_ids, ['client'])
        meta_path = orphan_dir / '.kato-meta.json'
        self.assertTrue(meta_path.is_file())
        meta = json.loads(meta_path.read_text())
        self.assertEqual(meta['task_id'], 'PROJ-1')
        self.assertEqual(meta['repository_ids'], ['client'])
        self.assertEqual(meta['status'], WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(meta['task_summary'], 'do the thing')

    def test_skips_folder_with_existing_metadata(self) -> None:
        orphan_dir = self._stage_orphan()
        (orphan_dir / '.kato-meta.json').write_text('{}')

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(adopted, [])

    def test_skips_folder_without_git_subdir(self) -> None:
        orphan_dir = self.workspaces_root / 'PROJ-1'
        (orphan_dir / 'client').mkdir(parents=True)

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(adopted, [])
        self.assertFalse((orphan_dir / '.kato-meta.json').exists())

    def test_skips_folder_when_no_live_task_matches(self) -> None:
        self._stage_orphan(task_id='PROJ-99')

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(adopted, [])

    def test_skips_when_task_repos_dont_match_subfolders(self) -> None:
        self._stage_orphan(repo_id='unexpected-repo')

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(adopted, [])

    def test_uses_review_tasks_when_assigned_is_empty(self) -> None:
        self.task_service.get_assigned_tasks.return_value = []
        self.task_service.get_review_tasks.return_value = [self.task]
        self._stage_orphan()

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(len(adopted), 1)

    def test_recovery_is_resilient_to_task_service_errors(self) -> None:
        self.task_service.get_assigned_tasks.side_effect = RuntimeError('boom')
        self.task_service.get_review_tasks.return_value = [self.task]
        self._stage_orphan()

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(len(adopted), 1)

    def test_recovery_persists_agent_session_id_in_metadata(self) -> None:
        # Pin the load-bearing assertion: when recovery finds a Claude
        # session whose cwd matches the orphan's repo path, the
        # session id must end up in ``.kato-meta.json`` so the next
        # spawn can ``--resume`` cleanly. workspace_core_lib serializes
        # this under the generic ``agent_session_id`` key.
        from unittest.mock import patch as _patch

        orphan_dir = self._stage_orphan()
        repo_dir = orphan_dir / 'client'

        def fake_find(cwd, **_):
            return 'claude-sess-recovered' if str(cwd) == str(repo_dir) else ''

        with _patch(
            'kato_core_lib.data_layers.service.workspace_recovery_service.find_session_id_for_cwd',
            side_effect=fake_find,
        ):
            adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(len(adopted), 1)
        meta = json.loads((orphan_dir / '.kato-meta.json').read_text())
        self.assertEqual(meta[AGENT_SESSION_ID], 'claude-sess-recovered')
        self.assertEqual(meta['cwd'], str(repo_dir))

    def test_recovery_writes_empty_session_id_when_no_match_found(self) -> None:
        # When no Claude session matches the orphan's cwd, recovery
        # still adopts the workspace but records an empty session id —
        # never invents one. Future operator reruns can fix this when
        # they spot the empty field in the metadata.
        orphan_dir = self._stage_orphan()

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(len(adopted), 1)
        meta = json.loads((orphan_dir / '.kato-meta.json').read_text())
        self.assertEqual(meta.get(AGENT_SESSION_ID, ''), '')

    def test_recovery_warns_when_live_task_list_is_empty(self) -> None:
        # When both API calls fail, recovery can't match orphans to
        # live tasks. A visible warning prevents silent abandonment.
        self.task_service.get_assigned_tasks.side_effect = RuntimeError('network error')
        self.task_service.get_review_tasks.side_effect = RuntimeError('network error')
        self._stage_orphan()
        mock_logger = MagicMock()
        service = WorkspaceRecoveryService(
            workspace_manager=self.workspace_manager,
            task_service=self.task_service,
            repository_service=self.repository_service,
            logger=mock_logger,
        )

        adopted = service.recover_orphan_workspaces()

        self.assertEqual(adopted, [])
        warning_args = [call.args[0] for call in mock_logger.warning.call_args_list]
        self.assertTrue(
            any('orphan workspace recovery skipped' in msg for msg in warning_args),
            f'expected orphan-skipped warning, got: {warning_args}',
        )

    def test_recovery_proceeds_with_partial_task_list_when_one_api_call_fails(self) -> None:
        # If assigned-tasks fails but review-tasks succeeds, recovery
        # continues with what we have.
        self.task_service.get_assigned_tasks.side_effect = RuntimeError('network error')
        self.task_service.get_review_tasks.return_value = [self.task]
        self._stage_orphan()

        adopted = self.service.recover_orphan_workspaces()

        self.assertEqual(len(adopted), 1)


if __name__ == '__main__':
    unittest.main()
