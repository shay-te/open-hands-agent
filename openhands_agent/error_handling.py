from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


T = TypeVar('T')


def run_best_effort(
    operation: Callable[[], T],
    *,
    logger,
    failure_log_message: str,
    failure_args: tuple[object, ...] = (),
    default: T | None = None,
) -> T | None:
    try:
        return operation()
    except Exception:
        logger.exception(failure_log_message, *failure_args)
        return default


def log_and_notify_failure(
    *,
    logger,
    notification_service,
    operation_name: str,
    error: Exception,
    failure_log_message: str,
    notification_failure_log_message: str,
    context: dict[str, object] | None = None,
) -> None:
    logger.exception(failure_log_message)

    def notify_failure():
        if context is None:
            return notification_service.notify_failure(operation_name, error)
        return notification_service.notify_failure(operation_name, error, context)

    run_best_effort(
        notify_failure,
        logger=logger,
        failure_log_message=notification_failure_log_message,
        default=False,
    )
