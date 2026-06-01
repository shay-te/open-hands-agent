from __future__ import annotations

import logging

# Root namespace for the loggers this shared base hands out. Generic by default
# so agent_core_lib carries no product brand when used standalone. A host can
# override it via ``set_workflow_root`` — e.g. kato sets it to ``kato.workflow``
# at import so its transport loggers parent under kato's namespace (preserving
# kato's operator log levels, its status-broadcaster target, and its log
# namespace). The override must run before any logger is created; transports
# create their loggers instance-level (at client construction), well after the
# host's import, so the ordering holds.
_DEFAULT_ROOT = 'agent.workflow'
_root = _DEFAULT_ROOT


def set_workflow_root(root: str) -> None:
    """Override the root namespace used by :func:`configure_logger`.

    Blank/None resets to the generic default. Idempotent.
    """
    global _root
    _root = str(root or '').strip() or _DEFAULT_ROOT


def get_workflow_root() -> str:
    """Return the current root namespace (``'agent.workflow'`` unless overridden)."""
    return _root


def configure_logger(name: str) -> logging.Logger:
    suffix = str(name or '').strip()
    if suffix:
        return logging.getLogger(f'{_root}.{suffix}')
    return logging.getLogger(_root)
