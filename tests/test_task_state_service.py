import types
import unittest
from unittest.mock import Mock

from kato.data_layers.data_access.task_data_access import TaskDataAccess
from kato.data_layers.service.task_state_service import TaskStateService


class TaskStateServiceTests(unittest.TestCase):
    def test_uses_configured_queue_states_and_state_transitions(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_in_progress('PROJ-1')
        task_state_service.move_task_to_review('PROJ-1')
        task_state_service.move_task_to_open('PROJ-1')

        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'In Review'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )

    def test_uses_legacy_issue_state_and_default_review_config(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_state="Todo",
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_in_progress('PROJ-1')
        task_state_service.move_task_to_review('PROJ-1')
        task_state_service.move_task_to_open('PROJ-1')

        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'In Review'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )

    def test_uses_explicit_review_config_and_parses_string_issue_states(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://jira.example",
            token="jira-token",
            project="PROJ",
            assignee="me",
            issue_states="To Do, In Progress",
            progress_state_field='status',
            progress_state='In Progress',
            review_state_field='status',
            review_state='Code Review',
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_in_progress('PROJ-1')
        task_state_service.move_task_to_review('PROJ-1')
        task_state_service.move_task_to_open('PROJ-1')

        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'status', 'In Progress'),
                unittest.mock.call('PROJ-1', 'status', 'Code Review'),
                unittest.mock.call('PROJ-1', 'status', 'To Do'),
            ],
        )

    def test_prefers_explicit_open_state_when_configured(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://jira.example",
            token="jira-token",
            project="PROJ",
            assignee="me",
            issue_states="To Do, In Progress",
            progress_state_field='status',
            progress_state='In Progress',
            review_state_field='status',
            review_state='Code Review',
            open_state_field='status',
            open_state='Open',
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_open('PROJ-1')

        client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'status',
            'Open',
        )
