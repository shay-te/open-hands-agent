import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from kato.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)


class StartupDependencyValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_connections_validator = Mock()
        self.task_service = SimpleNamespace(
            provider_name='youtrack',
            validate_connection=Mock(),
            max_retries=5,
        )
        self.implementation_service = SimpleNamespace(
            validate_connection=Mock(),
            max_retries=3,
        )
        self.testing_service = SimpleNamespace(
            validate_connection=Mock(),
            max_retries=4,
        )
        self.validator = StartupDependencyValidator(
            self.repository_connections_validator,
            self.task_service,
            self.implementation_service,
            self.testing_service,
            skip_testing=False,
        )
        self.logger = Mock()

    def test_validate_checks_repository_and_all_dependencies(self) -> None:
        self.validator.validate(self.logger)

        self.repository_connections_validator.validate.assert_called_once_with()
        self.task_service.validate_connection.assert_called_once_with()
        self.implementation_service.validate_connection.assert_called_once_with()
        self.testing_service.validate_connection.assert_called_once_with()
        self.logger.info.assert_any_call('validated repositories connection')
        self.logger.info.assert_any_call('validated %s connection', 'youtrack')
        self.logger.info.assert_any_call('validated %s connection', 'openhands')
        self.logger.info.assert_any_call('validated %s connection', 'openhands_testing')

    def test_validate_skips_testing_dependency_when_configured(self) -> None:
        validator = StartupDependencyValidator(
            self.repository_connections_validator,
            self.task_service,
            self.implementation_service,
            self.testing_service,
            skip_testing=True,
        )

        validator.validate(self.logger)

        self.repository_connections_validator.validate.assert_called_once_with()
        self.task_service.validate_connection.assert_called_once_with()
        self.implementation_service.validate_connection.assert_called_once_with()
        self.testing_service.validate_connection.assert_not_called()
        self.logger.info.assert_any_call('validated %s connection', 'youtrack')
        self.logger.info.assert_any_call('validated %s connection', 'openhands')
        self.assertNotIn(
            unittest.mock.call('validated %s connection', 'openhands_testing'),
            self.logger.info.call_args_list,
        )

    def test_validate_aggregates_dependency_failures(self) -> None:
        self.task_service.validate_connection.side_effect = ConnectionError('connection refused')
        self.testing_service.validate_connection.side_effect = RuntimeError('testing down')
        self.logger = Mock()

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            self.validator.validate(self.logger)

        message = str(exc_context.exception)
        self.assertIn('- unable to connect to youtrack (tried 5 times)', message)
        self.assertIn('- unable to validate openhands_testing: testing down', message)
        self.assertIn('Details:', message)
        self.assertIn('[youtrack]', message)
        self.assertIn('[openhands_testing]', message)
        self.logger.exception.assert_called()

    def test_validate_raises_when_repository_validation_fails(self) -> None:
        self.repository_connections_validator.validate.side_effect = RuntimeError('repo down')

        with self.assertRaisesRegex(RuntimeError, 'repo down') as exc_context:
            self.validator.validate(self.logger)

        self.repository_connections_validator.validate.assert_called_once_with()
        self.task_service.validate_connection.assert_not_called()
        self.implementation_service.validate_connection.assert_not_called()
        self.testing_service.validate_connection.assert_not_called()
        self.logger.error.assert_called_once()
        self.assertEqual(self.logger.error.call_args.args[0], 'failed to validate repositories connection: %s')
        self.assertIsInstance(self.logger.error.call_args.args[1], RuntimeError)
        self.assertEqual(str(self.logger.error.call_args.args[1]), 'repo down')
        self.assertIsInstance(exc_context.exception.__cause__, RuntimeError)
        self.assertEqual(str(exc_context.exception.__cause__), 'repo down')
