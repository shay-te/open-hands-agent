import unittest

from kato.validation.base import ValidationBase
from kato.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato.validation.branch_push import (
    TaskBranchPushValidator,
)
from kato.validation.model_access import (
    TaskModelAccessValidator,
)
from kato.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)


class ValidationBaseTests(unittest.TestCase):
    def test_all_validators_inherit_from_validation_base(self) -> None:
        self.assertTrue(issubclass(TaskBranchPublishabilityValidator, ValidationBase))
        self.assertTrue(issubclass(TaskBranchPushValidator, ValidationBase))
        self.assertTrue(issubclass(TaskModelAccessValidator, ValidationBase))
        self.assertTrue(issubclass(RepositoryConnectionsValidator, ValidationBase))
        self.assertTrue(issubclass(StartupDependencyValidator, ValidationBase))
