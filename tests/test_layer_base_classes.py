import unittest

import bootstrap  # noqa: F401

from core_lib.data_layers.data_access.data_access import DataAccess
from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data_access.agent_state_data_access import (
    AgentStateDataAccess,
)
from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.testing_service import TestingService


class LayerBaseClassTests(unittest.TestCase):
    def test_data_access_classes_extend_core_lib_data_access(self) -> None:
        for cls in (
            AgentStateDataAccess,
            PullRequestDataAccess,
            TaskDataAccess,
        ):
            self.assertTrue(issubclass(cls, DataAccess))

    def test_service_classes_extend_core_lib_service(self) -> None:
        for cls in (
            AgentService,
            ImplementationService,
            NotificationService,
            RepositoryService,
            TestingService,
        ):
            self.assertTrue(issubclass(cls, Service))
