"""Shared mtime+size cache for rendering directive text from a file.

The architecture-doc and lessons-doc helpers both follow the same
flow: normalise the configured path, stat the file (rejecting
non-files), key a per-path cache on ``(mtime, size)``, and only the
render step differs. This module owns that flow so the two callers
share one cache implementation; each passes its own ``renderer``
callable for the bit that is genuinely different.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

_cache: dict[str, tuple[float, int, str]] = {}
_cache_lock = threading.Lock()


def cached_file_render(
    path: str,
    renderer: Callable[[Path], str],
    *,
    logger: logging.Logger | None = None,
    stat_error_message: str | None = None,
) -> str:
    """Return ``renderer(file_path)`` cached on the file's mtime+size.

    * Empty / blank ``path`` → ``''`` (renderer not invoked).
    * Path that doesn't stat or isn't a regular file → ``''``. When
      ``stat_error_message`` is provided and a ``logger`` is given,
      a warning is emitted (``%s`` receives the resolved path).
    * On a cache hit (same mtime+size) the stored value is returned
      without re-rendering.
    * The render result is cached only when non-empty, so callers
      whose renderer returns ``''`` for an empty/unreadable body keep
      re-checking the file on the next call (identical to the prior
      per-helper behaviour).
    """
    normalized = str(path or '').strip()
    if not normalized:
        return ''
    file_path = Path(normalized).expanduser()
    try:
        stat = file_path.stat()
        if not file_path.is_file():
            raise OSError('not a file')
    except OSError:
        if normalized and stat_error_message is not None and logger is not None:
            logger.warning(stat_error_message, file_path)
        return ''
    cache_key = str(file_path)
    mtime = stat.st_mtime
    size = stat.st_size
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and cached[0] == mtime and cached[1] == size:
            return cached[2]
    value = renderer(file_path)
    if not value:
        return value
    with _cache_lock:
        _cache[cache_key] = (mtime, size, value)
    return value
