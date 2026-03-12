import types
import unittest
from unittest.mock import Mock

import bootstrap  # noqa: F401

from openhands_agent.data_layers.service.testing_service import TestingService
from utils import build_task


class TestingServiceTests(unittest.TestCase):
    def test_passes_openhands_client_calls(self) -> None:
        client = types.SimpleNamespace(
            validate_connection=Mock(),
            test_task=Mock(return_value={'success': True}),
        )
        service = TestingService(client)
        service.logger = Mock()
        task = build_task()

        service.validate_connection()
        service.test_task(task)

        client.validate_connection.assert_called_once_with()
        client.test_task.assert_called_once_with(task)
        service.logger.info.assert_called_once_with(
            'delegating testing validation for task %s',
            'PROJ-1',
        )
