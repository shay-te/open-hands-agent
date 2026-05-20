"""Enum of agent backends ``agent_core_lib`` knows how to construct.

Mirrors the shape of ``task_core_lib.platform.Platform`` and
``repository_core_lib.platform.Platform`` so a reader who has
already learned one factory pattern recognises this one. Aliases
on the lookup side (``claude-code``, ``open-hands``, …) are
kept inside the factory's ``from_config_string`` helper rather
than duplicated as enum members.
"""

from __future__ import annotations

from enum import Enum


class AgentPlatform(Enum):
    """Agent backends supported by ``agent_core_lib``."""

    CLAUDE = 'claude'
    CODEX = 'codex'
    OPENHANDS = 'openhands'
