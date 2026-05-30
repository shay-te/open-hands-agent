"""File I/O for the lessons subsystem.

File layout under ``state_dir``:

    state_dir/
      lessons.md                  <- compacted "core" lessons (the file
                                     injected into the Claude system
                                     prompt on every spawn)
      lessons/
        <task-id>.md              <- per-task pending lesson, one file
                                     per task. Overwritten every time
                                     the task is marked done. Deleted
                                     during compaction.

The global file's first line is a compaction timestamp:

    <!-- last_compacted: 2026-05-04T12:33:00Z -->

Used by ``LessonsService`` to decide whether to run the periodic
compact. The header is stripped before injection into the system
prompt — Claude doesn't need it.

This data-access layer is policy-free: it does not call any LLM, does
not decide what counts as a lesson, and does not enforce the compaction
schedule. Those decisions live in ``LessonsService``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
from kato_core_lib.helpers.logging_utils import configure_logger


_TIMESTAMP_PREFIX = '<!-- last_compacted: '
_TIMESTAMP_SUFFIX = ' -->'
_TIMESTAMP_PATTERN = re.compile(
    r'^<!--\s*last_compacted:\s*([0-9TZ:.\-+]+)\s*-->'
)


class LessonsDataAccess(object):
    """Read and write per-task and global lesson files."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self._global_path = self._state_dir / 'lessons.md'
        self._per_task_dir = self._state_dir / 'lessons'
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    # ----- global lessons file -----

    def read_global(self) -> str:
        """Return the raw global file contents, or empty string if absent."""
        if not self._global_path.is_file():
            return ''
        try:
            return self._global_path.read_text(encoding='utf-8')
        except OSError:
            self.logger.exception('failed to read global lessons at %s', self._global_path)
            return ''

    def read_global_body(self) -> str:
        """Return global lessons with the timestamp header stripped."""
        return strip_timestamp_header(self.read_global())

    def write_global(
        self,
        body: str,
        *,
        compacted_at: datetime | None = None,
    ) -> bool:
        """Write the global file with a fresh timestamp header.

        ``body`` is the lesson content. Any existing timestamp header in
        ``body`` is stripped before writing so consecutive compactions
        don't accumulate headers.
        """
        timestamp = (compacted_at or datetime.now(timezone.utc)).isoformat(
            timespec='seconds',
        )
        cleaned = strip_timestamp_header(body).lstrip()
        composed = f'{_TIMESTAMP_PREFIX}{timestamp}{_TIMESTAMP_SUFFIX}\n\n{cleaned}'
        if not composed.endswith('\n'):
            composed += '\n'
        return atomic_write_text(
            self._global_path,
            composed,
            logger=self.logger,
            label='global lessons',
        )

    def last_compacted_at(self) -> datetime | None:
        """Parse the timestamp header from the global file, or None."""
        if not self._global_path.is_file():
            return None
        first_line = self._read_first_line(self._global_path)
        match = _TIMESTAMP_PATTERN.match(first_line)
        if not match:
            return None
        try:
            return datetime.fromisoformat(match.group(1))
        except ValueError:
            return None

    # ----- per-task lessons -----

    def read_per_task(self, task_id: str) -> str | None:
        """Return per-task lesson content, or None if absent."""
        path = self._per_task_path(task_id)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            self.logger.exception(
                'failed to read per-task lesson for %s at %s', task_id, path,
            )
            return None

    def write_per_task(self, task_id: str, content: str) -> bool:
        """Overwrite the per-task lesson file. Idempotent — same task
        marked done repeatedly replaces the previous content."""
        path = self._per_task_path(task_id)
        if path is None:
            self.logger.warning(
                'rejected per-task lesson write for invalid task id %r', task_id,
            )
            return False
        body = content if content.endswith('\n') else content + '\n'
        return atomic_write_text(
            path,
            body,
            logger=self.logger,
            label=f'per-task lesson {task_id}',
        )

    def delete_per_task(self, task_id: str) -> None:
        """Remove the per-task lesson file. No-op when absent."""
        path = self._per_task_path(task_id)
        if path is None or not path.is_file():
            return
        try:
            path.unlink()
        except OSError:
            self.logger.exception(
                'failed to delete per-task lesson at %s', path,
            )

    def list_per_task_ids(self) -> list[str]:
        """Return sorted task ids that currently have pending lesson files."""
        if not self._per_task_dir.is_dir():
            return []
        ids = []
        for entry in sorted(self._per_task_dir.iterdir()):
            if entry.is_file() and entry.suffix == '.md':
                ids.append(entry.stem)
        return ids

    def read_all_per_task(self) -> dict[str, str]:
        """Return ``{task_id: content}`` for every pending per-task file."""
        out: dict[str, str] = {}
        for task_id in self.list_per_task_ids():
            content = self.read_per_task(task_id)
            if content is not None:
                out[task_id] = content
        return out

    # ----- internals -----

    def _per_task_path(self, task_id: str) -> Path | None:
        normalized = self._normalize_task_id(task_id)
        if not normalized:
            return None
        return self._per_task_dir / f'{normalized}.md'

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        # Reject characters and whole-string forms that would let a
        # caller escape the per-task directory or write outside the
        # state dir. Real task ids look like ``PROJ-123`` — none of
        # these are legitimate.
        if task_id is None:
            return ''
        normalized = str(task_id).strip()
        if not normalized:
            return ''
        if normalized in {'.', '..'}:
            return ''
        for forbidden in ('/', '\\', '..', '\x00'):
            if forbidden in normalized:
                return ''
        return normalized

    @staticmethod
    def _read_first_line(path: Path) -> str:
        try:
            with path.open('r', encoding='utf-8') as fh:
                return fh.readline()
        except OSError:
            return ''


def strip_timestamp_header(text: str) -> str:
    """Remove the leading ``<!-- last_compacted: ... -->`` line if present."""
    if not text:
        return ''
    lines = text.splitlines()
    if lines and _TIMESTAMP_PATTERN.match(lines[0]):
        return '\n'.join(lines[1:]).lstrip('\n')
    return text
