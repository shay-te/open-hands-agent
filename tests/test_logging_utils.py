from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import Mock, patch

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
        self.assertIsInstance(
            workflow_handler,
            logging_utils._InlineStatusAwareStreamHandler,
        )

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

    def test_inline_status_aware_handler_clears_active_inline_status_before_emitting(self) -> None:
        handler = logging_utils._InlineStatusAwareStreamHandler()
        handler.setStream(Mock())
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord(
            name='kato.workflow.AgentService',
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='hello',
            args=(),
            exc_info=None,
        )

        with patch('kato.helpers.logging_utils.clear_active_inline_status') as mock_clear_status:
            handler.emit(record)

        mock_clear_status.assert_called_once_with()

    @staticmethod
    def _named_handler(logger: logging.Logger, name: str) -> logging.Handler | None:
        for handler in logger.handlers:
            if handler.get_name() == name:
                return handler
        return None
