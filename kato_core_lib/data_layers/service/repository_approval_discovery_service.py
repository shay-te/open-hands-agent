"""Discover candidate repositories for the approval UI.

Pulls candidates from three sources, then de-duplicates by id with
inventory winning on conflict (its ``remote_url`` is the value kato
actually uses at task time, so we always show that even if a clone
on disk has a rewritten origin):

* **inventory** — kato's ``repositories:`` config block. Ground
  truth for "what repos can kato touch". Even repos with no on-disk
  clone yet show up here, so the operator can pre-approve before
  the first task.
* **checkout** — ``<REPOSITORY_ROOT_PATH>/<repo>/`` clones the
  operator already pushed/pulled outside kato. Same source the CLI
  ``approve-repo`` picker walked.
* **workspace** — ``<KATO_WORKSPACES_ROOT>/<task>/<repo>/`` per-task
  clones kato itself made. Useful for revoking access to repos kato
  touched on previous tasks.

This module replaces the CLI ``scripts/approve_repository.py``
discovery functions. The webserver's repository-approvals route
imports from here; the CLI is gone (operator-facing UI now lives
in the planning UI's Settings drawer).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredRepository(object):
    """One repo candidate offered by the approvals UI.

    ``source`` lets the UI render where the entry came from
    (inventory / checkout / workspace) so the operator can spot if
    the URL kato would record differs from what the config says.
    ``workspace_path`` is empty for inventory-only entries (no clone
    yet); ``task_id`` is only set for workspace entries.
    """

    repository_id: str
    remote_url: str
    source: str
    workspace_path: str = ''
    task_id: str = ''


def discover_all_repositories() -> list[DiscoveredRepository]:
    """One-call API: gather every candidate, merged in priority order."""
    return _merge_sources(
        discover_inventory_repositories(),
        discover_checkout_repositories(_resolve_repository_root()),
        discover_workspace_repositories(_resolve_workspaces_root()),
    )


def discover_inventory_repositories() -> list[DiscoveredRepository]:
    """Read kato's ``repositories`` config block.

    Best-effort: returns ``[]`` when kato isn't configured, hydra
    isn't importable, or the config file is missing — callers fall
    back to the on-disk scans.
    """
    try:
        from omegaconf import OmegaConf

        from kato_core_lib.data_layers.service.repository_inventory_service import (
            RepositoryInventoryService,
        )
    except Exception:
        return []
    config_path = _kato_config_path()
    if config_path is None or not config_path.is_file():
        return []
    try:
        cfg = OmegaConf.load(str(config_path))
        repositories_cfg = (
            getattr(getattr(cfg, 'kato', cfg), 'repositories', None)
            or getattr(cfg, 'repositories', None)
        )
        service = RepositoryInventoryService(repositories_cfg)
        repos = service.repositories
    except Exception:
        return []
    out: list[DiscoveredRepository] = []
    seen: set[str] = set()
    for repo in repos:
        repo_id = str(getattr(repo, 'id', '') or '').strip()
        remote_url = str(getattr(repo, 'remote_url', '') or '').strip()
        if not repo_id or not remote_url:
            continue
        key = repo_id.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(DiscoveredRepository(
            repository_id=repo_id,
            remote_url=remote_url,
            source='inventory',
        ))
    return sorted(out, key=lambda r: r.repository_id.lower())


def discover_checkout_repositories(root: Path | None) -> list[DiscoveredRepository]:
    """Walk ``<REPOSITORY_ROOT_PATH>/<repo>/`` one level deep for clones."""
    if root is None or not root.is_dir():
        return []
    discovered: list[DiscoveredRepository] = []
    seen_ids: set[str] = set()
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir():
            continue
        entry = _discovered_repo_from_clone(repo_dir, 'checkout', seen_ids)
        if entry is not None:
            discovered.append(entry)
    return discovered


def discover_workspace_repositories(root: Path) -> list[DiscoveredRepository]:
    """Walk ``<KATO_WORKSPACES_ROOT>/<task>/<repo>/`` for kato's own clones."""
    if not root.is_dir():
        return []
    discovered: list[DiscoveredRepository] = []
    seen_ids: set[str] = set()
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir():
            continue
        for repo_dir in sorted(task_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            entry = _discovered_repo_from_clone(
                repo_dir, 'workspace', seen_ids, task_id=task_dir.name,
            )
            if entry is not None:
                discovered.append(entry)
    return discovered


def _discovered_repo_from_clone(
    repo_dir: Path,
    source: str,
    seen_ids: set[str],
    task_id: str = '',
) -> DiscoveredRepository | None:
    """Build a ``DiscoveredRepository`` for a single clone directory.

    Returns ``None`` (and leaves ``seen_ids`` untouched) when ``repo_dir``
    isn't a git clone, has no origin URL, or its lowercased id was already
    seen. Shared by the checkout and workspace walkers.
    """
    if not (repo_dir / '.git').exists():
        return None
    remote_url = _read_origin_url(repo_dir)
    if not remote_url:
        return None
    repo_id = repo_dir.name
    key = repo_id.lower()
    if key in seen_ids:
        return None
    seen_ids.add(key)
    return DiscoveredRepository(
        repository_id=repo_id,
        remote_url=remote_url,
        source=source,
        workspace_path=str(repo_dir),
        task_id=task_id,
    )


def _read_origin_url(repo_dir: Path) -> str:
    """``git -C <repo_dir> remote get-url origin``. Empty on failure."""
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_dir), 'remote', 'get-url', 'origin'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def _merge_sources(
    *source_lists: list[DiscoveredRepository],
) -> list[DiscoveredRepository]:
    """First-source-wins de-dup. Inventory is canonical, checkout next."""
    by_id: dict[str, DiscoveredRepository] = {}
    for source_list in source_lists:
        for repo in source_list:
            by_id.setdefault(repo.repository_id.lower(), repo)
    return sorted(by_id.values(), key=lambda r: r.repository_id.lower())


def _resolve_workspaces_root() -> Path:
    configured = os.environ.get('KATO_WORKSPACES_ROOT', '').strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / '.kato' / 'workspaces'


def _resolve_repository_root() -> Path | None:
    configured = os.environ.get('REPOSITORY_ROOT_PATH', '').strip()
    if not configured:
        return None
    return Path(configured).expanduser()


def _kato_config_path() -> Path | None:
    configured = os.environ.get('KATO_CONFIG', '').strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    candidates = [
        Path.cwd() / '.kato' / 'kato.yaml',
        Path.cwd() / 'kato.yaml',
        Path.home() / '.kato' / 'kato.yaml',
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
