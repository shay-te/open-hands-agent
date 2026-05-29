from __future__ import annotations

import logging
from typing import Callable


def add_task_comment(
    task_service: object,
    logger: logging.Logger,
    log_step_fn: Callable[[str, str], None],
    task_id: str,
    comment: str,
    *,
    after_step: str = '',
    failure_log_message: str,
) -> bool:
    """Post ``comment`` on ``task_id``, logging an optional follow-up step.

    Returns True on success, False (after logging ``failure_log_message``
    against ``task_id``) if the platform call raises. ``log_step_fn`` is the
    caller's mission-step logger, invoked with ``(task_id, after_step)`` when
    ``after_step`` is provided.
    """
    try:
        task_service.add_comment(task_id, comment)
        if after_step:
            log_step_fn(task_id, after_step)
        return True
    except Exception:
        logger.exception(failure_log_message, task_id)
        return False
