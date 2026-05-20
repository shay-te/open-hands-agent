from __future__ import annotations

import logging
import threading
from pathlib import Path

_LIVING_DOC_DIRECTIVE_TEMPLATE = (
    'Project architecture document: {path}\n'
    'At the start of every task, use the Read tool to read this '
    'file. It contains the canonical map of the workspace and any '
    'non-obvious conventions, hidden contracts, gotchas, and layer '
    'boundaries the project has accumulated. Let it shape your '
    'plan.\n'
    '\n'
    'Treat it as a living document you are responsible for keeping '
    'accurate. While working, if you discover something not yet '
    'documented that would help a future agent (a non-obvious '
    'convention, a hidden contract, a gotcha, a layer boundary, a '
    '"why we do it this way"), update the file via the Edit tool — '
    'append a new sub-section under the most appropriate top-level '
    'section, or add a new section if none fits. Do not duplicate '
    'content already documented; do not restate what the code shows. '
    'The document is a navigation aid and a contract registry, not '
    'a mirror of the source. The orchestration layer commits and pushes the file (you '
    'must NEVER run git); just edit.\n'
)

_cache: dict[str, tuple[float, int, str]] = {}
_cache_lock = threading.Lock()


def read_architecture_doc(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    normalized = str(path or '').strip()
    if not normalized:
        return ''
    file_path = Path(normalized).expanduser()
    try:
        stat = file_path.stat()
        if not file_path.is_file():
            raise OSError('not a file')
    except OSError:
        if normalized and logger is not None:
            logger.warning(
                'architecture doc path %s is not a file; skipping context injection',
                file_path,
            )
        return ''
    cache_key = str(file_path)
    mtime = stat.st_mtime
    size = stat.st_size
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and cached[0] == mtime and cached[1] == size:
            return cached[2]
    value = _LIVING_DOC_DIRECTIVE_TEMPLATE.format(path=str(file_path))
    with _cache_lock:
        _cache[cache_key] = (mtime, size, value)
    return value
