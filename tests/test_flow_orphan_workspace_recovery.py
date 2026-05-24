"""Flow #9 — Boot-time recovery of orphan workspace folders.

A-Z scenario:

    1. Previous kato run created a workspace at
       ``<root>/<task_id>/<repo_name>/``, did some work, then died
       before persisting the workspace metadata.
    2. The folder exists on disk WITHOUT ``.kato-meta.json`` — orphan.
    3. New kato boots, calls ``recover_orphan_workspaces``.
    4. Recovery walks the root, finds orphan folders.
    5. For each orphan: look up live task, check git checkouts exist,
       verify repo names match what the ticket says, find any agent
       session id under ``~/.claude/projects/``, then register.
    6. Workspace registry rehydrated; UI tab comes back.

Why this matters: without recovery, every kato restart loses the
work-in-progress folders. A long-running task with hundreds of MB
cloned would be re-cloned from scratch — slow AND breaks any
uncommitted edits.

Adversarial regression modes pinned here:
    - Orphan with NO git checkouts (folder created but clones
      failed): must skip cleanly.
    - Orphan with git checkouts whose names DON'T match the task's
      declared repos: must skip (we may be looking at someone
      else's folder layout).
    - Live task not in the ticket system anymore: must skip.
    - Ticket system unreachable: must NOT crash boot.
    - Single orphan exception must NOT abort other orphan recovery.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.helpers.session_id_utils import AGENT_SESSION_ID
from kato_core_lib.data_layers.service.workspace_recovery_service import (
    WorkspaceRecoveryService,
)


def _make_orphan_folder(root, task_id, repo_names):
    """Create ``<root>/<task_id>/<repo>/.git`` for each repo. Returns the
    orphan path. NO ``.kato-meta.json`` is written — that's what makes
    it orphan."""
    orphan = root / task_id
    orphan.mkdir(parents=True, exist_ok=True)
    for r in repo_names:
        (orphan / r / '.git').mkdir(parents=True, exist_ok=True)
    return orphan


def _make_managed_folder(root, task_id, repo_names):
    """Folder with ``.kato-meta.json`` — NOT orphan; recovery must skip it."""
    folder = _make_orphan_folder(root, task_id, repo_names)
    (folder / '.kato-meta.json').write_text('{}', encoding='utf-8')
    return folder


def _make_service(workspace_manager, task_service, repository_service):
    return WorkspaceRecoveryService(
        workspace_manager=workspace_manager,
        task_service=task_service,
        repository_service=repository_service,
    )


def _stub_workspace_manager(root):
    mgr = MagicMock()
    mgr.root = Path(root)
    return mgr


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


class FlowOrphanRecoveryHappyPathTests(unittest.TestCase):

    def test_flow_orphan_recovery_rehydrates_a_live_task_folder(self) -> None:
        # A → Z: orphan folder for live task T1 with one matching repo.
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            _make_orphan_folder(root_path, 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root_path)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='fix login bug'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
            ]

            with patch(
                'kato_core_lib.data_layers.service.workspace_recovery_service.'
                'find_session_id_for_cwd', return_value='sess-abc',
            ):
                service = _make_service(workspace_mgr, task_service, repo_service)
                adopted = service.recover_orphan_workspaces()

            workspace_mgr.create.assert_called_once()
            workspace_mgr.update_status.assert_called_once()
            workspace_mgr.update_agent_session.assert_called_once()
            # Adopted call included the recovered session id.
            call = workspace_mgr.update_agent_session.call_args
            self.assertEqual(call.kwargs.get(AGENT_SESSION_ID), 'sess-abc')

    def test_flow_orphan_recovery_works_without_finding_session_id(self) -> None:
        # Recovery must NOT depend on Claude's projects dir being
        # present. Adoption proceeds without a session id; the next
        # message will start fresh.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
            ]

            with patch(
                'kato_core_lib.data_layers.service.workspace_recovery_service.'
                'find_session_id_for_cwd', return_value=None,
            ):
                service = _make_service(workspace_mgr, task_service, repo_service)
                service.recover_orphan_workspaces()

            workspace_mgr.create.assert_called_once()

    def test_flow_orphan_recovery_skips_managed_folder(self) -> None:
        # A folder WITH ``.kato-meta.json`` is not orphan — it's
        # already registered. Recovery must not double-register.
        with tempfile.TemporaryDirectory() as root:
            _make_managed_folder(Path(root), 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()

            service = _make_service(workspace_mgr, task_service, repo_service)
            service.recover_orphan_workspaces()

            workspace_mgr.create.assert_not_called()

    def test_flow_orphan_recovery_handles_review_tasks_too(self) -> None:
        # Tasks in "In Review" state must also be recoverable —
        # otherwise an in-progress PR review loses its workspace.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = []
            task_service.get_review_tasks.return_value = [
                SimpleNamespace(id='T1', summary='in review work'),
            ]
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
            ]

            with patch(
                'kato_core_lib.data_layers.service.workspace_recovery_service.'
                'find_session_id_for_cwd', return_value=None,
            ):
                service = _make_service(workspace_mgr, task_service, repo_service)
                service.recover_orphan_workspaces()

            workspace_mgr.create.assert_called_once()


# ---------------------------------------------------------------------------
# Negative paths: when recovery MUST skip a folder.
# ---------------------------------------------------------------------------


class FlowOrphanRecoverySkipPathTests(unittest.TestCase):

    def test_flow_orphan_recovery_skips_when_task_id_not_live(self) -> None:
        # Folder named ``T1`` but no live task with that id — skip.
        # Adopting unknown tasks would surface phantom tabs in the UI.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T-DEAD', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T-LIVE', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()

            service = _make_service(workspace_mgr, task_service, repo_service)
            adopted = service.recover_orphan_workspaces()

            self.assertEqual(adopted, [])
            workspace_mgr.create.assert_not_called()

    def test_flow_orphan_recovery_skips_when_no_git_subdir(self) -> None:
        # Folder exists but has no ``.git`` subdirs — probably a
        # half-broken clone. Adopt it would leave kato thinking
        # there's a real workspace where there's nothing usable.
        with tempfile.TemporaryDirectory() as root:
            orphan = Path(root) / 'T1'
            orphan.mkdir()
            # No git subdirs.

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()

            service = _make_service(workspace_mgr, task_service, repo_service)
            service.recover_orphan_workspaces()

            workspace_mgr.create.assert_not_called()

    def test_flow_orphan_recovery_skips_when_repo_names_dont_match(self) -> None:
        # On-disk repo names don't match what the ticket says. Could
        # be a folder from a different ticket layout. Skip — better
        # to flag than adopt the wrong thing.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['UNKNOWN-REPO'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
                SimpleNamespace(id='repo-b'),
            ]

            service = _make_service(workspace_mgr, task_service, repo_service)
            service.recover_orphan_workspaces()

            workspace_mgr.create.assert_not_called()

    def test_flow_orphan_recovery_skips_when_repo_resolution_fails(self) -> None:
        # ``resolve_task_repositories`` can raise on ticket-system
        # quirks. The orphan should be skipped (with logging), not
        # cascade-fail other orphans.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.side_effect = RuntimeError(
                'ticket system flaky',
            )

            service = _make_service(workspace_mgr, task_service, repo_service)
            service.recover_orphan_workspaces()

            workspace_mgr.create.assert_not_called()


# ---------------------------------------------------------------------------
# Robustness: ticket system unreachable, one bad orphan among many.
# ---------------------------------------------------------------------------


class FlowOrphanRecoveryRobustnessTests(unittest.TestCase):

    def test_flow_orphan_recovery_skips_all_when_ticket_system_returns_empty(self) -> None:
        # If we can't fetch any tasks, recovery returns [] rather
        # than crashing — boot continues, operator sees the warning
        # in logs.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = []
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()

            service = _make_service(workspace_mgr, task_service, repo_service)
            adopted = service.recover_orphan_workspaces()

            self.assertEqual(adopted, [])

    def test_flow_orphan_recovery_continues_after_ticket_service_raises(self) -> None:
        # ``get_assigned_tasks`` raises (transient connection error).
        # Recovery falls through to ``get_review_tasks`` rather than
        # killing boot.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            task_service.get_assigned_tasks.side_effect = RuntimeError('unreachable')
            task_service.get_review_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
            ]
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
            ]

            with patch(
                'kato_core_lib.data_layers.service.workspace_recovery_service.'
                'find_session_id_for_cwd', return_value=None,
            ):
                service = _make_service(workspace_mgr, task_service, repo_service)
                service.recover_orphan_workspaces()

            # Recovery still happened from review_tasks.
            workspace_mgr.create.assert_called_once()

    def test_flow_orphan_recovery_one_bad_orphan_does_not_block_others(self) -> None:
        # Two orphans, one will fail during ``_recover_one``. The
        # other MUST still recover. Bug-finder: a regression that
        # let an exception bubble out of the per-orphan loop would
        # take out everything queued after the bad one.
        with tempfile.TemporaryDirectory() as root:
            _make_orphan_folder(Path(root), 'T1', ['repo-a'])
            _make_orphan_folder(Path(root), 'T2', ['repo-a'])

            workspace_mgr = _stub_workspace_manager(root)
            # ``create`` raises for T1 only.
            def _create(**kwargs):
                if kwargs.get('task_id') == 'T1':
                    raise RuntimeError('disk full')
                return MagicMock()
            workspace_mgr.create.side_effect = _create

            task_service = MagicMock()
            task_service.get_assigned_tasks.return_value = [
                SimpleNamespace(id='T1', summary='go'),
                SimpleNamespace(id='T2', summary='go'),
            ]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
            ]

            with patch(
                'kato_core_lib.data_layers.service.workspace_recovery_service.'
                'find_session_id_for_cwd', return_value=None,
            ):
                service = _make_service(workspace_mgr, task_service, repo_service)
                service.recover_orphan_workspaces()

            # ``create`` was called twice (both orphans attempted).
            self.assertEqual(workspace_mgr.create.call_count, 2)

    def test_flow_orphan_recovery_returns_empty_when_root_does_not_exist(self) -> None:
        # First-ever kato run: workspaces root may not exist yet.
        # Recovery must not crash.
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'does-not-exist'

            workspace_mgr = _stub_workspace_manager(root)
            task_service = MagicMock()
            repo_service = MagicMock()

            service = _make_service(workspace_mgr, task_service, repo_service)
            adopted = service.recover_orphan_workspaces()
            self.assertEqual(adopted, [])


if __name__ == '__main__':
    unittest.main()
