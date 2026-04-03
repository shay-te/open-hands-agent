import types
import unittest
from unittest.mock import Mock

from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_failure_handler import TaskFailureHandler
from openhands_agent.data_layers.service.task_state_service import TaskStateService
from openhands_agent.data_layers.service.task_service import TaskService
from utils import build_task


class TaskFailureHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_state_service = Mock(spec=TaskStateService)
        self.repository_service = Mock(spec=RepositoryService)
        self.notification_service = Mock(spec=NotificationService)
        self.handler = TaskFailureHandler(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
        )
        self.handler.logger = Mock()

    def test_handle_repository_resolution_failure_comments_skip_for_repository_detection_error(
        self,
    ) -> None:
        task = build_task(description='Update client and backend APIs')

        self.handler.handle_repository_resolution_failure(
            task,
            ValueError('no configured repository matched task PROJ-1'),
        )

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('could not detect which repository', comment)
        self.assertIn('repository name or alias', comment)
        self.task_state_service.move_task_to_open.assert_not_called()
        self.notification_service.notify_failure.assert_not_called()

    def test_handle_task_failure_restores_repositories_and_notifies_without_reopening(self) -> None:
        prepared_task = types.SimpleNamespace(repositories=[types.SimpleNamespace(id='client')])
        task = build_task(description='Update client and backend APIs')

        self.handler.handle_task_failure(
            task,
            RuntimeError('repository service down'),
            prepared_task=prepared_task,
        )

        self.repository_service.restore_task_repositories.assert_called_once_with(
            prepared_task.repositories,
            force=True,
        )
        self.task_service.add_comment.assert_called_once()
        self.assertIn(
            'OpenHands agent could not safely process this task: repository service down',
            self.task_service.add_comment.call_args.args[1],
        )
        self.task_state_service.move_task_to_open.assert_not_called()
        self.notification_service.notify_failure.assert_called_once()
        notify_args = self.notification_service.notify_failure.call_args.args
        self.assertEqual(notify_args[0], 'process_assigned_task')
        self.assertEqual(str(notify_args[1]), 'repository service down')
        self.assertEqual(notify_args[2], {Task.id.key: task.id})

    def test_handle_started_task_failure_moves_task_back_to_open(self) -> None:
        prepared_task = types.SimpleNamespace(repositories=[types.SimpleNamespace(id='client')])
        task = build_task(description='Update client and backend APIs')

        self.handler.handle_started_task_failure(
            task,
            RuntimeError('push failed'),
            prepared_task=prepared_task,
        )

        self.repository_service.restore_task_repositories.assert_called_once_with(
            prepared_task.repositories,
            force=True,
        )
        self.task_state_service.move_task_to_open.assert_called_once_with(task.id)
        self.task_service.add_comment.assert_called_once()
        self.assertIn(
            'OpenHands agent stopped working on this task: push failed',
            self.task_service.add_comment.call_args.args[1],
        )
        self.notification_service.notify_failure.assert_called_once()
        notify_args = self.notification_service.notify_failure.call_args.args
        self.assertEqual(notify_args[0], 'process_assigned_task')
        self.assertEqual(str(notify_args[1]), 'push failed')
        self.assertEqual(notify_args[2], {Task.id.key: task.id})

    def test_handle_task_definition_failure_comments_skip_message(self) -> None:
        task = build_task(description='test')

        self.handler.handle_task_definition_failure(task)

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('task definition is too thin', comment)
        self.handler.logger.info.assert_any_call(
            'Mission %s: %s',
            task.id,
            'recording task-definition skip comment',
        )
        self.handler.logger.info.assert_any_call(
            'Mission %s: %s',
            task.id,
            'added task-definition skip comment',
        )
