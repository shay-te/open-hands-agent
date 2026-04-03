from __future__ import annotations


def log_mission_step(logger, task_id: str, message: str, *args) -> None:
    formatted_message = message
    if args:
        try:
            formatted_message = message % args
        except Exception:
            formatted_message = ' '.join([message, *[str(arg) for arg in args]])
    logger.info('Mission %s: %s', task_id, formatted_message)
