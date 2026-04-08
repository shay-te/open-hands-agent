import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from kato.validation.repository_connections import (
    RepositoryConnectionsValidator,
)


class RepositoryConnectionsValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_service = SimpleNamespace(
            _validate_inventory=Mock(),
            _validate_git_executable=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            repositories=['repo-1', 'repo-2'],
        )
        self.validator = RepositoryConnectionsValidator(self.repository_service)

    def test_validate_checks_inventory_executable_and_each_repository(self) -> None:
        self.validator.validate()

        self.repository_service._validate_inventory.assert_called_once_with()
        self.repository_service._validate_git_executable.assert_called_once_with()
        self.repository_service._prepare_repository_access.assert_has_calls(
            [unittest.mock.call('repo-1'), unittest.mock.call('repo-2')]
        )
        self.repository_service._validate_repository_git_access.assert_has_calls(
            [unittest.mock.call('repo-1'), unittest.mock.call('repo-2')]
        )

    def test_validate_stops_on_inventory_failure(self) -> None:
        self.repository_service._validate_inventory.side_effect = RuntimeError('inventory down')

        with self.assertRaisesRegex(RuntimeError, 'inventory down'):
            self.validator.validate()

        self.repository_service._validate_inventory.assert_called_once_with()
        self.repository_service._validate_git_executable.assert_not_called()
        self.repository_service._prepare_repository_access.assert_not_called()
        self.repository_service._validate_repository_git_access.assert_not_called()

