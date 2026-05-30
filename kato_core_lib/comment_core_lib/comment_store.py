"""JSON-backed per-workspace comment store. See ``LocalCommentStore``."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from kato_core_lib.comment_core_lib.comment_record import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
)
from kato_core_lib.helpers.logging_utils import configure_logger

# Cross-platform file locking. POSIX has fcntl; Windows has msvcrt.
try:
    import fcntl                            # type: ignore[import-not-found]
except ImportError:                          # pragma: no cover — Windows
    fcntl = None                            # type: ignore[assignment]
try:
    import msvcrt                           # type: ignore[import-not-found]
except ImportError:                          # POSIX
    msvcrt = None                           # type: ignore[assignment]


_STORE_FILENAME = '.kato-comments.json'


@contextlib.contextmanager
def _process_safe_write_lock(store_path: Path):
    """Cross-process exclusive lock for the store's read-modify-write."""
    lock_path = store_path.with_suffix(store_path.suffix + '.lock')
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
        with lock_path.open('a+b') as handle:
            handle.seek(0)
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
    yield


class LocalCommentStore(object):
    """JSON file at ``<workspace>/.kato-comments.json`` — CRUD + sync."""

    # Shared across instances pointing at the same store so two
    # callers in the same process (webserver thread + scanner
    # thread) can't both read the same baseline and lose updates.
    _locks_by_path: dict[Path, threading.RLock] = {}
    _locks_registry_lock = threading.Lock()

    def __init__(self, workspace_dir: str | Path) -> None:
        self._workspace_dir = Path(workspace_dir)
        self._path = self._workspace_dir / _STORE_FILENAME
        self._lock = self._lock_for(self._path)
        self.logger = configure_logger(self.__class__.__name__)
        # Cache + mtime so a long-lived instance sees cross-process writes.
        self._cache: list[CommentRecord] | None = None
        self._cache_mtime_ns: int = 0

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        # parent.resolve() + name, not full resolve() — the store
        # file may not exist yet at construction time.
        key = path.parent.resolve() / path.name
        with cls._locks_registry_lock:
            lock = cls._locks_by_path.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks_by_path[key] = lock
            return lock

    # ----- public API -----

    @property
    def storage_path(self) -> Path:
        return self._path

    def list(self) -> list[CommentRecord]:
        with self._lock:
            return list(self._load_all())

    def list_for_repo(self, repo_id: str) -> list[CommentRecord]:
        normalised = str(repo_id or '').strip().lower()
        if not normalised:
            return []
        return [
            record for record in self.list()
            if record.repo_id.lower() == normalised
        ]

    def get(self, comment_id: str) -> CommentRecord | None:
        target = str(comment_id or '').strip()
        if not target:
            return None
        with self._lock:
            for record in self._load_all():
                if record.id == target:
                    return record
        return None

    def add(self, record: CommentRecord) -> CommentRecord:
        """Append a new comment (or a reply if ``parent_id`` is set).

        Returns the persisted record so callers can read back any
        defaults the dataclass filled in. Raises ``ValueError`` on
        bad input — empty body / missing repo / stale parent —
        rather than silently dropping data.
        """
        body = str(record.body or '').strip()
        if not body:
            raise ValueError('comment body must be non-empty')
        if not str(record.repo_id or '').strip():
            raise ValueError('comment repo_id must be non-empty')
        # flock wraps the WHOLE RMW so a sibling process can't load the
        # same baseline and clobber us on rename.
        with self._lock, _process_safe_write_lock(self._path):
            if record.parent_id:
                parent = next(
                    (r for r in self._load_all(force=True)
                     if r.id == record.parent_id),
                    None,
                )
                if parent is None:
                    raise ValueError(
                        f'parent comment {record.parent_id!r} does not exist',
                    )
            existing = list(self._load_all(force=True))
            existing.append(record)
            self._persist(existing)
        return record

    def upsert_remote(self, record: CommentRecord) -> CommentRecord:
        """Insert or update a remote-sourced comment by ``remote_id``.

        Used by the sync path (``pull from source git platform``).
        Matches on ``remote_id`` so re-syncing the same remote
        comment doesn't duplicate it. Local-side fields kato cares
        about (``kato_status``, ``kato_addressed_sha``) are
        preserved across upserts so a fix kato pushed for a
        remote comment isn't blown away on the next sync.
        """
        if record.source != CommentSource.REMOTE.value:
            raise ValueError(
                'upsert_remote is only valid for source=remote records',
            )
        if not record.remote_id:
            raise ValueError('remote_id is required to upsert a remote comment')
        with self._lock, _process_safe_write_lock(self._path):
            existing = list(self._load_all(force=True))
            for index, current in enumerate(existing):
                if (
                    current.source == CommentSource.REMOTE.value
                    and current.remote_id == record.remote_id
                ):
                    # Preserve kato pipeline fields on update so a
                    # re-sync after kato has already addressed the
                    # comment doesn't reset its kato_status to IDLE.
                    record.kato_status = current.kato_status
                    record.kato_addressed_sha = current.kato_addressed_sha
                    record.kato_failure_reason = current.kato_failure_reason
                    existing[index] = record
                    self._persist(existing)
                    return record
            existing.append(record)
            self._persist(existing)
        return record

    def _mutate_by_id(self, comment_id: str, apply) -> CommentRecord | None:
        """Locked load → find ``comment_id`` → ``apply(record)`` → persist.

        Holds the in-process + cross-process write lock, force-reloads from
        disk, mutates the matching record in place via ``apply``, persists
        the full list, and returns the mutated record. Returns ``None`` when
        no record matches. Shared scaffold for the status mutators.
        """
        with self._lock, _process_safe_write_lock(self._path):
            existing = list(self._load_all(force=True))
            for index, current in enumerate(existing):
                if current.id != comment_id:
                    continue
                apply(current)
                existing[index] = current
                self._persist(existing)
                return current
        return None

    def update_status(
        self,
        comment_id: str,
        *,
        status: str | None = None,
        resolved_by: str = '',
    ) -> CommentRecord | None:
        """Open / resolve a thread (or its top-of-thread comment).

        Resolving the top-of-thread comment is what marks the
        whole thread resolved on the source git platform on next
        sync; replies inherit the thread's resolved state.
        """
        if status is not None and status not in (
            CommentStatus.OPEN.value,
            CommentStatus.RESOLVED.value,
        ):
            raise ValueError(f'unknown comment status: {status!r}')

        def _apply(current: CommentRecord) -> None:
            if status is not None:  # pragma: no branch - all production callers pass a status
                current.status = status
                if status == CommentStatus.RESOLVED.value:
                    current.resolved_by = resolved_by or current.resolved_by
                    current.resolved_at_epoch = time.time()
                else:
                    current.resolved_by = ''
                    current.resolved_at_epoch = 0.0

        return self._mutate_by_id(comment_id, _apply)

    def update_kato_status(
        self,
        comment_id: str,
        *,
        kato_status: str,
        addressed_sha: str = '',
        failure_reason: str = '',
    ) -> CommentRecord | None:
        """Move kato's own pipeline state on a comment.

        Called by the agent_service when an agent run starts /
        finishes. Independent of the operator-facing
        ``CommentStatus`` so kato can be done while the operator
        keeps the thread open for review.
        """
        if kato_status not in {item.value for item in KatoCommentStatus}:
            raise ValueError(f'unknown kato_status: {kato_status!r}')

        def _apply(current: CommentRecord) -> None:
            current.kato_status = kato_status
            if addressed_sha:
                current.kato_addressed_sha = addressed_sha
            if failure_reason:
                current.kato_failure_reason = failure_reason
            else:
                if kato_status == KatoCommentStatus.IDLE.value:
                    current.kato_failure_reason = ''

        return self._mutate_by_id(comment_id, _apply)

    def delete(self, comment_id: str) -> bool:
        """Remove a comment (and any direct replies). Returns True on hit."""
        with self._lock, _process_safe_write_lock(self._path):
            existing = list(self._load_all(force=True))
            removed = False
            kept: list[CommentRecord] = []
            ids_to_drop = {comment_id}
            # First pass: collect every reply chain rooted at the
            # target so we don't strand orphaned replies.
            changed = True
            while changed:
                changed = False
                for record in existing:
                    if record.id in ids_to_drop:
                        continue
                    if record.parent_id and record.parent_id in ids_to_drop:
                        ids_to_drop.add(record.id)
                        changed = True
            for record in existing:
                if record.id in ids_to_drop:
                    removed = True
                    continue
                kept.append(record)
            if removed:
                self._persist(kept)
            return removed

    def next_queued(self) -> CommentRecord | None:
        """Oldest QUEUED comment (FIFO). Empty when the queue is drained.

        The agent_service hook calls this on every "agent went
        idle" tick to drain comments one at a time.
        """
        queued = [
            record for record in self.list()
            if record.kato_status == KatoCommentStatus.QUEUED.value
        ]
        if not queued:
            return None
        queued.sort(key=lambda r: r.created_at_epoch)
        return queued[0]

    # ----- internals -----

    def _current_mtime_ns(self) -> int:
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return 0

    def _cache_empty(self, mtime: int) -> list:
        """Reset the cache to empty (stamped at ``mtime``) and return ``[]``."""
        self._cache = []
        self._cache_mtime_ns = mtime
        return []

    def _load_all(self, *, force: bool = False) -> list[CommentRecord]:
        # Cross-process writes bump the file mtime; drop the cache on change.
        current_mtime = self._current_mtime_ns()
        if (
            self._cache is not None
            and not force
            and current_mtime == self._cache_mtime_ns
        ):
            return list(self._cache)
        if not self._path.is_file():
            return self._cache_empty(current_mtime)
        try:
            with self._path.open('r', encoding='utf-8') as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning(
                'comment store at %s is unreadable (%s) — treating as empty',
                self._path, exc,
            )
            return self._cache_empty(current_mtime)
        if not isinstance(payload, dict):
            return self._cache_empty(current_mtime)
        rows = payload.get('comments') or []
        if not isinstance(rows, list):
            return self._cache_empty(current_mtime)
        out: list[CommentRecord] = []
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(CommentRecord.from_dict(entry))
            except (TypeError, ValueError):
                self.logger.warning(
                    'skipping malformed comment record in %s',
                    self._path,
                )
        self._cache = list(out)
        self._cache_mtime_ns = current_mtime
        return out

    def _persist(self, records: list[CommentRecord]) -> None:
        # Callers hold the cross-process flock; this just serialises
        # the file IO. pid/thread/uuid in the tmp name guards against
        # a stray writer that bypassed the lock.
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        payload = {'comments': [record.to_dict() for record in records]}
        tmp_path = self._path.with_suffix(
            self._path.suffix
            + f'.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp',
        )
        try:
            with tmp_path.open('w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            self.logger.warning(
                'failed to persist comment store at %s: %s', self._path, exc,
            )
            return
        self._cache = list(records)
        self._cache_mtime_ns = self._current_mtime_ns()
