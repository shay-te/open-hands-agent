"""Shared @-mention filter for ticket-platform comments.

Every ticket platform (YouTrack, Jira, GitHub Issues, GitLab Issues,
Bitbucket Issues) pulls comments off the issue and embeds them in the
task description that ultimately reaches the agent. Without filtering,
a comment like ``@jane.doe please look at this`` becomes work the
agent attempts — even though it was clearly directed at a human, not
the kato bot user. This module is the one helper every platform's
client calls to decide whether a given comment is "for someone else".

Single rule:

  * Comment contains at least one ``@login`` mention AND none of
    those mentions match the configured bot login  →  skip.
  * Otherwise (no mentions OR a mention that DOES match the bot)
    →  include.

Bot login defaults to ``""`` and the YouTrack alias ``"me"`` is also
treated as unset — both turn the filter into a no-op so platforms
that haven't configured a real bot login preserve their pre-filter
behavior.
"""
from __future__ import annotations

import re


# ``@login`` at a word boundary. Login characters cover the union of
# what YouTrack / Jira / GitHub / GitLab / Bitbucket accept:
# letters, digits, underscore, dot, hyphen.
#
# * The lookbehind on ``[\w.]`` keeps email addresses like
#   ``foo@example.com`` from matching ``@example``.
# * The login must start AND end with a word character so that
#   sentence punctuation like ``@carol.`` doesn't get consumed as
#   part of the login. Internal ``.`` / ``-`` (e.g. ``@user.name``,
#   ``@bob-jr``) is still allowed.
_MENTION_PATTERN = re.compile(r'(?<![\w.])@(\w(?:[\w.\-]*\w)?)')


def extract_mention_logins(body: object) -> list[str]:
    """Return lowercase logins mentioned in ``body`` via ``@login``.

    Returns ``[]`` when ``body`` is falsy, not a string, or contains
    no recognizable mentions. The result preserves source order but
    lowercases each login so the host can do a case-insensitive
    comparison against its configured bot login.
    """
    if not body:
        return []
    text = body if isinstance(body, str) else str(body)
    return [m.group(1).lower() for m in _MENTION_PATTERN.finditer(text)]


def _normalize_bot_login(bot_login: object) -> str:
    """Lowercase and strip the bot login; treat ``"me"`` as unset.

    YouTrack accepts ``"me"`` as an alias for "the calling user" in
    queries, but it never appears as a literal mention in comment
    bodies — so a ``"me"`` value can never match and must be treated
    as "filter disabled".
    """
    if bot_login is None:
        return ''
    text = str(bot_login).strip().lower()
    return '' if text == 'me' else text


def is_comment_addressed_elsewhere_any(body: object, bot_logins: object) -> bool:
    """Same rule as :func:`is_comment_addressed_elsewhere`, but for a bot
    known under SEVERAL logins at once.

    A bot can have more than one login simultaneously — e.g. its
    ticket-platform ``assignee`` and its (often different) code-host
    username. A comment that ``@mentions`` the bot under ANY of those logins
    is "for the bot" and is kept; only a comment that mentions other people
    and none of the bot's logins is skipped. Empty / ``"me"`` logins are
    ignored, so a bot with no usable login disables the filter (returns
    False), exactly like the single-login form. A bare string is accepted as
    a single login.
    """
    if bot_logins is None or isinstance(bot_logins, str):
        candidates: tuple = (bot_logins,)
    else:
        candidates = tuple(bot_logins)
    logins = {_normalize_bot_login(candidate) for candidate in candidates}
    logins.discard('')
    if not logins:
        return False
    mentions = set(extract_mention_logins(body))
    if not mentions:
        return False
    return logins.isdisjoint(mentions)


def is_comment_addressed_elsewhere(body: object, bot_login: object) -> bool:
    """Is this comment @-mentioning humans OTHER than the bot user?

    See the module docstring for the rule. Returns False whenever
    the filter is disabled (empty / ``"me"`` bot login) so callers
    can wire this in unconditionally. Thin single-login wrapper over
    :func:`is_comment_addressed_elsewhere_any`.
    """
    return is_comment_addressed_elsewhere_any(body, (bot_login,))
