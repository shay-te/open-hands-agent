from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_TMP_SUFFIX = '.json.tmp'


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    logger: logging.Logger | None = None,
    label: str = '',
) -> bool:
    tmp_path = path.with_suffix(_TMP_SUFFIX)
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        tmp_path.replace(path)
        return True
    except OSError as exc:
        if logger is not None:
            label_text = f' for {label}' if label else ''
            logger.warning(
                'failed to persist json%s at %s: %s', label_text, path, exc,
            )
        return False
