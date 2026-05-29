"""Discover the agent CLI's supported ``--effort`` levels at runtime.

Hardcoding the level list (``low``/``medium``/``high``/…) drifts the moment
the CLI ships a new tier. Instead we parse them out of ``<binary> --help``
so the set always tracks the installed version. The static list is only a
fallback for when the binary can't be run (offline tests, missing CLI).
"""

from __future__ import annotations

import re
import subprocess
import threading

# Fallback only — used when ``<binary> --help`` can't be run or parsed.
# Matches the CLI shipped at the time of writing; discovery supersedes it.
FALLBACK_EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')

_cache: dict[str, tuple[str, ...]] = {}
_cache_lock = threading.Lock()


def discover_effort_levels(binary: str = 'claude', timeout: float = 10.0) -> list[str]:
    """Return the ``--effort`` levels the given CLI advertises.

    Result is cached per binary (the help output doesn't change between
    runs of the same install). Always returns a non-empty list — the
    fallback when discovery fails — so callers never have to special-case
    an empty result.
    """
    key = str(binary or 'claude').strip() or 'claude'
    with _cache_lock:
        if key in _cache:
            return list(_cache[key])
    discovered = _parse_effort_levels_from_help(key, timeout)
    levels = tuple(discovered) if discovered else FALLBACK_EFFORT_LEVELS
    with _cache_lock:
        _cache[key] = levels
    return list(levels)


def _parse_effort_levels_from_help(binary: str, timeout: float) -> list[str] | None:
    """Parse ``--effort <level> … (low, medium, high, …)`` out of --help."""
    try:
        proc = subprocess.run(
            [binary, '--help'],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception:
        return None
    text = f'{proc.stdout or ""}\n{proc.stderr or ""}'
    # The flag line carries the allowed values in parentheses, e.g.
    #   --effort <level>   Effort level for the current session (low, medium, high, xhigh, max)
    match = re.search(r'--effort\b[^\n(]*\(([^)]*)\)', text)
    if not match:
        return None
    levels = [part.strip().lower() for part in match.group(1).split(',')]
    levels = [lvl for lvl in levels if re.fullmatch(r'[a-z][a-z0-9_-]*', lvl)]
    return levels or None


def reset_effort_levels_cache() -> None:
    """Clear the discovery cache (tests / a CLI upgrade mid-process)."""
    with _cache_lock:
        _cache.clear()
