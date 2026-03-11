import unittest

import bootstrap  # noqa: F401

from hydra.core.config_search_path import ConfigSearchPath
from hydra_plugins.openhands_agent.openhands_agent_searchpath import (
    OpenHandsAgentSearchPathPlugin,
)


class _SearchPath(ConfigSearchPath):
    def __init__(self) -> None:
        self.calls = []

    def append(self, provider: str, path: str) -> None:
        self.calls.append((provider, path))


class HydraPluginTests(unittest.TestCase):
    def test_registers_openhands_agent_config_path(self) -> None:
        plugin = OpenHandsAgentSearchPathPlugin()
        search_path = _SearchPath()

        plugin.manipulate_search_path(search_path)

        self.assertEqual(
            search_path.calls,
            [('openhands-agent', 'pkg://openhands_agent.config')],
        )
