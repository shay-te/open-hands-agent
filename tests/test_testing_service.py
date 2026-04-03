import types
import unittest
from unittest.mock import Mock

from openhands_agent.data_layers.service.testing_service import TestingService
from openhands_agent.helpers.task_context_utils import PreparedTaskContext
from utils import build_task


class TestingServiceTests(unittest.TestCase):
    def _build_client(self, *, max_retries=1) -> types.SimpleNamespace:
        client = types.SimpleNamespace(
            max_retries=max_retries,
            validate_connection=Mock(),
            validate_model_access=Mock(),
            test_task=Mock(return_value={'success': True}),
        )
        return client

    def _build_prepared_task(self) -> PreparedTaskContext:
        return PreparedTaskContext(
            branch_name='feature/proj-1',
            repositories=[],
            repository_branches={},
        )

    def test_max_retries_uses_client_value(self) -> None:
        client = self._build_client(max_retries=5)

        service = TestingService(client)

        self.assertEqual(service.max_retries, 5)

    def test_max_retries_falls_back_to_one_when_client_has_no_value(self) -> None:
        client = types.SimpleNamespace(
            validate_connection=Mock(),
            validate_model_access=Mock(),
            test_task=Mock(return_value={'success': True}),
        )

        service = TestingService(client)

        self.assertEqual(service.max_retries, 1)

    def test_validate_connection_delegates_to_client(self) -> None:
        client = self._build_client()
        service = TestingService(client)

        service.validate_connection()

        client.validate_connection.assert_called_once_with()

    def test_validate_connection_propagates_client_exception(self) -> None:
        client = self._build_client()
        client.validate_connection.side_effect = RuntimeError('unreachable')
        service = TestingService(client)

        with self.assertRaisesRegex(RuntimeError, 'unreachable'):
            service.validate_connection()

    def test_validate_model_access_delegates_to_client(self) -> None:
        client = self._build_client()
        service = TestingService(client)

        service.validate_model_access()

        client.validate_model_access.assert_called_once_with()

    def test_validate_model_access_propagates_client_exception(self) -> None:
        client = self._build_client()
        client.validate_model_access.side_effect = ValueError('no model access')
        service = TestingService(client)

        with self.assertRaisesRegex(ValueError, 'no model access'):
            service.validate_model_access()

    def test_test_task_delegates_with_prepared_task(self) -> None:
        client = self._build_client()
        service = TestingService(client)
        service.logger = Mock()
        task = build_task()
        prepared_task = self._build_prepared_task()

        result = service.test_task(task, prepared_task=prepared_task)

        client.test_task.assert_called_once_with(task, prepared_task=prepared_task)
        self.assertEqual(result, {'success': True})
        service.logger.info.assert_called_once_with(
            'delegating testing validation for task %s',
            'PROJ-1',
        )

    def test_test_task_passes_none_prepared_task_by_default(self) -> None:
        client = self._build_client()
        service = TestingService(client)
        task = build_task()

        service.test_task(task)

        client.test_task.assert_called_once_with(task, prepared_task=None)

    def test_test_task_returns_client_result_unchanged(self) -> None:
        expected = {'success': False, 'summary': 'tests failed', 'message': 'assertion error'}
        client = self._build_client()
        client.test_task.return_value = expected
        service = TestingService(client)

        result = service.test_task(build_task())

        self.assertIs(result, expected)

    def test_test_task_propagates_client_exception(self) -> None:
        client = self._build_client()
        client.test_task.side_effect = RuntimeError('timeout')
        service = TestingService(client)

        with self.assertRaisesRegex(RuntimeError, 'timeout'):
            service.test_task(build_task())

