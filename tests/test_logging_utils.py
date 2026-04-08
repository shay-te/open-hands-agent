import logging
import os
import unittest
from unittest.mock import patch

from kato.helpers import logging_utils


class LoggingUtilsTests(unittest.TestCase):
    def tearDown(self) -> None:
        logging_utils._LOGGING_CONFIGURED = False
        root_logger = logging.getLogger()
        workflow_logger = logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX)
        root_logger.handlers = [
            handler
            for handler in root_logger.handlers
            if handler.get_name() != logging_utils._ROOT_HANDLER_NAME
        ]
        workflow_logger.handlers = [
            handler
            for handler in workflow_logger.handlers
            if handler.get_name() != logging_utils._WORKFLOW_HANDLER_NAME
        ]
        workflow_logger.propagate = True
        workflow_logger.setLevel(logging.NOTSET)

    def test_configure_logger_defaults_to_warning_dependencies_and_info_workflow(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            logger = logging_utils.configure_logger('AgentService')

        self.assertEqual(logger.name, 'kato.workflow.AgentService')
        root_handler = self._named_handler(logging.getLogger(), logging_utils._ROOT_HANDLER_NAME)
        workflow_handler = self._named_handler(
            logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX),
            logging_utils._WORKFLOW_HANDLER_NAME,
        )
        self.assertIsNotNone(root_handler)
        self.assertIsNotNone(workflow_handler)
        self.assertEqual(root_handler.level, logging.WARNING)
        self.assertEqual(workflow_handler.level, logging.INFO)
        self.assertEqual(workflow_handler.formatter._fmt, '%(message)s')

    def test_configure_logger_uses_configured_dependency_and_workflow_levels(self) -> None:
        with patch.dict(
            os.environ,
            {
                'KATO_LOG_LEVEL': 'error',
                'KATO_WORKFLOW_LOG_LEVEL': 'debug',
            },
            clear=False,
        ):
            logging_utils.configure_logger('AgentService')

        root_handler = self._named_handler(logging.getLogger(), logging_utils._ROOT_HANDLER_NAME)
        workflow_handler = self._named_handler(
            logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX),
            logging_utils._WORKFLOW_HANDLER_NAME,
        )
        self.assertEqual(root_handler.level, logging.ERROR)
        self.assertEqual(workflow_handler.level, logging.DEBUG)

    def test_configure_logger_falls_back_to_defaults_for_invalid_levels(self) -> None:
        with patch.dict(
            os.environ,
            {
                'KATO_LOG_LEVEL': 'LOUD',
                'KATO_WORKFLOW_LOG_LEVEL': 'CHATTER',
            },
            clear=False,
        ):
            logging_utils.configure_logger('AgentService')

        root_handler = self._named_handler(logging.getLogger(), logging_utils._ROOT_HANDLER_NAME)
        workflow_handler = self._named_handler(
            logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX),
            logging_utils._WORKFLOW_HANDLER_NAME,
        )
        self.assertEqual(root_handler.level, logging.WARNING)
        self.assertEqual(workflow_handler.level, logging.INFO)

    @staticmethod
    def _named_handler(logger: logging.Logger, name: str) -> logging.Handler | None:
        for handler in logger.handlers:
            if handler.get_name() == name:
                return handler
        return None
