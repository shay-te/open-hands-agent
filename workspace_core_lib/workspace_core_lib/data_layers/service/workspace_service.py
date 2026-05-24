"""High-level workspace API.

What callers reach for. Composes :class:`WorkspaceDataAccess` with
business behavior (status validation, partial updates, preflight
log, parallelism cap, path computation for sibling repo clones).

Layering: this is the only class the consumer should touch. Don't
bypass it for raw data-access calls — the validation and partial-update
semantics live here.

Thread safety: every public mutation acquires an internal lock so
concurrent callers (e.g. the orchestrator's main thread creating
workspaces while a webserver thread enumerates them for the tab
list) can't tear each other's writes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from core_lib.data_layers.service.service import Service

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    SUPPORTED_WORKSPACE_STATUSES,
    WORKSPACE_STATUS_PROVISIONING,
    WorkspaceRecord,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    WorkspaceDataAccess,
    _safe_segment,
)
from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    fix_session_id,
)


DEFAULT_PREFLIGHT_LOG_FILENAME = '.workspace-preflight.log'


class WorkspaceService(Service):
    """Public façade for workspace lifecycle.

    Owns:
    * Creating a workspace folder + writing its initial metadata.
    * Reading / listing / deleting workspaces (delegated to
      ``WorkspaceDataAccess`` but exposed here so callers don't
      reach across layers).
    * Computing per-repository clone paths and the preflight log
      path.
    * Partial updates that preserve unset fields (so a caller
      writing only ``agent_session_id`` doesn't blank ``cwd``).
    * Validating ``status`` transitions against the supported set.
    * Capping concurrency via a configured ``max_parallel_tasks``
      (informational; the lib doesn't refuse creates above the cap
      — callers do).
    """

    def __init__(
        self,
        data_access: WorkspaceDataAccess,
        *,
        max_parallel_tasks: int = 1,
        preflight_log_filename: str = DEFAULT_PREFLIGHT_LOG_FILENAME,
        logger: logging.Logger | None = None,
    ) -> None:
        if data_access is None:
            raise ValueError('data_access is required')
        if not str(preflight_log_filename or '').strip():
            raise ValueError('preflight_log_filename is required')
        self._data_access = data_access
        self._max_parallel_tasks = max(1, int(max_parallel_tasks or 1))
        self._preflight_log_filename = str(preflight_log_filename)
        self._lock = threading.RLock()
        self._logger = logger or logging.getLogger(self.__class__.__name__)

    # ----- accessors -----

    @property
    def root(self) -> Path:
        return self._data_access.root

    @property
    def max_parallel_tasks(self) -> int:
        return self._max_parallel_tasks

    @property
    def data_access(self) -> WorkspaceDataAccess:
        """Escape hatch for the recovery / scanner services in
        this same lib. Not part of the consumer-facing API."""
        return self._data_access

    # ----- path computation -----

    def workspace_path(self, task_id: str) -> Path:
        """Absolute path of the workspace folder for ``task_id``.

        Doesn't require the folder to exist.
        """
        return self._data_access.workspace_dir(task_id)

    def repository_path(self, task_id: str, repository_id: str) -> Path:
        """Where the named repository is (or would be) cloned."""
        return self.workspace_path(task_id) / _safe_segment(
            repository_id, label='repository_id',
        )

    def preflight_log_path(self, task_id: str) -> Path:
        """``<workspace>/<preflight-log-filename>`` — append-only
        provisioning step log. Consumed by UIs to show a chat-visible
        progress trail (cloning 1/3, ✓ cloned 1/3, ...)."""
        return self.workspace_path(task_id) / self._preflight_log_filename

    # ----- queries -----

    def exists(self, task_id: str) -> bool:
        return self._data_access.exists(task_id)

    def get(self, task_id: str) -> WorkspaceRecord | None:
        with self._lock:
            return self._data_access.get(task_id)

    def list_workspaces(self) -> list[WorkspaceRecord]:
        with self._lock:
            return self._data_access.list_all()

    # ----- create / update / delete -----

    def create(
        self,
        *,
        task_id: str,
        task_summary: str = '',
        repository_ids: list[str] | None = None,
    ) -> WorkspaceRecord:
        """Create the workspace folder + metadata. Idempotent.

        On a second call for the same ``task_id``, fields the caller
        didn't pass (empty ``task_summary``, ``None``
        ``repository_ids``) fall back to whatever the existing record
        had. ``created_at_epoch`` is preserved across calls.
        """
        with self._lock:
            existing = self._data_access.get(task_id)
            now = time.time()
            record = WorkspaceRecord(
                task_id=_safe_segment(task_id, label='task_id'),
                task_summary=(
                    str(task_summary or '').strip()
                    or (existing.task_summary if existing else '')
                ),
                status=(
                    existing.status if existing else WORKSPACE_STATUS_PROVISIONING
                ),
                repository_ids=(
                    [str(rid) for rid in (repository_ids or []) if rid]
                    or (list(existing.repository_ids) if existing else [])
                ),
                agent_session_id=(
                    existing.agent_session_id if existing else ''
                ),
                cwd=(existing.cwd if existing else ''),
                resume_on_startup=(
                    existing.resume_on_startup if existing else True
                ),
                created_at_epoch=(
                    existing.created_at_epoch if existing else now
                ),
                updated_at_epoch=now,
            )
            self._data_access.save(record)
            return record

    def update_status(
        self, task_id: str, status: str,
    ) -> WorkspaceRecord | None:
        if status not in SUPPORTED_WORKSPACE_STATUSES:
            raise ValueError(
                f'unknown workspace status: {status!r}; '
                f'supported: {sorted(SUPPORTED_WORKSPACE_STATUSES)}'
            )
        return self._mutate(task_id, lambda r: setattr(r, 'status', status))

    def update_agent_session(
        self,
        task_id: str,
        *,
        agent_session_id: str = '',
        cwd: str = '',
    ) -> WorkspaceRecord | None:
        """Persist the bound agent's session id and/or cwd.

        Both fields are optional — pass only what you have. Existing
        values are kept when the corresponding argument is empty so
        a partial update doesn't blank a previously-recorded id.
        """
        new_session = fix_session_id(agent_session_id)
        new_cwd = str(cwd or '').strip()

        def apply(record: WorkspaceRecord) -> None:
            if new_session:
                record.agent_session_id = new_session
            if new_cwd:
                record.cwd = new_cwd

        return self._mutate(task_id, apply)

    def update_resume_on_startup(
        self, task_id: str, resume_on_startup: bool,
    ) -> WorkspaceRecord | None:
        return self._mutate(
            task_id,
            lambda r: setattr(r, 'resume_on_startup', bool(resume_on_startup)),
        )

    def update_repositories(
        self, task_id: str, repository_ids: list[str],
    ) -> WorkspaceRecord | None:
        cleaned = [str(rid) for rid in (repository_ids or []) if rid]
        return self._mutate(
            task_id, lambda r: setattr(r, 'repository_ids', cleaned),
        )

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._data_access.delete(task_id)

    # ----- preflight log -----

    def append_preflight_log(self, task_id: str, message: str) -> None:
        """Append one line to the per-task preflight log (best-effort).

        The workspace folder must already exist (e.g. from a prior
        :meth:`create` call) — we don't auto-create here because a log
        line for a never-created workspace is almost certainly a bug.
        Filesystem errors are swallowed and logged: a status line is
        informational; failing to write it should never abort the
        provisioning step that triggered it.
        """
        text = str(message or '').strip()
        if not text:
            return
        with self._lock:
            path = self.preflight_log_path(task_id)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as fh:
                    fh.write(f'{int(time.time())}\t{text}\n')
            except OSError as exc:
                self._logger.warning(
                    'failed to append preflight log for task %s: %s',
                    task_id, exc,
                )

    def read_preflight_log(self, task_id: str) -> list[tuple[float, str]]:
        """``(epoch, message)`` pairs from the log, oldest first.

        Empty list when no log is on disk yet OR on any read error
        (callers treat absent log identically to "nothing to
        replay"). Tab-separated lines without a leading epoch parse
        with epoch=0 so very old logs (or hand-edited ones) still
        render.
        """
        path = self.preflight_log_path(task_id)
        if not path.is_file():
            return []
        out: list[tuple[float, str]] = []
        try:
            with path.open('r', encoding='utf-8') as fh:
                for raw in fh:
                    line = raw.rstrip('\n')
                    if not line:
                        continue
                    if '\t' not in line:
                        out.append((0.0, line))
                        continue
                    epoch_text, _, message = line.partition('\t')
                    try:
                        epoch = float(epoch_text)
                    except ValueError:
                        epoch = 0.0
                    out.append((epoch, message))
        except OSError as exc:
            self._logger.warning(
                'failed to read preflight log for task %s: %s',
                task_id, exc,
            )
            return []
        return out

    # ----- internals -----

    def _mutate(self, task_id: str, apply) -> WorkspaceRecord | None:
        """Read-modify-write helper for partial-update endpoints."""
        with self._lock:
            workspace_dir = self._data_access.workspace_dir(task_id)
            if not workspace_dir.is_dir():
                return None
            record = self._data_access.get(task_id)
            if record is None or not self._data_access.has_metadata(task_id):
                # Workspace folder exists but the metadata file
                # doesn't (or is unreadable). We refuse to seed a
                # synthetic record on update — callers should hit
                # :meth:`create` to fix that, not silently overwrite
                # whatever broken state is on disk.
                return None
            apply(record)
            record.updated_at_epoch = time.time()
            self._data_access.save(record)
            return record


def _normalize_text(value) -> str:  # noqa: ANN001 — runtime helper
    """Tiny text normalizer so the lib has no upstream string-helper
    dependency. ``None`` → ``''``, surrounding whitespace stripped.
    """
    return str(value or '').strip()
