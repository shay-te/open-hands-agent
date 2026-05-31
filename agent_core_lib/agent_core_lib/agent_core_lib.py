"""Composition root for the agent backend.

Single ``CoreLib`` subclass that exposes the chosen backend
behind one attribute (``self.agent``) typed as the shared
``AgentProvider`` Protocol.
"""

from __future__ import annotations

from typing import Any

from agent_provider_contracts.agent_provider_contracts.agent_provider import (
    AgentProvider,
)
from core_lib.core_lib import CoreLib

from agent_core_lib.agent_core_lib.client.agent_client_factory import (
    AgentClientFactory,
)
from agent_core_lib.agent_core_lib.platform import AgentPlatform


class AgentCoreLib(CoreLib):
    """Compose the configured agent backend."""

    def __init__(
        self,
        platform: AgentPlatform,
        cfg: Any,
        max_retries: int,
        *,
        testing: bool = False,
        docker_mode_on: bool = False,
        read_only_tools_on: bool = False,
        workspace_refusal_guidance: str = '',
    ) -> None:
        super().__init__()
        factory = AgentClientFactory(
            max_retries=max_retries,
            testing=testing,
            docker_mode_on=docker_mode_on,
            read_only_tools_on=read_only_tools_on,
            workspace_refusal_guidance=workspace_refusal_guidance,
        )
        self.agent: AgentProvider = factory.build(platform, cfg)
