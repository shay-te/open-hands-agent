from __future__ import annotations

from typing import Callable, Iterable, Optional


def task_id_matches(task: object, task_id: str) -> bool:
    """Return True when ``task``'s ``id`` equals ``task_id``.

    Normalizes the task's ``id`` attribute with ``str(...).strip()`` (a
    missing/``None`` id becomes ``''``) before comparing, so callers
    don't have to re-spell that expression at every queue-walk site.
    """
    return str(getattr(task, 'id', '') or '').strip() == task_id


def find_task_by_id(
    task_service: object,
    task_id: str,
    *,
    queues: Iterable[str],
    on_error: Optional[Callable[[str], None]] = None,
):
    """Return the first task whose ``id`` matches ``task_id`` (or ``None``).

    Walks each fetcher named in ``queues`` on ``task_service`` in order,
    swallowing per-queue errors and returning the first object whose
    ``str(id).strip()`` equals ``task_id``. Callers keep their own queue
    selection (and any fallback for the not-found case).

    ``on_error`` is an optional callback invoked with the queue name when a
    fetch raises, letting callers log.
    """
    for queue_name in queues:
        fetch = getattr(task_service, queue_name, None)
        if not callable(fetch):
            continue
        try:
            tasks = fetch() or []
        except Exception:
            if on_error is not None:
                on_error(queue_name)
            continue
        for task in tasks:
            if task_id_matches(task, task_id):
                return task
    return None
