import types
import unittest
from unittest.mock import Mock, patch

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
            data_access.move_task_to_review('PROJ-1')

        mock_client_cls.assert_not_called()
        client = mock_client_cls.return_value
        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        client.add_comment.assert_called_once_with(
            'PROJ-1',
            'Pull request created: https://bitbucket/pr/1',
        )
        client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'State',
            'In Review',
        )

    def test_validates_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            review_state_field='State',
            review_state='In Review',
            issue_states=["Todo", "Open"],
        )
        data_access = TaskDataAccess(config, types.SimpleNamespace())

        with self.assertRaisesRegex(ValueError, 'issue_id must be'):
            data_access.add_pull_request_comment(17, 'https://bitbucket/pr/1')

        with self.assertRaisesRegex(ValueError, 'assignee must be'):
            data_access.get_assigned_tasks(assignee=17)

        with self.assertRaisesRegex(ValueError, 'states must be'):
            data_access.get_assigned_tasks(states='Open')

        with self.assertRaisesRegex(ValueError, 'issue_id must be'):
            data_access.move_task_to_review(17)

    def test_uses_legacy_issue_state_and_default_review_config(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_state="Todo",
        )

        with patch(
            'openhands_agent.data_layers.data_access.task_data_access.YouTrackClient'
        ) as mock_client_cls:
            data_access = TaskDataAccess(config, mock_client_cls.return_value)
            data_access.get_assigned_tasks()
            data_access.move_task_to_review('PROJ-1')

        client = mock_client_cls.return_value
        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo'],
        )
        client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'State',
            'In Review',
        )


class PullRequestDataAccessTests(unittest.TestCase):
    def test_passes_repository_settings_to_client_call(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )

        client = types.SimpleNamespace(
            provider_name='bitbucket',
            create_pull_request=Mock(),
        )

        data_access = PullRequestDataAccess(config, client)
        data_access.create_pull_request(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            description='Ready for review',
        )

        client.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            repo_owner='workspace',
            repo_slug='repo',
            destination_branch='main',
            description='Ready for review',
        )

    def test_validates_create_pull_request_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
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

        with self.assertRaisesRegex(ValueError, 'source_branch must be'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch=None,
                description='Ready for review',
            )

        with self.assertRaisesRegex(ValueError, 'description must be'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1',
                description=None,
            )

    def test_prefers_runtime_destination_branch_override(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        client = types.SimpleNamespace(
            provider_name='bitbucket',
            create_pull_request=Mock(),
        )

        data_access = PullRequestDataAccess(config, client)
        data_access.create_pull_request(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            destination_branch='release',
            description='Ready for review',
        )

        client.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            repo_owner='workspace',
            repo_slug='repo',
            destination_branch='release',
            description='Ready for review',
        )

    def test_lists_pull_request_comments_via_client(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        client = types.SimpleNamespace(
            provider_name='bitbucket',
            list_pull_request_comments=Mock(return_value=['comment']),
        )

        data_access = PullRequestDataAccess(config, client)
        comments = data_access.list_pull_request_comments('17')

        self.assertEqual(comments, ['comment'])
        client.list_pull_request_comments.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            pull_request_id='17',
        )
