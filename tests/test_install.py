import unittest
from unittest.mock import patch

from openhands_agent.install import main
from utils import build_test_cfg


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_main_runs_core_lib_install(self) -> None:
        with patch('openhands_agent.install.OpenHandsAgentCoreLib.install') as mock_install:
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_install.assert_called_once_with(self.cfg)
