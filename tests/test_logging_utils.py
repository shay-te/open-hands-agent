from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import patch

from kato_core_lib.helpers import logging_utils


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
            logging.StreamHandler,
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

    @staticmethod
    def _named_handler(logger: logging.Logger, name: str) -> logging.Handler | None:
        for handler in logger.handlers:
            if handler.get_name() == name:
                return handler
        return None


class AgentWorkflowRootResetTests(unittest.TestCase):
    """agent_core_lib's shared logger root defaults to the generic
    ``agent.workflow``; importing kato's logging_utils re-roots it under
    ``kato.workflow`` so transport (Claude/Codex/OpenHands) loggers — which use
    agent_core_lib's configure_logger — parent under kato's namespace. This
    keeps them under kato's status-broadcast handler + KATO_WORKFLOW_LOG_LEVEL
    control; guards the regression where they'd orphan to ``agent.workflow``
    and the planning UI status bar would go silent for transport events.
    """

    def test_importing_kato_logging_utils_reroots_agent_core_lib(self) -> None:
        from agent_core_lib.agent_core_lib.helpers.logging_utils import (
            configure_logger as agent_configure_logger,
            get_workflow_root,
        )
        # ``from kato_core_lib.helpers import logging_utils`` at module top runs
        # set_workflow_root('kato.workflow') at import time.
        self.assertEqual(get_workflow_root(), 'kato.workflow')
        # A transport-style logger built via agent_core_lib now lands under
        # kato's namespace (a child of the status-broadcast target).
        self.assertEqual(
            agent_configure_logger('ClaudeCliClient').name,
            'kato.workflow.ClaudeCliClient',
        )
