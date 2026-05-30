"""Persistent store for the Restricted Execution Protocol (REP).

The sidecar lives at ``~/.kato/approved-repositories.json`` (override
via ``KATO_APPROVED_REPOSITORIES_PATH`` for tests). Layout is the
``ApprovalSidecar`` schema in
``kato_core_lib.data_layers.data.repository_approval``.

Why a JSON sidecar, not a SQLite table or a config-file entry:

- Per-operator-per-machine policy (per the plan, deliberately not
  shared across machines). A single file in the operator's home
  directory is the natural shape.
- Operator can read / hand-edit it. Auditing "what did kato just
  approve?" is `cat ~/.kato/approved-repositories.json`.
- No new runtime dependency. ``json`` + a file lock is enough.

Concurrency: writes hold an advisory lock around the read-modify-write
cycle so two operators clicking the planning-UI approval button at
the same time can't lose an entry. Reads are lock-free — the file is
a few hundred bytes even for the largest plausible operator
deployment.

Corruption tolerance: a missing or unreadable file is treated as
"no approvals on record" and logged once. We never crash the boot
loop because of a malformed sidecar — the fail-closed posture of
REP (refuse all unapproved repos) keeps that safe.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Optional

# Cross-platform file locking. POSIX has fcntl; Windows has msvcrt.
try:
    import fcntl                            # type: ignore[import-not-found]
except ImportError:                          # pragma: no cover — Windows
    fcntl = None                            # type: ignore[assignment]
try:
    import msvcrt                           # type: ignore[import-not-found]
except ImportError:                          # POSIX
    msvcrt = None                           # type: ignore[assignment]

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.data.repository_approval import (
    ApprovalMode,
    ApprovalSidecar,
    RepositoryApproval,
    now_epoch,
)
from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.text_utils import normalized_lower_text, normalized_text


APPROVED_REPOSITORIES_PATH_ENV_KEY = 'KATO_APPROVED_REPOSITORIES_PATH'
OPERATOR_EMAIL_ENV_KEY = 'KATO_OPERATOR_EMAIL'


def default_storage_path() -> Path:
    return kato_home_path(
        'approved-repositories.json',
        env_key=APPROVED_REPOSITORIES_PATH_ENV_KEY,
    )


@contextlib.contextmanager
def _process_safe_write_lock(sidecar_path: Path):
    """Cross-process exclusive lock for the sidecar's read-modify-write.

    POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking`` on a
    sidecar lockfile.
    """
    lock_path = sidecar_path.with_suffix(sidecar_path.suffix + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        with lock_path.open('a+') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        # msvcrt.locking is byte-range; lock a single byte at offset 0.
        # The lockfile only ever needs to exist — we never read its bytes.
        with lock_path.open('a+b') as handle:
            handle.seek(0)
            # LK_LOCK blocks for up to ~10s then raises; retry forever
            # so a long-running sibling writer doesn't surface as a
            # spurious approval failure.
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    # Neither — degrade to no-op (rare; only on a platform without
    # either module). Callers still have the in-process Lock.
    yield


def operator_identity(env: dict | None = None) -> str:
    source = env if env is not None else os.environ
    explicit = str(source.get(OPERATOR_EMAIL_ENV_KEY, '') or '').strip()
    if explicit:
        return explicit
    user = str(source.get('USER', '') or source.get('USERNAME', '') or '').strip()
    return user or 'unknown'


class RepositoryApprovalService(Service):
    """Read/write the approval sidecar; expose a thread-safe lookup API."""

    # Shared across instances pointing at the same sidecar so the
    # webserver + scanner threads can't both read the same baseline
    # and lose updates on write.
    _locks_by_path: dict[Path, Lock] = {}
    _locks_registry_lock = Lock()

    def __init__(
        self,
        storage_path: Path | str | None = None,
        *,
        logger=None,
    ) -> None:
        self.logger = logger or configure_logger(self.__class__.__name__)
        self._storage_path = Path(storage_path).expanduser() if storage_path else default_storage_path()
        self._cache: ApprovalSidecar | None = None
        # File mtime at cache time — drops cache on cross-process writes.
        self._cache_mtime_ns: int = 0
        self._write_lock = self._lock_for(self._storage_path)
        self._corrupt_warned = False

    @classmethod
    def _lock_for(cls, path: Path) -> Lock:
        # parent.resolve() + name, not full resolve() — the sidecar
        # file may not exist yet at construction time.
        key = path.parent.resolve() / path.name
        with cls._locks_registry_lock:
            lock = cls._locks_by_path.get(key)
            if lock is None:
                lock = Lock()
                cls._locks_by_path[key] = lock
            return lock

    # ----- public API -----

    @property
    def storage_path(self) -> Path:
        return self._storage_path

    def is_approved(self, repository_id: str) -> Optional[ApprovalMode]:
        """Return the approval mode if the repo is approved, else None.

        ``None`` means "no record" — REP refuses the task. A returned
        ``ApprovalMode`` means "operator explicitly approved this id".
        """
        normalised = normalized_lower_text(repository_id)
        if not normalised:
            return None
        sidecar = self._read_sidecar()
        for entry in sidecar.approved:
            if entry.repository_id == normalised:
                return entry.approval_mode
        return None

    def lookup(self, repository_id: str) -> RepositoryApproval | None:
        normalised = normalized_lower_text(repository_id)
        if not normalised:
            return None
        sidecar = self._read_sidecar()
        for entry in sidecar.approved:
            if entry.repository_id == normalised:
                return entry
        return None

    def list_approvals(self) -> tuple[RepositoryApproval, ...]:
        return self._read_sidecar().approved

    def approve(
        self,
        repository_id: str,
        remote_url: str,
        *,
        mode: ApprovalMode | str = ApprovalMode.RESTRICTED,
        approved_by: str | None = None,
    ) -> RepositoryApproval:
        """Add or upgrade an approval entry. Idempotent on (id, mode).

        Re-approving the same id with the same mode and remote_url is
        a no-op (the existing record stays). Re-approving with a
        different mode upgrades / downgrades. Re-approving with a
        different remote_url updates the URL — the operator running
        ``approve-repo`` is asserting they trust the current remote.
        """
        normalised_id = normalized_lower_text(repository_id)
        if not normalised_id:
            raise ValueError('repository_id must be non-empty')
        approval_mode = mode if isinstance(mode, ApprovalMode) else ApprovalMode.from_string(mode)
        identity = approved_by or operator_identity()
        normalised_url = normalized_text(remote_url)
        # flock wraps the WHOLE read-modify-write so a sibling process
        # can't load the same baseline and clobber us on rename.
        with self._write_lock, _process_safe_write_lock(self._storage_path):
            sidecar = self._read_sidecar(force=True)
            existing = next(
                (entry for entry in sidecar.approved if entry.repository_id == normalised_id),
                None,
            )
            if existing and existing.approval_mode == approval_mode and existing.remote_url == normalised_url:
                return existing
            new_entry = RepositoryApproval(
                repository_id=normalised_id,
                remote_url=normalised_url,
                approved_at_epoch=now_epoch(),
                approved_by=identity,
                approval_mode=approval_mode,
            )
            updated = tuple(
                entry for entry in sidecar.approved if entry.repository_id != normalised_id
            ) + (new_entry,)
            self._write_sidecar(replace(sidecar, approved=updated))
            return new_entry

    def revoke(self, repository_id: str) -> bool:
        """Drop an approval entry. Returns True when something was removed."""
        normalised_id = normalized_lower_text(repository_id)
        if not normalised_id:
            return False
        with self._write_lock, _process_safe_write_lock(self._storage_path):
            sidecar = self._read_sidecar(force=True)
            updated = tuple(
                entry for entry in sidecar.approved if entry.repository_id != normalised_id
            )
            if len(updated) == len(sidecar.approved):
                return False
            self._write_sidecar(replace(sidecar, approved=updated))
            return True

    def unapproved_repository_ids(self, repositories: Iterable[object]) -> list[str]:
        """Filter ``repositories`` down to ids that lack approval records.

        Convenience for preflight: hand a resolved repo set in, get
        back the subset that REP must refuse. Order is preserved so
        the operator-facing comment lists ids the same way they appear
        in the task.
        """
        unapproved: list[str] = []
        for repository in repositories:
            repo_id = normalized_lower_text(getattr(repository, 'id', ''))
            if not repo_id:
                continue
            if self.is_approved(repo_id) is None:
                unapproved.append(repo_id)
        return unapproved

    def restricted_mode_repository_ids(
        self, repositories: Iterable[object],
    ) -> list[str]:
        """Return the subset of ``repositories`` approved in RESTRICTED mode.

        Used by preflight to decide whether the runtime posture gate
        applies. TRUSTED-mode repos opt out of the stricter posture
        because the operator has explicitly elevated them after
        review.
        """
        ids: list[str] = []
        for repository in repositories:
            repo_id = normalized_lower_text(getattr(repository, 'id', ''))
            if not repo_id:
                continue
            if self.is_approved(repo_id) == ApprovalMode.RESTRICTED:
                ids.append(repo_id)
        return ids

    # ----- internals -----

    def _read_sidecar(self, *, force: bool = False) -> ApprovalSidecar:
        path = self._storage_path
        # Cross-process writes bump the mtime; drop the cache on change.
        current_mtime = self._current_mtime_ns()
        if (
            self._cache is not None
            and not force
            and current_mtime == self._cache_mtime_ns
        ):
            return self._cache
        if not path.is_file():
            self._cache = ApprovalSidecar()
            self._cache_mtime_ns = current_mtime
            return self._cache
        try:
            with path.open('r', encoding='utf-8') as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            if not self._corrupt_warned:
                self.logger.warning(
                    'approval sidecar at %s is unreadable or corrupt; '
                    'treating as empty (no approvals on record). '
                    'Restore or delete the file to recover.',
                    path,
                )
                self._corrupt_warned = True
            self._cache = ApprovalSidecar()
            self._cache_mtime_ns = current_mtime
            return self._cache
        if not isinstance(payload, dict):
            self._cache = ApprovalSidecar()
            self._cache_mtime_ns = current_mtime
            return self._cache
        self._cache = ApprovalSidecar.from_dict(payload)
        self._cache_mtime_ns = current_mtime
        return self._cache

    def _current_mtime_ns(self) -> int:
        try:
            return self._storage_path.stat().st_mtime_ns
        except OSError:
            return 0

    def _write_sidecar(self, sidecar: ApprovalSidecar) -> None:
        # Callers hold the lock. Unique tmp name guards against a
        # stray writer that bypassed it.
        path = self._storage_path
        path.parent.mkdir(parents=True, exist_ok=True)
        import threading
        import uuid
        tmp_path = path.with_suffix(
            path.suffix
            + f'.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp',
        )
        with tmp_path.open('w', encoding='utf-8') as handle:
            json.dump(sidecar.to_dict(), handle, indent=2, sort_keys=True)
            handle.write('\n')
        os.replace(tmp_path, path)
        self._cache = sidecar
        self._cache_mtime_ns = self._current_mtime_ns()


class RestrictedExecutionRefusal(RuntimeError):
    """Raised when preflight refuses a task because of unapproved repos.

    The exception carries the list of unapproved ids so the failure
    handler can build a single ticket comment listing all of them.
    """

    def __init__(self, repository_ids: list[str]) -> None:
        self.repository_ids = list(repository_ids)
        joined = ', '.join(self.repository_ids) or '<none>'
        super().__init__(
            f'restricted execution protocol refused task: '
            f'no approval on record for repository id(s) {joined}.\n'
            f'\n'
            f'**How to fix:** run `./kato approve-repo` on the host '
            f'where kato runs. The picker shows every repo it can '
            f'find (kato config + workspaces + your '
            f'`REPOSITORY_ROOT_PATH` checkouts) with `[x]` next to '
            f'the ones already approved. Type the index of '
            f'`{joined}` to toggle it on, press Enter to apply, '
            f'then re-run this task.'
        )


class RestrictedModePostureViolation(RuntimeError):
    """Raised when a RESTRICTED-mode repo would run with a weak posture.

    The plan promises that the *first* run against a newly-approved
    repo gets extra scrutiny: docker-on, bypass-off, scanner blocks at
    MEDIUM severity. Threading per-task config overrides through the
    Claude CLI client + planning runner + implementation service
    would touch every spawn site. Instead, we enforce the constraint
    at preflight: when *any* repo on the task is RESTRICTED-approved
    AND the global posture is weaker than the plan requires, refuse
    the task with a message that names the specific knob.

    The operator has two outs:

    1. Strengthen the global posture (set ``KATO_CLAUDE_DOCKER=true``
       / unset ``KATO_CLAUDE_BYPASS_PERMISSIONS`` / tighten the
       scanner block list) and restart kato.
    2. Elevate the repo to ``trusted`` mode after reviewing the first
       agent run — re-run ``./kato approve-repo``, toggle the repo,
       and answer "yes" to the trusted-mode question on apply.

    Either way, no RESTRICTED-mode repo ever runs under the lax
    posture, which matches the plan's security promise without
    rewriting the spawn pipeline.
    """

    def __init__(
        self,
        repository_ids: list[str],
        violations: list[str],
    ) -> None:
        self.repository_ids = list(repository_ids)
        self.violations = list(violations)
        ids_text = ', '.join(self.repository_ids) or '<none>'
        violations_text = '; '.join(self.violations) or 'unknown violation'
        super().__init__(
            f'restricted execution protocol refused task: repository id(s) '
            f'{ids_text} are RESTRICTED-mode approved, but the global kato '
            f'posture violates restricted-mode requirements: '
            f'{violations_text}.\n'
            f'\n'
            f'**How to fix:** either tighten the global posture and '
            f'restart kato (set `KATO_CLAUDE_DOCKER=true`, unset '
            f'`KATO_CLAUDE_BYPASS_PERMISSIONS`, tighten the scanner '
            f'block list — whichever the violation above names), '
            f'OR elevate the repo(s) to trusted mode by re-running '
            f'`./kato approve-repo` and answering "yes" to the '
            f'trusted-mode question on apply.'
        )


@dataclass(frozen=True)
class RuntimePosture:
    """Snapshot of the global posture knobs REP enforces against.

    Holding these in a dataclass keeps the preflight gate signature
    flat — and lets tests build a posture without mocking the whole
    open-config object graph.
    """

    bypass_permissions: bool
    docker_mode_on: bool
    scanner_blocks_at_medium: bool


def restricted_mode_posture_violations(posture: RuntimePosture) -> list[str]:
    """Return human-readable strings describing each posture violation.

    Empty list means the posture meets restricted-mode requirements.
    Order is fixed so test assertions don't have to sort.
    """
    violations: list[str] = []
    if posture.bypass_permissions:
        violations.append(
            'KATO_CLAUDE_BYPASS_PERMISSIONS=true (restricted mode requires false)'
        )
    if not posture.docker_mode_on:
        violations.append(
            'KATO_CLAUDE_DOCKER is not enabled (restricted mode requires docker on)'
        )
    if not posture.scanner_blocks_at_medium:
        violations.append(
            'security scanner block_on_severity does not include MEDIUM '
            '(restricted mode requires the stricter threshold)'
        )
    return violations
