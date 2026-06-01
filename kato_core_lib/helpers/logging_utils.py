from __future__ import annotations

import logging
import os

from agent_core_lib.agent_core_lib.helpers.logging_utils import (
    set_workflow_root as _set_agent_workflow_root,
)


_LOGGING_CONFIGURED = False
_ROOT_HANDLER_NAME = 'kato_root'
_WORKFLOW_HANDLER_NAME = 'kato_workflow'
_WORKFLOW_LOGGER_PREFIX = 'kato.workflow'
_DEFAULT_LOG_LEVEL = logging.WARNING
_DEFAULT_WORKFLOW_LOG_LEVEL = logging.INFO

# Re-root agent_core_lib's SHARED logger namespace under kato's, so the
# transport (Claude/Codex/OpenHands) loggers — which use agent_core_lib's
# configure_logger — parent under ``kato.workflow`` exactly as kato's own
# services do. Without this, agent_core_lib's generic default (``agent.workflow``)
# would orphan transport logs from the status broadcaster + KATO_WORKFLOW_LOG_LEVEL
# control. Runs at first import of this ubiquitously-imported module, before any
# transport logger is created.
_set_agent_workflow_root(_WORKFLOW_LOGGER_PREFIX)


def _configured_log_level(env_key: str, default_name: str, fallback_level: int) -> int:
    configured_name = str(os.getenv(env_key, default_name) or '').strip().upper()
    return getattr(logging, configured_name, fallback_level)


def _dependency_log_level() -> int:
    return _configured_log_level(
        'KATO_LOG_LEVEL',
        'warning',
        _DEFAULT_LOG_LEVEL,
    )


def _workflow_log_level() -> int:
    return _configured_log_level(
        'KATO_WORKFLOW_LOG_LEVEL',
        'info',
        _DEFAULT_WORKFLOW_LOG_LEVEL,
    )


def _named_handler(logger: logging.Logger, handler_name: str) -> logging.Handler | None:
    for handler in logger.handlers:
        if handler.get_name() == handler_name:
            return handler
    return None


def _ensure_root_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.NOTSET)
    handler = _named_handler(root_logger, _ROOT_HANDLER_NAME)
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name(_ROOT_HANDLER_NAME)
        root_logger.addHandler(handler)
    handler.setLevel(_dependency_log_level())
    handler.setFormatter(logging.Formatter('%(message)s'))


def _ensure_workflow_logging() -> None:
    workflow_logger = logging.getLogger(_WORKFLOW_LOGGER_PREFIX)
    workflow_logger.setLevel(_workflow_log_level())
    workflow_logger.propagate = False
    handler = _named_handler(workflow_logger, _WORKFLOW_HANDLER_NAME)
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name(_WORKFLOW_HANDLER_NAME)
        workflow_logger.addHandler(handler)
    handler.setLevel(_workflow_log_level())
    handler.setFormatter(logging.Formatter('%(message)s'))


def _workflow_logger_name(name: str) -> str:
    suffix = str(name or '').strip().replace(' ', '_').replace('-', '_').replace('.', '_')
    if not suffix:
        return _WORKFLOW_LOGGER_PREFIX
    return f'{_WORKFLOW_LOGGER_PREFIX}.{suffix}'


def configure_logger(name: str) -> logging.Logger:
    global _LOGGING_CONFIGURED

    if not _LOGGING_CONFIGURED:
        _ensure_root_logging()
        _ensure_workflow_logging()
        _LOGGING_CONFIGURED = True

    return logging.getLogger(_workflow_logger_name(name))
