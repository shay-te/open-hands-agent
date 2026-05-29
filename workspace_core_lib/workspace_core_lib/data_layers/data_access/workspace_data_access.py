"""Persistence for :class:`WorkspaceRecord` objects.

Each workspace folder carries its own metadata file
(``<workspace>/<metadata-filename>``). This data-access class owns
every read/write of those files. Service-layer code never touches
the filesystem directly — it goes through here.

Design:

* **One source of truth per workspace.** The metadata file lives
  inside the workspace folder, so a workspace and its metadata move
  together (delete the folder = delete the record).
* **No domain logic.** This class doesn't know what the fields
  mean; it just round-trips JSON through :class:`WorkspaceRecord`.
* **Atomic writes.** A torn ``.json`` would block the planning UI
  (``JSONDecodeError`` on every list call). All writes use
  :func:`atomic_write_json`.
* **Configurable filename.** Defaults to ``.workspace-meta.json``
  but the metadata filename is constructor-injectable so existing
  deployments with a legacy filename can keep working without a
  disk migration.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core_lib.data_layers.data_access.data_access import DataAccess

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WorkspaceRecord,
)
from workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils import (
    atomic_write_json,
)


DEFAULT_METADATA_FILENAME = '.workspace-meta.json'


class WorkspaceDataAccess(DataAccess):
    """Read/write workspace records on the filesystem.

    Each ``task_id`` maps 1:1 to ``<root>/<task_id>/<metadata-file>``.
    The class is stateless apart from the configured root and
    filename — safe to call from multiple threads (the underlying
    ``atomic_write_json`` is process-safe; readers can race writers
    and either see the old or the new payload, never a torn one).
    """

    def __init__(
        self,
        *,
        root: str | os.PathLike[str],
        metadata_filename: str = DEFAULT_METADATA_FILENAME,
        logger: logging.Logger | None = None,
    ) -> None:
        if not str(root or '').strip():
            raise ValueError('root is required')
        if not str(metadata_filename or '').strip():
            raise ValueError('metadata_filename is required')
        self._root = Path(root)
        self._metadata_filename = str(metadata_filename)
        self._logger = logger or logging.getLogger(self.__class__.__name__)
        self._root.mkdir(parents=True, exist_ok=True)

    # ----- accessors -----

    @property
    def root(self) -> Path:
        return self._root

    @property
    def metadata_filename(self) -> str:
        return self._metadata_filename

    def workspace_dir(self, task_id: str) -> Path:
        """Folder a workspace's contents live in.

        Doesn't require the folder to exist (callers use this for
        "would this be the location" checks before calling
        :meth:`create`).
        """
        return self._root / _safe_segment(task_id, label='task_id')

    def metadata_path(self, task_id: str) -> Path:
        return self.workspace_dir(task_id) / self._metadata_filename

    # ----- queries -----

    def exists(self, task_id: str) -> bool:
        """True iff the workspace folder is on disk.

        Doesn't require valid metadata — a folder without a metadata
        file still counts (orphan adoption flow needs to discover
        these).
        """
        return self.workspace_dir(task_id).is_dir()

    def has_metadata(self, task_id: str) -> bool:
        return self.metadata_path(task_id).is_file()

    def get(self, task_id: str) -> WorkspaceRecord | None:
        """Read one record, or ``None`` if the folder is missing.

        Returns a synthetic ``errored`` record when the folder
        exists but the metadata file doesn't (or is unreadable).
        That lets a UI render a "Discard" button instead of dropping
        the entry entirely.
        """
        workspace_dir = self.workspace_dir(task_id)
        if not workspace_dir.is_dir():
            return None
        record = self._read_metadata_at(workspace_dir)
        if record is not None:
            return record
        from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
            WORKSPACE_STATUS_ERRORED,
        )
        return WorkspaceRecord(
            task_id=workspace_dir.name,
            status=WORKSPACE_STATUS_ERRORED,
        )

    def _iter_workspace_dirs(self, root: Path):
        """Yield ``(dir, has_metadata)`` for every immediate
        subdirectory of ``root``, sorted by name.

        Non-directories are skipped. ``has_metadata`` is ``True`` when
        the workspace's metadata file is present in the folder. The
        generator does NOT filter on the flag — callers want opposite
        subsets (``list_all`` takes all, the orphan scanner takes only
        those without metadata), so the predicate stays on the caller.
        """
        if not root.exists():
            return
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            yield entry, (entry / self._metadata_filename).is_file()

    def list_all(self) -> list[WorkspaceRecord]:
        """Snapshot of every workspace folder under the root."""
        results: list[WorkspaceRecord] = []
        for entry, _has_metadata in self._iter_workspace_dirs(self._root):
            record = self._read_metadata_at(entry)
            if record is None:
                from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
                    WORKSPACE_STATUS_ERRORED,
                )
                record = WorkspaceRecord(
                    task_id=entry.name,
                    status=WORKSPACE_STATUS_ERRORED,
                )
            results.append(record)
        return results

    # ----- mutations -----

    def ensure_workspace_dir(self, task_id: str) -> Path:
        """Create the workspace folder if missing, return its path.

        Idempotent. The metadata file is NOT written here — call
        :meth:`save` for that.
        """
        workspace_dir = self.workspace_dir(task_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir

    def save(self, record: WorkspaceRecord) -> None:
        """Persist ``record`` to its workspace folder's metadata file.

        Creates the workspace folder if it doesn't already exist.
        Uses an atomic write so concurrent readers never see a torn
        file.
        """
        if not record.task_id:
            raise ValueError('record.task_id is required')
        workspace_dir = self.ensure_workspace_dir(record.task_id)
        atomic_write_json(
            workspace_dir / self._metadata_filename,
            record.to_dict(),
            logger=self._logger,
            label='workspace metadata',
        )

    def delete(self, task_id: str) -> None:
        """Remove the workspace folder and everything inside it.

        Idempotent: deleting a missing workspace is a no-op. Logs but
        doesn't raise on filesystem errors so a permission glitch on
        one task can't block cleanup of others — the caller verifies
        ``workspace_dir.exists()`` after to detect partial failures.

        Windows-specific: file locks on .git/index, .pack files, and
        any file held by a recently-killed process can cause
        ``rmtree`` to fail with PermissionError. ``onerror`` flips
        read-only bits and retries once; a short post-delete retry
        catches the case where the OS is slow to release a handle
        after we just terminated the subprocess.
        """
        import shutil
        import stat
        import time
        workspace_dir = self.workspace_dir(task_id)
        if not workspace_dir.exists():
            return

        def _on_rm_error(func, path, exc_info):
            # Most Windows rmtree failures are read-only files (git
            # pack files, .git/index lock). Flip the bit and retry
            # the operation that failed.
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except OSError:
                # Re-raise the ORIGINAL exception so the outer
                # try/except sees a meaningful trace.
                raise exc_info[1]

        for attempt in range(3):
            try:
                shutil.rmtree(workspace_dir, onerror=_on_rm_error)
                return
            except OSError as exc:
                if attempt == 2:
                    self._logger.warning(
                        'failed to delete workspace for task %s at %s '
                        'after 3 attempts: %s '
                        '(likely a file lock — close any process with '
                        'open handles in this clone)',
                        task_id, workspace_dir, exc,
                    )
                    return
                # Brief pause lets the OS release handles from a
                # subprocess we just terminated (Windows is slow to
                # propagate the close).
                time.sleep(0.5)

    # ----- internals -----

    def _read_metadata_at(self, workspace_dir: Path) -> WorkspaceRecord | None:
        path = workspace_dir / self._metadata_filename
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            self._logger.warning(
                'failed to read workspace metadata at %s: %s', path, exc,
            )
            return None
        if not isinstance(payload, dict):
            return None
        return WorkspaceRecord.from_dict(payload)


def _safe_segment(value: str, *, label: str) -> str:
    """Reject empty + strip path separators from a filename segment.

    Defends against ``..`` / ``a/b`` slipping into a task or
    repository id and escaping the workspace root. Doesn't try to
    sanitize unicode or other quirks — callers are expected to pass
    well-formed identifiers (e.g. ``PROJ-123``, ``my-repo``).
    """
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{label} is required')
    return normalized.replace('/', '_').replace(os.sep, '_')
