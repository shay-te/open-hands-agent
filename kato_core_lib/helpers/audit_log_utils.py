"""Append-only audit log for "what did kato do recently?".

Writes one NDJSON record per task completion / review-fix completion
/ task failure to ``~/.kato/audit.log.jsonl``. Read by ``./kato
history`` and by anyone who wants to grep / tail the file directly.

Design notes:

- **Append-only.** Records are never rewritten or deleted. Concurrent
  kato workers (parallel runner) are safe because POSIX ``O_APPEND``
  makes individual writes atomic up to PIPE_BUF (~4KB) — well above
  the per-record size we write here.
- **Best-effort.** A failed audit write must NEVER bubble up as a
  task failure. Audit is observability, not a correctness gate. We
  log the write error and continue.
- **Disjoint from `.kato-meta.json`.** The per-workspace metadata
  file is for recovery; this is for history. They serve different
  purposes and live in different paths.
- **Test override.** ``KATO_AUDIT_LOG_PATH`` points the writer +
  reader at a temp file so unit tests don't pollute the operator's
  real history.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from kato_core_lib.helpers.kato_paths_utils import kato_home_path


AUDIT_LOG_PATH_ENV_KEY = 'KATO_AUDIT_LOG_PATH'

EVENT_TASK_COMPLETED = 'task_completed'
EVENT_REVIEW_FIX_COMPLETED = 'review_fix_completed'
EVENT_TASK_FAILED = 'task_failed'

OUTCOME_SUCCESS = 'success'
OUTCOME_FAILURE = 'failure'

_logger = logging.getLogger(__name__)


def default_audit_log_path() -> Path:
    """Resolve the audit log location.

    Honours ``KATO_AUDIT_LOG_PATH`` first (tests). Falls back to
    ``~/.kato/audit.log.jsonl`` in production.
    """
    return kato_home_path('audit.log.jsonl', env_key=AUDIT_LOG_PATH_ENV_KEY)


def append_audit_event(
    *,
    event: str,
    task_id: str = '',
    ticket_summary: str = '',
    repositories: list[str] | None = None,
    branch: str = '',
    pr_url: str = '',
    outcome: str = OUTCOME_SUCCESS,
    error: str = '',
    path: Path | None = None,
) -> None:
    """Append one record to the audit log. Best-effort; never raises.

    Schema is fixed by the user-facing spec — every field is always
    present so the reader doesn't have to handle missing keys.
    """
    record = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event': event,
        'task_id': str(task_id or ''),
        'ticket_summary': str(ticket_summary or ''),
        'repositories': list(repositories or []),
        'branch': str(branch or ''),
        'pr_url': str(pr_url or ''),
        'outcome': str(outcome or OUTCOME_SUCCESS),
        'error': str(error or ''),
    }
    target = path or default_audit_log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + '\n'
        # ``O_APPEND`` makes the write atomic with respect to other
        # writers — the kernel inserts the bytes at the current file
        # end as a single op. Bytes-mode + manual encode so a stray
        # non-utf8 char in ``error`` doesn't blow up the writer.
        fd = os.open(
            str(target),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o644,
        )
        try:
            os.write(fd, line.encode('utf-8', errors='replace'))
        finally:
            os.close(fd)
    except OSError:
        _logger.exception(
            'failed to append audit event to %s — '
            'history will be incomplete but task processing continues',
            target,
        )


def append_task_audit_event(
    task,
    prepared_task,
    *,
    event: str,
    outcome: str = OUTCOME_SUCCESS,
    pr_url: str = '',
    error: str = '',
) -> None:
    """Derive the task/repo/branch fields from ``(task, prepared_task)``
    and append one audit record. Best-effort; never raises.

    Shared by the task-completed (success + ``pr_url``) and task-failed
    (failure + ``error``) audit funnels.
    """
    repositories: list[str] = []
    branch = ''
    if prepared_task is not None:
        repositories = [
            str(getattr(repo, 'id', '') or '')
            for repo in (getattr(prepared_task, 'repositories', None) or [])
        ]
        branch = str(getattr(prepared_task, 'branch_name', '') or '')
    append_audit_event(
        event=event,
        task_id=str(getattr(task, 'id', '') or ''),
        ticket_summary=str(getattr(task, 'summary', '') or ''),
        repositories=[r for r in repositories if r],
        branch=branch,
        pr_url=pr_url,
        outcome=outcome,
        error=error,
    )


def read_audit_records(path: Path | None = None) -> list[dict]:
    """Read every well-formed record from the audit log.

    Returns ``[]`` when the file doesn't exist. Lines that fail to
    parse as JSON are skipped silently — a corrupt write must not
    block ``kato history`` from showing the records around it.
    """
    target = path or default_audit_log_path()
    if not target.is_file():
        return []
    records: list[dict] = []
    try:
        with target.open('r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records
