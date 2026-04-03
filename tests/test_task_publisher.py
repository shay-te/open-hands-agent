import types
import unittest
from unittest.mock import Mock

from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_failure_handler import TaskFailureHandler
from openhands_agent.data_layers.service.task_state_service import TaskStateService
from openhands_agent.data_layers.service.task_publisher import TaskPublisher
from openhands_agent.data_layers.service.task_service import TaskService
from utils import build_task


class TaskPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_service.add_comment = Mock()
        self.task_state_service = Mock(spec=TaskStateService)
        self.task_state_service.move_task_to_review = Mock()
        self.repository_service = Mock(spec=RepositoryService)
        self.notification_service = Mock(spec=NotificationService)
        self.state_registry = Mock(spec=AgentStateRegistry)
        self.failure_handler = Mock(spec=TaskFailureHandler)
        self.publisher = TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
        )

    def test_publish_task_execution_marks_processed_and_moves_to_review(self) -> None:
        task = build_task(description='Update client and backend APIs')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='backend', destination_branch='main'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'backend': 'feature/proj-1/backend',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.SESSION_ID: 'conversation-1',
            Task.summary.key: 'Files changed:\n- client/app.ts\n  Updated the client flow.',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified the implementation.',
        }
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                PullRequestFields.ID: '18',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://github/pr/18',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                PullRequestFields.DESTINATION_BRANCH: 'main',
            },
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.task_state_service.move_task_to_review.assert_called_once_with(task.id)
        self.state_registry.mark_task_processed.assert_called_once()
        processed_args = self.state_registry.mark_task_processed.call_args.args
        self.assertEqual(processed_args[0], task.id)
        self.assertEqual(
            [pull_request[PullRequestFields.REPOSITORY_ID] for pull_request in processed_args[1]],
            ['client', 'backend'],
        )
        self.notification_service.notify_task_ready_for_review.assert_called_once()
        self.assertEqual(self.repository_service.create_pull_request.call_count, 2)
        first_call = self.repository_service.create_pull_request.call_args_list[0]
        self.assertEqual(first_call.kwargs['title'], 'PROJ-1 Fix bug')
        self.assertIn('Requested change:', first_call.kwargs['description'])
        self.assertIn('Implementation summary:', first_call.kwargs['description'])
        self.assertIn('Execution notes:', first_call.kwargs['description'])
        self.assertEqual(self.task_service.add_comment.call_count, 1)
        self.assertIn(
            'Published review links:',
            self.task_service.add_comment.call_args.args[1],
        )
        self.state_registry.remember_pull_request_context.assert_called()

    def test_publish_task_execution_partial_failure_reports_failure(self) -> None:
        task = build_task(description='Update client and backend APIs')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='backend', destination_branch='main'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'backend': 'feature/proj-1/backend',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.SESSION_ID: 'conversation-1',
            Task.summary.key: 'Files changed:\n- client/app.ts',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified the implementation.',
        }
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            RuntimeError('github down'),
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        self.assertEqual(
            result[PullRequestFields.FAILED_REPOSITORIES],
            ['backend'],
        )
        self.failure_handler.handle_started_task_failure.assert_called_once()
        failure_args, failure_kwargs = self.failure_handler.handle_started_task_failure.call_args
        self.assertEqual(failure_args[0], task)
        self.assertEqual(str(failure_args[1]), 'failed to create pull requests for repositories: backend')
        self.assertEqual(failure_kwargs['prepared_task'], prepared_task)

    def test_publish_task_execution_failure_to_move_review_calls_failure_handler(self) -> None:
        task = build_task(description='Update client and backend APIs')
        prepared_task = types.SimpleNamespace(
            repositories=[types.SimpleNamespace(id='client', destination_branch='master')],
            repository_branches={'client': 'feature/proj-1/client'},
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.SESSION_ID: 'conversation-1',
            Task.summary.key: 'Files changed:\n- client/app.ts',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified the implementation.',
        }
        self.repository_service.create_pull_request.return_value = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
            PullRequestFields.DESTINATION_BRANCH: 'master',
        }
        self.task_state_service.move_task_to_review.side_effect = RuntimeError('transition failed')

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertIsNone(result)
        self.failure_handler.handle_started_task_failure.assert_called_once()
        failure_args, failure_kwargs = self.failure_handler.handle_started_task_failure.call_args
        self.assertEqual(failure_args[0], task)
        self.assertEqual(str(failure_args[1]), 'transition failed')
        self.assertEqual(failure_kwargs['prepared_task'], prepared_task)
        self.state_registry.mark_task_processed.assert_not_called()
        self.notification_service.notify_task_ready_for_review.assert_not_called()

    def test_comment_task_started_uses_repository_context(self) -> None:
        task = build_task(description='Update client and backend APIs')

        self.publisher.comment_task_started(
            task,
            [types.SimpleNamespace(id='client'), types.SimpleNamespace(id='backend')],
        )

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('started working on this task in repositories: client, backend', comment)
