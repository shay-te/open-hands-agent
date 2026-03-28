import logging
import os
import unittest
from unittest.mock import patch

from openhands_agent import logging_utils


class LoggingUtilsTests(unittest.TestCase):
    def tearDown(self) -> None:
        logging_utils._LOGGING_CONFIGURED = False

    def test_configure_logger_defaults_to_warning_level(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            'openhands_agent.logging_utils.logging.basicConfig'
        ) as mock_basic_config:
            logging_utils.configure_logger('openhands-agent')

        self.assertEqual(mock_basic_config.call_args.kwargs['level'], logging.WARNING)

    def test_configure_logger_uses_configured_agent_log_level(self) -> None:
        with patch.dict(
            os.environ,
            {'OPENHANDS_AGENT_LOG_LEVEL': 'ERROR'},
            clear=False,
        ), patch('openhands_agent.logging_utils.logging.basicConfig') as mock_basic_config:
            logging_utils.configure_logger('openhands-agent')

        self.assertEqual(mock_basic_config.call_args.kwargs['level'], logging.ERROR)

    def test_configure_logger_falls_back_to_warning_for_invalid_level(self) -> None:
        with patch.dict(
            os.environ,
            {'OPENHANDS_AGENT_LOG_LEVEL': 'LOUD'},
            clear=False,
        ), patch('openhands_agent.logging_utils.logging.basicConfig') as mock_basic_config:
            logging_utils.configure_logger('openhands-agent')

        self.assertEqual(mock_basic_config.call_args.kwargs['level'], logging.WARNING)
