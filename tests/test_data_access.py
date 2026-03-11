import types
import unittest
from unittest.mock import patch

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess


class TaskDataAccessTests(unittest.TestCase):
    def test_uses_base_url_only_for_client_and_passes_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )

        with patch(
            'openhands_agent.data_layers.data_access.task_data_access.YouTrackClient'
        ) as mock_client_cls:
            data_access = TaskDataAccess(config, mock_client_cls.return_value)
            data_access.get_assigned_tasks()
            data_access.add_pull_request_comment('PROJ-1', 'https://bitbucket/pr/1')

        mock_client_cls.assert_not_called()
        client = mock_client_cls.return_value
        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        client.add_pull_request_comment.assert_called_once_with(
            'PROJ-1',
            'https://bitbucket/pr/1',
        )

    def test_validates_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        data_access = TaskDataAccess(config, types.SimpleNamespace())

        with self.assertRaisesRegex(ValueError, 'issue_id must be'):
            data_access.add_pull_request_comment(17, 'https://bitbucket/pr/1')

        with self.assertRaisesRegex(ValueError, 'assignee must be'):
            data_access.get_assigned_tasks(assignee=17)

        with self.assertRaisesRegex(ValueError, 'states must be'):
            data_access.get_assigned_tasks(states='Open')


class PullRequestDataAccessTests(unittest.TestCase):
    def test_passes_bitbucket_settings_to_client_call(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            workspace="workspace",
            repo_slug="repo",
            destination_branch="main",
        )

        with patch(
            'openhands_agent.data_layers.data_access.pull_request_data_access.BitbucketClient'
        ) as mock_client_cls:
            data_access = PullRequestDataAccess(config, mock_client_cls.return_value)
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1',
                description='Ready for review',
            )

        mock_client_cls.assert_not_called()
        mock_client_cls.return_value.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            workspace='workspace',
            repo_slug='repo',
            destination_branch='main',
            description='Ready for review',
        )

    def test_validates_create_pull_request_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            workspace="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        data_access = PullRequestDataAccess(config, types.SimpleNamespace())

        with self.assertRaisesRegex(ValueError, 'title must be'):
            data_access.create_pull_request(
                title=17,
                source_branch='feature/proj-1',
                description='Ready for review',
            )
