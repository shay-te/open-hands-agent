"""Single source of truth for normalizing a Claude session-id value.

The codebase used to repeat ``str(record.claude_session_id or '').strip()``
(and variants reading from dicts, payloads, attributes) at every site
that touches a session id. Each call site had its own subtle bugs:

  * some forgot to ``strip()`` — kato then passed ``--resume '   '`` to
    the Claude CLI and got a "No conversation found" error;
  * some forgot the ``or ''`` guard — ``None`` propagated and crashed
    later string ops;
  * some forgot ``str(...)`` — a non-string value (test fixture,
    legacy record format) caused ``AttributeError`` deep in the
    spawn path.

``fix_session_id`` is the one helper everything goes through. Pass it
anything — a string, ``None``, an attribute lookup, a dict value —
and you get back a clean stripped str (or ``''`` for "no id known").

Empty string is the canonical "no session" sentinel everywhere in
kato; we keep that convention rather than returning ``None`` so
existing truthiness checks (``if session_id: ...``) keep working.
"""
from __future__ import annotations


def fix_session_id(value: object) -> str:
    """Normalize any session-id input into a clean ``str`` (or ``''``).

    Always:
      * ``None`` → ``''``
      * non-string types → coerced via ``str(...)``
      * whitespace stripped from both ends
      * whitespace-only input → ``''`` (so the caller's
        ``if session_id:`` truthy guards continue to work)

    This is the ONLY function callers should use to read a session
    id off a ``PlanningSessionRecord``, a workspace record, a
    request payload, an event raw dict, or any other source. Doing
    it any other way risks the bugs catalogued in the module
    docstring.
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return value.strip()
