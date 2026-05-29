"""Operator settings persisted to ``~/.kato/settings.json``.

This is the source of truth for everything the planning-UI Settings
drawer edits — repository root, task provider + its credentials, git
host credentials. It lives in the user's home ``.kato`` directory
(next to ``approved-repositories.json``, ``workspaces/``,
``sessions/``) so it's per-operator, survives ``git clean``, and is
shared across multiple kato checkouts.

Precedence (resolved at boot by ``load_kato_settings_into_environ``):

    real shell env  >  ~/.kato/settings.json  >  <repo>/.env

* A variable the operator exported in their shell always wins — an
  emergency override stays in their hands.
* ``settings.json`` is authoritative for UI-managed keys.
* ``<repo>/.env`` remains a legacy fallback (loaded separately by
  ``dotenv_utils``) so installs that predate this file keep working
  until the operator saves once through the UI — at which point
  ``settings.json`` is written and from then on wins. No explicit
  migration step, no data loss.

The file is a flat ``{"KEY": "value"}`` JSON object — same env-var
keys kato already reads via hydra ``${oc.env:...}``. Flat (not
nested per-provider) so the boot loader is a trivial dict→environ
copy and the file diffs cleanly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path


_SETTINGS_PATH_ENV_KEY = 'KATO_SETTINGS_FILE'


def kato_settings_path() -> Path:
    """``~/.kato/settings.json`` (or ``$KATO_SETTINGS_FILE`` override).

    The env override exists for tests + for operators who keep their
    ``.kato`` dir somewhere non-standard; it mirrors how
    ``KATO_APPROVED_REPOSITORIES_PATH`` overrides the approvals
    sidecar location.
    """
    return kato_home_path('settings.json', env_key=_SETTINGS_PATH_ENV_KEY)


def read_kato_settings() -> dict[str, str]:
    """Return the settings dict, or ``{}`` when the file is absent.

    Tolerant of a corrupt / partial file (returns ``{}`` rather than
    raising) so a hand-edit typo can't brick the boot path — the
    operator just falls back to ``.env`` until they fix or re-save.
    """
    path = kato_settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Coerce every value to str — env vars are strings, and a
    # hand-edit that put a number / bool shouldn't blow up the
    # ``os.environ[...] = value`` assignment at boot.
    return {str(k): str(v) for k, v in data.items() if k}


def write_kato_settings(updates: dict[str, str]) -> dict[str, str]:
    """Merge ``updates`` into the settings file. Returns the new full dict.

    Atomic: read → merge → write a sibling temp file → ``os.replace``
    so a concurrent reader (the boot loader, another webserver
    thread) sees either the old file in full or the new one in full,
    never a torn JSON. Creates ``~/.kato`` if missing. Keys whose
    value is the empty string are kept (kato treats empty as "unset"
    via the ``${oc.env:KEY,"default"}`` pattern) so the operator can
    explicitly clear a field.
    """
    if not updates:
        return read_kato_settings()
    current = read_kato_settings()
    current.update({str(k): str(v) for k, v in updates.items() if k})
    path = kato_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Route through the shared atomic writer (sibling tmp + os.replace) but
    # keep settings-specific behavior: surface OSError to the operator (the
    # UI relies on it) and preserve the trailing newline for clean diffs.
    atomic_write_json(
        path,
        current,
        trailing_newline=True,
        raise_on_error=True,
    )
    return current


def load_kato_settings_into_environ() -> int:
    """Inject ``~/.kato/settings.json`` into ``os.environ``.

    Real shell env vars win — a key already present in
    ``os.environ`` is NOT overwritten (same contract as
    ``load_dotenv_into_environ``). Returns the count of keys
    actually inserted, for diagnostics.

    Call order at boot matters: run this BEFORE
    ``load_dotenv_into_environ`` so settings.json wins over ``.env``
    (``.env``'s loader also skips keys already set, so once this has
    populated a key, ``.env`` won't clobber it).
    """
    added = 0
    for key, value in read_kato_settings().items():
        if key in os.environ:
            continue
        os.environ[key] = value
        added += 1
    return added
