import types
import unittest

import bootstrap  # noqa: F401

from openhands_agent.jobs.process_assigned_tasks import ProcessAssignedTasksJob
from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from utils import sync_create_start_core_lib


class ProcessAssignedTasksJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job = ProcessAssignedTasksJob()
        self.openhands_core_lib = sync_create_start_core_lib()

    def test_initialized_accepts_openhands_agent_core_lib(self) -> None:
        self.job.initialized(self.openhands_core_lib)

        self.assertIs(self.job._data_handler, self.openhands_core_lib)
        self.assertIsInstance(self.job._data_handler, OpenHandsAgentCoreLib)

    def test_initialized_rejects_invalid_data_handler(self) -> None:
        with self.assertRaises(AssertionError):
            self.job.initialized(types.SimpleNamespace())
