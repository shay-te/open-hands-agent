from __future__ import annotations

from omegaconf import DictConfig

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


class OpenHandsAgentInstance:
    _app_instance: OpenHandsAgentCoreLib | None = None

    @staticmethod
    def init(core_lib_cfg: DictConfig) -> None:
        if OpenHandsAgentInstance._app_instance is None:
            OpenHandsAgentInstance._app_instance = OpenHandsAgentCoreLib(core_lib_cfg)

    @staticmethod
    def get() -> OpenHandsAgentCoreLib:
        if OpenHandsAgentInstance._app_instance is None:
            raise RuntimeError("OpenHandsAgentCoreLib is not initialized")
        return OpenHandsAgentInstance._app_instance
