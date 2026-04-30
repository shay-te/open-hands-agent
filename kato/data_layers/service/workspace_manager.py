"""Per-task workspace folders for parallel kato execution.

A *workspace* is a folder named after a YouTrack/Jira ticket id (e.g.
``PROJ-12/``) that contains a fresh clone of every repository the task
touches. The workspace is the source of truth for "which tasks are
currently in flight" — the planning UI tab list scans the workspace
root, one folder = one tab.

Lifecycle:

* Created when the orchestrator starts a task (clones happen lazily as
  ``RepositoryService`` runs, not in this module).
* Persisted on disk; survives kato restart.
* Tracks per-task metadata in ``<workspace>/.kato-meta.json``: summary,
  status, parallel slot, timestamps. The Claude session id is *not*
  stored here — we recover it by searching Claude's own session storage
  on resume.
* Deleted when the ticket transitions to "Done" (handled by the cleanup
  loop in ``AgentService``).

This module owns folder creation / deletion / metadata I/O only — it
does not run git, does not start subprocesses, does not know about
Flask. Pure infrastructure that mirrors :class:`ClaudeSessionManager`'s
shape but folder-backed.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kato.helpers.logging_utils import configure_logger
from kato.helpers.text_utils import normalized_text


WORKSPACE_STATUS_PROVISIONING = 'provisioning'
WORKSPACE_STATUS_ACTIVE = 'active'
WORKSPACE_STATUS_REVIEW = 'review'
WORKSPACE_STATUS_DONE = 'done'
WORKSPACE_STATUS_ERRORED = 'errored'
WORKSPACE_STATUS_TERMINATED = 'terminated'

SUPPORTED_WORKSPACE_STATUSES = frozenset(
    {
        WORKSPACE_STATUS_PROVISIONING,
        WORKSPACE_STATUS_ACTIVE,
        WORKSPACE_STATUS_REVIEW,
        WORKSPACE_STATUS_DONE,
        WORKSPACE_STATUS_ERRORED,
        WORKSPACE_STATUS_TERMINATED,
    }
)

_METADATA_FILENAME = '.kato-meta.json'


@dataclass
class WorkspaceRecord(object):
    """On-disk metadata for one task workspace.

    Stored as ``<workspace_dir>/.kato-meta.json``. The repository clones
    sit alongside it as sibling subdirectories.
    """

    task_id: str
    task_summary: str = ''
    status: str = WORKSPACE_STATUS_PROVISIONING
    repository_ids: list[str] = field(default_factory=list)
    created_at_epoch: float = field(default_factory=time.time)
    updated_at_epoch: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> 'WorkspaceRecord':
        repository_ids = payload.get('repository_ids') or []
        if not isinstance(repository_ids, list):
            repository_ids = []
        return cls(
            task_id=str(payload.get('task_id', '') or ''),
            task_summary=str(payload.get('task_summary', '') or ''),
            status=str(payload.get('status', WORKSPACE_STATUS_PROVISIONING)
                       or WORKSPACE_STATUS_PROVISIONING),
            repository_ids=[str(rid) for rid in repository_ids if rid],
            created_at_epoch=float(
                payload.get('created_at_epoch', time.time()) or time.time(),
            ),
            updated_at_epoch=float(
                payload.get('updated_at_epoch', time.time()) or time.time(),
            ),
        )


class WorkspaceManager(object):
    """Owns the workspace root: create, list, look up, delete folders.

    Thread-safe by design. The orchestrator's main scan thread creates
    workspaces; the parallel worker threads read them; the webserver
    enumerates them for the tab list — all concurrently.
    """

    DEFAULT_ROOT_DIR_NAME = '.kato/workspaces'

    @classmethod
    def from_config(
        cls,
        open_cfg,
        agent_backend: str,  # noqa: ARG003 — accepted for API parity
    ) -> 'WorkspaceManager':
        """Build the manager from the kato config block.

        Backend-agnostic: both Claude and OpenHands flows use workspaces
        for parallel isolation, so we don't gate on ``agent_backend``.
        """
        configured_root = normalized_text(
            getattr(open_cfg, 'workspaces_root', '')
            or os.environ.get('KATO_WORKSPACES_ROOT', '')
        )
        root = configured_root or str(Path.home() / cls.DEFAULT_ROOT_DIR_NAME)
        max_parallel = _coerce_positive_int(
            getattr(open_cfg, 'max_parallel_tasks', None)
            or os.environ.get('KATO_MAX_PARALLEL_TASKS', ''),
            default=1,
        )
        return cls(root=root, max_parallel_tasks=max_parallel)

    def __init__(
        self,
        *,
        root: str | os.PathLike[str],
        max_parallel_tasks: int = 1,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_parallel_tasks = max(1, int(max_parallel_tasks or 1))
        self._lock = threading.RLock()
        self.logger = configure_logger(self.__class__.__name__)

    # ----- public API -----

    @property
    def root(self) -> Path:
        return self._root

    @property
    def max_parallel_tasks(self) -> int:
        return self._max_parallel_tasks

    def workspace_path(self, task_id: str) -> Path:
        """Absolute path of the workspace folder for ``task_id``.

        Doesn't require the folder to exist; safe to use for
        "would this be the location" checks.
        """
        return self._root / self._safe_task_id(task_id)

    def repository_path(self, task_id: str, repository_id: str) -> Path:
        """Path the named repository should be cloned into."""
        return self.workspace_path(task_id) / self._safe_repository_id(repository_id)

    def exists(self, task_id: str) -> bool:
        return self.workspace_path(task_id).is_dir()

    def create(
        self,
        *,
        task_id: str,
        task_summary: str = '',
        repository_ids: list[str] | None = None,
    ) -> WorkspaceRecord:
        """Create the workspace folder + metadata. Idempotent."""
        with self._lock:
            workspace_dir = self.workspace_path(task_id)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            existing = self._read_metadata(workspace_dir)
            record = WorkspaceRecord(
                task_id=self._safe_task_id(task_id),
                task_summary=normalized_text(task_summary)
                or (existing.task_summary if existing else ''),
                status=(existing.status if existing else WORKSPACE_STATUS_PROVISIONING),
                repository_ids=list(repository_ids or [])
                or (list(existing.repository_ids) if existing else []),
                created_at_epoch=(
                    existing.created_at_epoch if existing else time.time()
                ),
                updated_at_epoch=time.time(),
            )
            self._write_metadata(workspace_dir, record)
            return record

    def get(self, task_id: str) -> WorkspaceRecord | None:
        with self._lock:
            workspace_dir = self.workspace_path(task_id)
            if not workspace_dir.is_dir():
                return None
            return self._read_metadata(workspace_dir) or WorkspaceRecord(
                task_id=self._safe_task_id(task_id),
                status=WORKSPACE_STATUS_ERRORED,
            )

    def list_workspaces(self) -> list[WorkspaceRecord]:
        """Snapshot of every workspace folder under the root.

        Folders without metadata get a synthetic ``errored`` record so
        the UI can offer the user a Discard button.
        """
        with self._lock:
            results: list[WorkspaceRecord] = []
            if not self._root.exists():
                return results
            for entry in sorted(self._root.iterdir()):
                if not entry.is_dir():
                    continue
                record = self._read_metadata(entry) or WorkspaceRecord(
                    task_id=entry.name,
                    status=WORKSPACE_STATUS_ERRORED,
                )
                results.append(record)
            return results

    def update_status(self, task_id: str, status: str) -> WorkspaceRecord | None:
        if status not in SUPPORTED_WORKSPACE_STATUSES:
            raise ValueError(
                f'unknown workspace status: {status!r}; '
                f'supported: {sorted(SUPPORTED_WORKSPACE_STATUSES)}'
            )
        with self._lock:
            workspace_dir = self.workspace_path(task_id)
            if not workspace_dir.is_dir():
                return None
            record = self._read_metadata(workspace_dir)
            if record is None:
                return None
            record.status = status
            record.updated_at_epoch = time.time()
            self._write_metadata(workspace_dir, record)
            return record

    def update_repositories(
        self,
        task_id: str,
        repository_ids: list[str],
    ) -> WorkspaceRecord | None:
        with self._lock:
            workspace_dir = self.workspace_path(task_id)
            if not workspace_dir.is_dir():
                return None
            record = self._read_metadata(workspace_dir)
            if record is None:
                return None
            record.repository_ids = [str(rid) for rid in repository_ids if rid]
            record.updated_at_epoch = time.time()
            self._write_metadata(workspace_dir, record)
            return record

    def delete(self, task_id: str) -> None:
        """Remove the workspace folder + everything inside.

        Idempotent: deleting a missing workspace is a no-op. Logs but
        doesn't raise on filesystem errors (a permission issue or NFS
        hiccup shouldn't tank the orchestrator's main loop).
        """
        with self._lock:
            workspace_dir = self.workspace_path(task_id)
            if not workspace_dir.exists():
                return
            try:
                shutil.rmtree(workspace_dir)
            except OSError as exc:
                self.logger.warning(
                    'failed to delete workspace for task %s at %s: %s',
                    task_id, workspace_dir, exc,
                )

    # ----- internals -----

    @staticmethod
    def _safe_segment(value: str, *, label: str) -> str:
        """Filename-safe slug for a single path segment.

        YouTrack/Jira ids and repository ids are conventionally
        filename-safe already (e.g. ``PROJ-123``, ``ob-love-admin-client``);
        we strip path separators defensively so a malicious or quirky
        source can't escape the workspace root via ``..``.
        """
        normalized = normalized_text(value)
        if not normalized:
            raise ValueError(f'{label} is required')
        return normalized.replace('/', '_').replace(os.sep, '_')

    @classmethod
    def _safe_task_id(cls, task_id: str) -> str:
        return cls._safe_segment(task_id, label='task_id')

    @classmethod
    def _safe_repository_id(cls, repository_id: str) -> str:
        return cls._safe_segment(repository_id, label='repository_id')

    @staticmethod
    def _metadata_path(workspace_dir: Path) -> Path:
        return workspace_dir / _METADATA_FILENAME

    def _read_metadata(self, workspace_dir: Path) -> WorkspaceRecord | None:
        path = self._metadata_path(workspace_dir)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning(
                'failed to read workspace metadata at %s: %s', path, exc,
            )
            return None
        if not isinstance(payload, dict):
            return None
        return WorkspaceRecord.from_dict(payload)

    def _write_metadata(self, workspace_dir: Path, record: WorkspaceRecord) -> None:
        path = self._metadata_path(workspace_dir)
        tmp_path = path.with_suffix('.json.tmp')
        try:
            tmp_path.write_text(
                json.dumps(record.to_dict(), indent=2, sort_keys=True),
                encoding='utf-8',
            )
            tmp_path.replace(path)
        except OSError as exc:
            self.logger.warning(
                'failed to persist workspace metadata at %s: %s', path, exc,
            )


def _coerce_positive_int(value, *, default: int) -> int:
    if value in (None, ''):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def provision_task_workspace_clones(
    workspace_manager: 'WorkspaceManager | None',
    repository_service,
    task,
    repositories: list,
):
    """Clone (or reuse) per-task workspace copies of ``repositories``.

    Returns shallow copies of the inventory ``Repository`` objects with
    ``local_path`` rewritten to point at the workspace clone path. The
    inventory originals are never mutated, so concurrent tasks never
    share branch state.

    No-op when ``workspace_manager`` is None — the autonomous and
    wait-planning flows fall through to the legacy "use existing local
    clones" path. On any error after the workspace folder is created,
    the workspace is marked ``errored`` so the UI can prompt the user.
    """
    if workspace_manager is None or not repositories:
        return repositories
    repository_ids = [
        getattr(r, 'id', '') for r in repositories if getattr(r, 'id', '')
    ]
    workspace_manager.create(
        task_id=str(task.id),
        task_summary=str(getattr(task, 'summary', '') or ''),
        repository_ids=repository_ids,
    )
    provisioned: list = []
    try:
        for repository in repositories:
            clone_path = workspace_manager.repository_path(
                str(task.id), repository.id,
            )
            repository_service.ensure_clone(repository, clone_path)
            rewritten = copy.copy(repository)
            rewritten.local_path = str(clone_path)
            provisioned.append(rewritten)
    except Exception:
        workspace_manager.update_status(str(task.id), WORKSPACE_STATUS_ERRORED)
        raise
    workspace_manager.update_status(str(task.id), WORKSPACE_STATUS_ACTIVE)
    return provisioned
