import types
import unittest
from unittest.mock import Mock

from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.task_service import TaskService


class TaskServiceTests(unittest.TestCase):
    def test_uses_configured_queue_states_and_comment_operations(self) -> None:
        config = types.SimpleNamespace(
            base_url='https://youtrack.example',
            token='yt-token',
            project='PROJ',
            assignee='me',
            issue_states=['Todo', 'Open'],
        )
        client = Mock()

        task_service = TaskService(config, TaskDataAccess(config, client))
        task_service.validate_connection()
        task_service.get_assigned_tasks()
        task_service.add_pull_request_comment('PROJ-1', 'https://bitbucket/pr/1')

        client.validate_connection.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        client.add_comment.assert_called_once_with(
            'PROJ-1',
            'Pull request created: https://bitbucket/pr/1',
        )

    def test_get_review_tasks_uses_review_state_only(self) -> None:
        config = types.SimpleNamespace(
            base_url='https://youtrack.example',
            token='yt-token',
            project='PROJ',
            assignee='me',
            review_state='To Verify',
            review_state_field='State',
            issue_states=['Todo', 'Open'],
        )
        client = Mock()

        task_service = TaskService(config, TaskDataAccess(config, client))
        task_service.get_review_tasks()

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['To Verify'],
        )

    def test_uses_legacy_issue_state_and_default_review_config(self) -> None:
        config = types.SimpleNamespace(
            base_url='https://youtrack.example',
            token='yt-token',
            project='PROJ',
            assignee='me',
            issue_state='Todo',
        )
        client = Mock()

        task_service = TaskService(config, TaskDataAccess(config, client))
        task_service.get_assigned_tasks()

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo'],
        )

    def test_excludes_review_state_from_legacy_issue_state_queue(self) -> None:
        config = types.SimpleNamespace(
            base_url='https://youtrack.example',
            token='yt-token',
            project='PROJ',
            assignee='me',
            issue_state='To Verify',
            review_state='To Verify',
        )
        client = Mock()

        task_service = TaskService(config, TaskDataAccess(config, client))
        task_service.get_assigned_tasks()

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=[],
        )

    def test_uses_explicit_review_config_and_parses_string_issue_states(self) -> None:
        config = types.SimpleNamespace(
            base_url='https://jira.example',
            token='jira-token',
            project='PROJ',
            assignee='me',
            issue_states='To Do, In Progress',
            progress_state_field='status',
            progress_state='In Progress',
            review_state_field='status',
            review_state='Code Review',
        )
        client = Mock()

        task_service = TaskService(config, TaskDataAccess(config, client))
        task_service.get_assigned_tasks()

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['To Do'],
        )

    def test_filters_review_and_progress_states_from_issue_queue(self) -> None:
        config = types.SimpleNamespace(
            base_url='https://youtrack.example',
            token='yt-token',
            project='PROJ',
            assignee='me',
            issue_states=['Open', 'In Progress', 'To Verify', 'Open'],
            progress_state_field='State',
            progress_state='In Progress',
            review_state_field='State',
            review_state='To Verify',
        )
        client = Mock()

        task_service = TaskService(config, TaskDataAccess(config, client))
        task_service.get_assigned_tasks()

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Open'],
        )
