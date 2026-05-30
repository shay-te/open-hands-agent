"""Repository denylist driven by ``KATO_REPOSITORY_DENYLIST``.

Operators sometimes need kato **never** to touch certain repos —
secrets-vault repos, regulated-data repos, customer-bespoke private
forks. This module is the canonical "is this repo on the denylist?"
oracle. Pure data — no I/O beyond env-var read, no kato service
dependencies — so every layer (inventory loader, security-posture
banner, configurator) can consult it cheaply.

The list is comma-separated repo IDs (case-insensitive, whitespace
trimmed). Empty / unset means no denylist (current behaviour).
Match is against ``Repository.id`` which kato normalises to
lowercase already.

Why an env var, not a config file:
- Per-operator-per-machine policy. Different operators of the same
  inventory may have different denylists; baking it in a shared
  config file forces a sync.
- Often policy-sensitive (the names of the repos you refuse to
  touch). Env vars stay out of git.
- Boot validators see env before the inventory loads, so the
  refusal can fire even on a broken inventory config.
"""

from __future__ import annotations

import os


REPOSITORY_DENYLIST_ENV_KEY = 'KATO_REPOSITORY_DENYLIST'


def denied_ids(env: dict | None = None) -> frozenset[str]:
    """Parse the env var into a normalised set of repo IDs.

    Returns an empty frozenset when the var is unset, blank, or
    whitespace-only. Whitespace around each comma-separated entry
    is trimmed; case is folded to lowercase. Duplicates collapse
    silently.
    """
    source = env if env is not None else os.environ
    raw = str(source.get(REPOSITORY_DENYLIST_ENV_KEY, '') or '').strip()
    if not raw:
        return frozenset()
    parsed: set[str] = set()
    for entry in raw.split(','):
        normalized = entry.strip().lower()
        if normalized:
            parsed.add(normalized)
    return frozenset(parsed)
