from __future__ import annotations

import logging


def configure_logger(name: str) -> logging.Logger:
    suffix = str(name or '').strip()
    if suffix:
        return logging.getLogger(f'kato.workflow.{suffix}')
    return logging.getLogger('kato.workflow')
