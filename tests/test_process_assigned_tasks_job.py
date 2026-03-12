import io
import json
import types
import unittest
from unittest.mock import Mock
from contextlib import redirect_stdout

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

    def test_run_prints_results_to_stdout(self) -> None:
        results = [{'id': '17', 'url': 'https://bitbucket/pr/17'}]
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = ['task-1']
        self.openhands_core_lib.service.process_assigned_task.return_value = results[0]
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.initialized(self.openhands_core_lib)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            returned_results = self.job.run()

        self.assertIsNone(returned_results)
        self.assertEqual(stdout.getvalue().strip(), json.dumps(results))

    def test_run_sends_failure_notification_before_reraising(self) -> None:
        notification_service = Mock()
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.side_effect = RuntimeError('service down')
        self.openhands_core_lib.service.notification_service = notification_service
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)

        with self.assertRaisesRegex(RuntimeError, 'service down'):
            self.job.run()

        notification_service.notify_failure.assert_called_once()
        self.job.logger.exception.assert_called_once_with(
            'process_assigned_tasks_job failed'
        )

    def test_run_preserves_original_error_when_failure_notification_breaks(self) -> None:
        notification_service = Mock()
        notification_service.notify_failure.side_effect = RuntimeError('mailer down')
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.side_effect = RuntimeError('service down')
        self.openhands_core_lib.service.notification_service = notification_service
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)

        with self.assertRaisesRegex(RuntimeError, 'service down'):
            self.job.run()

        self.assertEqual(self.job.logger.exception.call_count, 2)

    def test_run_loops_over_each_assigned_task(self) -> None:
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = ['task-1', 'task-2']
        self.openhands_core_lib.service.process_assigned_task.side_effect = [
            {'id': '17'},
            {'id': '18'},
        ]
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.initialized(self.openhands_core_lib)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            self.job.run()

        self.assertEqual(
            self.openhands_core_lib.service.process_assigned_task.call_args_list[0].args,
            ('task-1',),
        )
        self.assertEqual(
            self.openhands_core_lib.service.process_assigned_task.call_args_list[1].args,
            ('task-2',),
        )
