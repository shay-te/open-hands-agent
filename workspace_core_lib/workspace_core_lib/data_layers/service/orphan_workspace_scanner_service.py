"""Find workspace folders that lack metadata.

A workspace folder under the configured root without a metadata
file is an *orphan*: someone (the operator? a previous tool? a
crash mid-create?) put a folder there but didn't register it. The
host application typically wants to:

1. Discover orphans.
2. Inspect their layout (which subfolders look like git checkouts).
3. Decide per orphan whether to adopt or skip — that decision is
   host-policy (does the task id correspond to a real ticket? do
   the subfolders match repos the host knows about?), so this lib
   does NOT make it.

This service owns the *find* and *inspect* steps. Adoption belongs
to the host application: it reads the orphan list from here, makes
its decision, then calls :meth:`WorkspaceService.create` to
register the survivor.

Pure scanning: no network, no git invocations, no host-specific
knowledge. Reads filesystem state and returns paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core_lib.data_layers.service.service import Service

from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    WorkspaceDataAccess,
)


_GIT_DIR_NAME = '.git'


@dataclass(frozen=True)
class OrphanWorkspace(object):
    """One unregistered workspace folder under the root.

    * ``path`` — absolute path of the folder.
    * ``task_id`` — the folder name (would-be task id if the host
      decides to adopt this orphan).
    * ``git_repository_dirs`` — names of immediate subdirectories
      that contain a ``.git`` directory (i.e. look like git
      checkouts the host might want to register as workspace
      repos).
    """

    path: Path
    task_id: str
    git_repository_dirs: tuple[str, ...]


class OrphanWorkspaceScannerService(Service):
    """Scan the workspace root for unregistered folders.

    Stateless. Each :meth:`scan` re-reads the filesystem so the
    host gets a fresh snapshot every call. The pattern is "scan
    once at boot, decide, adopt the survivors" — but nothing
    prevents repeated calls if a host wants to surface orphans
    interactively.
    """

    def __init__(self, data_access: WorkspaceDataAccess) -> None:
        if data_access is None:
            raise ValueError('data_access is required')
        self._data_access = data_access

    def scan(self) -> list[OrphanWorkspace]:
        """Return every orphan workspace currently on disk.

        Sorted by folder name so the result is deterministic and
        UIs can render a stable list.
        """
        results: list[OrphanWorkspace] = []
        for entry, has_metadata in self._data_access._iter_workspace_dirs(
            self._data_access.root
        ):
            if has_metadata:
                continue
            results.append(
                OrphanWorkspace(
                    path=entry,
                    task_id=entry.name,
                    git_repository_dirs=self._git_subdirs_in(entry),
                ),
            )
        return results

    @staticmethod
    def _git_subdirs_in(orphan_dir: Path) -> tuple[str, ...]:
        """Return immediate subdirs of ``orphan_dir`` that look like
        git checkouts.

        We match presence of a ``.git`` entry at the next level only
        (no recursion). Worktrees and submodules are out of scope —
        a host that needs them can re-walk the path.
        """
        names: list[str] = []
        try:
            entries = sorted(orphan_dir.iterdir())
        except OSError:
            return ()
        for entry in entries:
            if not entry.is_dir():
                continue
            if not (entry / _GIT_DIR_NAME).exists():
                continue
            names.append(entry.name)
        return tuple(names)
