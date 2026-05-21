"""Adopt orphan workspace folders that kato didn't create itself.

A user can drop a repo clone under ``KATO_WORKSPACES_ROOT/<task_id>/<repo>/``
out-of-band — for example, if they cloned and started editing manually
before kato was running, or copied a folder from another machine. The
folder won't have the workspace metadata file so kato has no idea it
exists, and the planning UI's tab list will skip it.

This service runs once at startup, finds those orphan folders, and
adopts them as if kato had provisioned the workspace itself:

* The folder name must match an assigned-or-review task id in the
  configured ticket system, and that task must carry the kato repo
  tags pointing at the repos sitting in the folder.
* Each subfolder is verified to be a real git checkout.
* If Claude already ran a session inside the folder (Claude Code
  records every conversation under ``~/.claude/projects``), we look up
  the matching session id by ``cwd`` and store it.
* A fresh workspace metadata file is written so future kato cycles
  treat the workspace exactly like one it provisioned itself.

Folders that don't match (no live task, no tags, empty, or already
managed) are left alone — recovery is opt-in by virtue of having a
real task behind the folder name.

Pure orchestration: no Flask, no subprocess, no git invocations. Reads
``WorkspaceManager``, ``TaskService`` and the Claude session locator;
calls ``WorkspaceManager.create`` + ``update_claude_session`` /
``update_status`` to register the recovered workspace.
"""

from __future__ import annotations

import logging
from pathlib import Path

from claude_core_lib.claude_core_lib.session.history import find_session_id_for_cwd
from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ACTIVE,
    WorkspaceManager,
    WorkspaceRecord,
)
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.text_utils import normalized_text
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
)


_GIT_DIR = '.git'


