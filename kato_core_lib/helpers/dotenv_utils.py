"""Load ``KEY=VALUE`` lines from a ``.env`` file into the process env.

Two callsites use this — the ``tools/kato/kato.py`` dispatcher
(loaded once before any subcommand subprocess runs) and
``scripts/approve_repository.py`` (belt-and-suspenders for
developers who bypass the dispatcher and run the script directly).
Before this extraction both had a hand-rolled near-identical loop
with the same quirks (``export`` prefix, single/double-quote
stripping, missing-file silence). One implementation here keeps
the two callsites lockstep.

The contract:

* Real environment variables ALWAYS win — values already present in
  ``os.environ`` are not overwritten. So an operator who exports a
  variable in their shell stays in control over what's in ``.env``.
* Best-effort parser: malformed lines (missing ``=``, blank keys)
  are skipped silently rather than raising. We'd rather load 23 of
  24 valid lines than refuse a whole bootstrap because line 47 has
  a stray quote.
* Returns the count of *new* keys actually inserted, mostly for
  diagnostics; nothing currently branches on it.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_into_environ(env_path: Path) -> int:
    """Read ``KEY=VALUE`` lines from ``env_path`` into ``os.environ``.

    Real env vars win — values already present in the parent
    environment are NOT overwritten. Returns the number of new keys
    actually added; ``0`` for a missing or unreadable file.
    """
    if not env_path.is_file():
        return 0
    try:
        text = env_path.read_text(encoding='utf-8')
    except OSError:
        return 0
    added = 0
    for key, value in parse_dotenv_text(text).items():
        if key in os.environ:
            continue
        os.environ[key] = value
        added += 1
    return added


def parse_dotenv_text(text: str) -> dict[str, str]:
    """Tokenize ``.env`` ``text`` into a ``{KEY: value}`` dict.

    The single source of truth for kato's ``.env`` parsing. Drops
    comments, blanks, and malformed lines (no ``=`` / blank key);
    strips a leading ``export `` so files written for ``source .env``
    Bash usage parse here; strips one matched pair of surrounding
    quotes from each value. Later duplicate keys win (last assignment).
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        key, value = parse_dotenv_line(raw_line)
        if key is None:
            continue
        values[key] = value
    return values


def parse_dotenv_line(raw_line: str) -> tuple[str | None, str]:
    """Parse one ``.env`` line into ``(key, value)`` or ``(None, '')``.

    Drops comments, blank lines, and malformed lines (no ``=``,
    blank key). Strips a leading ``export `` so files written for
    ``source .env`` Bash usage still parse here. Strips a single
    matched pair of surrounding quotes from the value — embedded
    quotes are preserved.
    """
    line = raw_line.strip()
    if not line or line.startswith('#'):
        return None, ''
    if line.startswith('export '):
        line = line[len('export '):].lstrip()
    if '=' not in line:
        return None, ''
    key, _, value = line.partition('=')
    key = key.strip()
    if not key:
        return None, ''
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return key, value
