"""DTO for the dict-shaped result every backend returns today.

Backends return free-form dicts with at least ``success: bool``
and usually a ``message: str`` / ``summary: str`` / ``agent_session_id:
str``. Pinning the full shape would be brittle — different
backends populate different keys. We type the methods as
``AgentResult = dict[str, Any]`` so the contract describes the
shape loosely and individual impls remain free to add their own
diagnostic keys.

A future tightening could replace this with a real frozen
dataclass once the kato call sites have been audited for which
keys they actually read.
"""

from __future__ import annotations

from typing import Any


# Loose alias for the dict every backend returns from
# ``implement_task`` / ``test_task`` / ``fix_review_comment(s)``.
# Always carries ``success: bool``; everything else is per-backend.
AgentResult = dict[str, Any]