class WorkspaceRecoveryService(object):
    """Adopt out-of-band task folders into the workspace registry."""

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        task_service,
        repository_service,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        if workspace_manager is None:
            raise ValueError('workspace_manager is required')
        if task_service is None:
            raise ValueError('task_service is required')
        if repository_service is None:
            raise ValueError('repository_service is required')
        self._workspace_manager = workspace_manager
        self._task_service = task_service
        self._repository_service = repository_service
        self.logger = logger or configure_logger(self.__class__.__name__)

    def recover_orphan_workspaces(self) -> list[WorkspaceRecord]:
        """Walk the workspaces root and adopt every recoverable orphan.

        Returns the records kato just adopted (empty list when there's
        nothing to recover). Best-effort: any single folder that fails
        to recover is logged and skipped — recovery never blocks boot.
        """
        orphan_dirs = self._collect_orphan_directories()
        if not orphan_dirs:
            return []
        live_tasks_by_id = self._fetch_live_tasks_by_id()
        if not live_tasks_by_id:
            self.logger.warning(
                'orphan workspace recovery skipped: could not fetch any live '
                'tasks from the ticket system (%d orphan folder(s) left '
                'unadopted). Check the ticket service connection and restart '
                'kato to retry.',
                len(orphan_dirs),
            )
            return []
        adopted: list[WorkspaceRecord] = []
        failed: list[Path] = []
        for orphan_dir in orphan_dirs:
            try:
                record = self._recover_one(orphan_dir, live_tasks_by_id)
            except Exception:
                self.logger.exception(
                    'failed to recover orphan workspace %s', orphan_dir,
                )
                failed.append(orphan_dir)
                continue
            if record is not None:
                adopted.append(record)
        # Surface a one-line summary even when some folders failed so
        # operators reviewing boot logs see the count without digging
        # through the per-folder exception traces. Previously a
        # permission-denied on one folder was invisible unless the
        # operator scrolled through tracebacks.
        if failed:
            self.logger.warning(
                'orphan recovery completed with errors: '
                'adopted %d, failed %d. Failed folders: %s. '
                'Re-check filesystem permissions and re-run kato to retry.',
                len(adopted), len(failed),
                ', '.join(str(p) for p in failed),
            )
        return adopted

    def _collect_orphan_directories(self) -> list[Path]:
        """Folders under workspace root that lack the workspace metadata file.

        The metadata filename is whatever the configured workspace
        ``data_access`` actually writes. Kato pins ``.kato-meta.json``
        (see ``_KATO_METADATA_FILENAME`` in workspace_manager.py); the
        bare ``workspace_core_lib`` default is ``.workspace-meta.json``.
        Asking the data_access (rather than hardcoding a literal) means
        a future filename change in one place can't drift this check.
        """
        root = self._workspace_manager.root
        if not root.exists():
            return []
        metadata_filename = self._metadata_filename()
        orphans: list[Path] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if (entry / metadata_filename).is_file():
                continue
            orphans.append(entry)
        return orphans

    def _metadata_filename(self) -> str:
        """Filename the configured ``data_access`` writes for metadata.

        Falls back to ``DEFAULT_METADATA_FILENAME`` if the workspace_manager
        doesn't expose ``data_access`` (defensive — every real
        ``WorkspaceService`` from workspace_core_lib does, but a future
        wrapper might not).
        """
        data_access = getattr(self._workspace_manager, 'data_access', None)
        filename = getattr(data_access, 'metadata_filename', None)
        return filename or DEFAULT_METADATA_FILENAME

    def _fetch_live_tasks_by_id(self) -> dict[str, object]:
        """Index of task_id → task for tasks worth recovering against."""
        tasks: dict[str, object] = {}
        try:
            for task in self._task_service.get_assigned_tasks():
                tasks[str(task.id)] = task
        except Exception:
            self.logger.exception('failed to fetch assigned tasks during recovery')
        try:
            for task in self._task_service.get_review_tasks():
                tasks.setdefault(str(task.id), task)
        except Exception:
            self.logger.exception('failed to fetch review tasks during recovery')
        return tasks

    def _recover_one(
        self,
        orphan_dir: Path,
        live_tasks_by_id: dict[str, object],
    ) -> WorkspaceRecord | None:
        task_id = orphan_dir.name
        task = live_tasks_by_id.get(task_id)
        if task is None:
            self.logger.debug(
                'skipping orphan folder %s: no live task with that id', orphan_dir,
            )
            return None
        repository_dirs = self._git_repository_subdirs(orphan_dir)
        if not repository_dirs:
            self.logger.info(
                'skipping orphan folder %s: no git checkouts inside', orphan_dir,
            )
            return None
        try:
            task_repositories = self._repository_service.resolve_task_repositories(task)
        except Exception:
            self.logger.exception(
                'skipping orphan folder %s: cannot resolve repositories for task %s',
                orphan_dir, task_id,
            )
            return None
        repository_ids = self._match_repository_ids(repository_dirs, task_repositories)
        if not repository_ids:
            self.logger.info(
                'skipping orphan folder %s: subfolders do not match any repository '
                'declared by task %s',
                orphan_dir, task_id,
            )
            return None
        first_repo_dir = orphan_dir / repository_dirs[0]
        agent_session_id = find_session_id_for_cwd(str(first_repo_dir))
        record = self._workspace_manager.create(
            task_id=task_id,
            task_summary=normalized_text(getattr(task, 'summary', '')),
            repository_ids=repository_ids,
        )
        self._workspace_manager.update_status(task_id, WORKSPACE_STATUS_ACTIVE)
        self._workspace_manager.update_agent_session(
            task_id,
            agent_session_id=agent_session_id,
            cwd=str(first_repo_dir),
        )
        self.logger.info(
            'recovered orphan workspace for task %s (%d repo%s, '
            'agent_session_id=%s)',
            task_id,
            len(repository_ids),
            '' if len(repository_ids) == 1 else 's',
            agent_session_id or '<none>',
        )
        return record

    @staticmethod
    def _git_repository_subdirs(orphan_dir: Path) -> list[str]:
        names: list[str] = []
        for entry in sorted(orphan_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / _GIT_DIR).exists():
                continue
            names.append(entry.name)
        return names

    @staticmethod
    def _match_repository_ids(
        repository_dirs: list[str],
        task_repositories: list[object],
    ) -> list[str]:
        """Pick repository ids whose name lines up with a folder on disk.

        Workspace clones are written at ``<workspace>/<repo_id>/`` so the
        folder name should equal the repository id from the inventory.
        We tolerate exact and case-insensitive matches; no fuzzy match —
        an unfamiliar folder name should fall through to "skip" so we
        don't silently adopt the wrong layout.
        """
        folder_set = {name for name in repository_dirs}
        folder_set_lower = {name.lower() for name in repository_dirs}
        matched: list[str] = []
        for repository in task_repositories:
            repository_id = str(getattr(repository, 'id', '') or '')
            if not repository_id:
                continue
            if repository_id in folder_set or repository_id.lower() in folder_set_lower:
                matched.append(repository_id)
        return matched
