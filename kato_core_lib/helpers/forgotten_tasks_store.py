"""Persistent set of task ids the operator explicitly forgot.

Forgetting a task (DELETE ``/api/sessions/<task_id>/workspace``) wipes its local
workspace clones + session record — but the task can still be IN REVIEW on the
platform (YouTrack/Jira/Bitbucket) with unresolved PR comments. The review-comment
scan polls the PLATFORM for in-review tasks (``TaskService.get_review_tasks``),
so without a persistent marker a forgotten task gets re-discovered and
resurrected on the next scan. This is especially visible after a restart, which
clears the in-memory ``AgentStateRegistry.processed_review_comment_map`` so every
comment looks new again — a task forgotten days ago "pops up from nothing".

This file records the forgotten ids on disk so the scan skips them until the
operator RE-ADOPTS the task (adopt clears the mark). It does NOT touch the
platform — kato never mutates the ticket; it just stops re-engaging locally.

Stored at ``~/.kato/forgotten_tasks.json`` (override via
``KATO_FORGOTTEN_TASKS_PATH``).
"""
from __future__ import annotations

import json
from pathlib import Path

from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_FORGOTTEN_TASKS_PATH'
_FILENAME = 'forgotten_tasks.json'


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def forgotten_task_ids() -> set[str]:
    """Return the forgotten task ids — empty set on a missing/corrupt file."""
    path = _path()
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item).strip() for item in data if str(item).strip()}


def is_forgotten(task_id: str) -> bool:
    normalized = str(task_id or '').strip()
    return bool(normalized) and normalized in forgotten_task_ids()


def forget(task_id: str) -> None:
    """Mark a task forgotten so the scan skips it until it is re-adopted."""
    normalized = str(task_id or '').strip()
    if not normalized:
        return
    ids = forgotten_task_ids()
    if normalized in ids:
        return
    ids.add(normalized)
    _write(ids)


def unforget(task_id: str) -> None:
    """Clear a task's forgotten mark — the operator re-adopted it."""
    normalized = str(task_id or '').strip()
    if not normalized:
        return
    ids = forgotten_task_ids()
    if normalized not in ids:
        return
    ids.discard(normalized)
    _write(ids)


def _write(ids: set[str]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(path, sorted(ids))
