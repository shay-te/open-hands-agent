from __future__ import annotations

_GREEN = '\033[32m'
_CYAN = '\033[36m'
_RESET = '\033[0m'


def _format_message(message: str, args: tuple) -> str:
    if not args:
        return message
    try:
        return message % args
    except Exception:
        return ' '.join([message, *[str(arg) for arg in args]])


def log_mission_step(logger, task_id: str, message: str, *args) -> None:
    logger.info('Mission %s: %s', task_id, _format_message(message, args))


def log_mission_start(logger, task_id: str, message: str, *args) -> None:
    logger.info('%s>> Mission %s: %s%s', _GREEN, task_id, _format_message(message, args), _RESET)


def log_mission_end(logger, task_id: str, message: str, *args) -> None:
    logger.info('%s<< Mission %s: %s%s', _GREEN, task_id, _format_message(message, args), _RESET)


def log_review_comment_start(logger, task_id: str, message: str, *args) -> None:
    logger.info('%s>> Mission %s: %s%s', _CYAN, task_id, _format_message(message, args), _RESET)


def log_review_comment_end(logger, task_id: str, message: str, *args) -> None:
    logger.info('%s<< Mission %s: %s%s', _CYAN, task_id, _format_message(message, args), _RESET)


class MissionStepLoggerMixin(object):
    """Provides ``_log_task_step`` for services that own a ``self.logger``.

    Collapses the byte-identical wrappers that forwarded to
    ``log_mission_step``. Remains an instance method so per-instance
    patching in tests keeps working.
    """

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        log_mission_step(self.logger, task_id, message, *args)
