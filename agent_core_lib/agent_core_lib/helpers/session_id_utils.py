"""Canonical helpers for agent session-id normalization.

Owned by ``agent_core_lib`` because every agent backend a host can spawn
(Claude, Codex, OpenHands, OpenRouter, ...) keeps a per-task session
id and needs identical handling: strip whitespace, accept ``None``,
treat blanks as the empty-string sentinel, compare by canonical form.

This module has no upstream imports from any other core-lib — it lives
at the bottom so
``claude_core_lib``, ``codex_core_lib``, ``workspace_core_lib``, the
webserver, and anywhere else can import it without creating a cycle.

The bug class this module exists to prevent: each call site used to
spell out ``str(record.agent_session_id or '').strip()`` by hand,
with subtle variants (some forgot ``.strip()``, some forgot the
``or ''`` guard, some forgot ``str(...)``). The result was a host
passing ``--resume '   '`` to the agent CLI and getting a
"No conversation found" error, or comparing dirty-vs-clean copies
of the same id and deciding they were different. Every read goes
through ``fix_session_id`` now; every comparison through
``same_session_id``; every "do I have a usable id?" check through
``has_session_id``; every read-from-an-object-of-any-shape through
``read_session_id_from``.
"""
from __future__ import annotations

from collections.abc import Mapping


# Canonical key name for the agent's session id across every code
# surface the host exposes (JSON payloads on disk, API request/response
# bodies, hook event payloads, log labels). Use this constant instead
# of an inline field-name literal so a future rename can land here
# and a typo can't silently drift one consumer away from the others.
AGENT_SESSION_ID = 'agent_session_id'


def fix_session_id(value: object) -> str:
    """Normalize a session-id input into a clean ``str`` or ``''``.

    Always:
      * ``None`` → ``''``
      * non-string types → coerced via ``str(...)``
      * whitespace stripped from both ends
      * whitespace-only input → ``''`` (so the caller's
        ``if agent_session_id:`` truthy guards continue to work)

    This is the ONLY function callers should use to normalize a
    session id read from any source (record, payload, attribute,
    JSONL field). Doing it any other way risks the inconsistencies
    catalogued in the module docstring.
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


def has_session_id(value: object) -> bool:
    """Return ``True`` when ``value`` normalizes to a non-empty session id."""
    return bool(fix_session_id(value))


def same_session_id(left: object, right: object) -> bool:
    """Compare two session ids after applying the canonical normalization.

    Use this instead of ``==`` on session ids — the raw equality
    check fails when one side has trailing whitespace from a JSONL
    parse or a payload deserialization while the other is clean.
    """
    return fix_session_id(left) == fix_session_id(right)


def read_session_id_from(obj: object) -> str:
    """Return the canonical session id for ``obj`` (record / session / workspace).

    Duck-typed lookup of the ``agent_session_id`` attribute. Always
    normalized via :func:`fix_session_id` so the return value is
    the clean canonical form (or ``''`` for "no id known"). ``None``
    input is treated as "no id" so call sites don't need their own
    ``if obj is not None`` guard.
    """
    if obj is None:
        return ''
    return (
        fix_session_id(getattr(obj, AGENT_SESSION_ID, ''))
        or fix_session_id(getattr(obj, 'claude_session_id', ''))
    )


def read_session_id_from_mapping(payload: object) -> str:
    """Return the canonical session id from mapping payloads."""
    if not isinstance(payload, Mapping):
        return ''
    return (
        fix_session_id(payload.get(AGENT_SESSION_ID))
        or fix_session_id(payload.get('claude_session_id'))
    )
