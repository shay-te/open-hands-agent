from __future__ import annotations

import os
from pathlib import Path


def kato_home_path(filename: str, *, env_key: str) -> Path:
    """Resolve a file under ``~/.kato/`` with an env-var override.

    Honours ``$<env_key>`` first (expanduser-ed) — used by tests and by
    operators who keep their ``.kato`` dir somewhere non-standard — and
    otherwise falls back to ``~/.kato/<filename>``.
    """
    override = os.environ.get(env_key, '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / '.kato' / filename
