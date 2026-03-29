import types
import unittest
from unittest.mock import Mock


from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.fields import ReviewCommentFields
from utils import build_review_comment


class TaskDataAccessTests(unittest.TestCase):
    def test_uses_base_url_only_for_client_and_passes_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        client = Mock()

        data_access = TaskDataAccess(config, client)
        data_access.get_assigned_tasks()
        data_access.move_task_to_in_progress('PROJ-1')
        data_access.add_pull_request_comment('PROJ-1', 'https://bitbucket/pr/1')
        data_access.move_task_to_review('PROJ-1')
        data_access.move_task_to_open('PROJ-1')

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        client.add_comment.assert_called_once_with(
            'PROJ-1',
            'Pull request created: https://bitbucket/pr/1',
        )
        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call(
                    'PROJ-1',
                    'State',
                    'In Progress',
                ),
                unittest.mock.call(
                    'PROJ-1',
                    'State',
                    'In Review',
                ),
                unittest.mock.call(
                    'PROJ-1',
                    'State',
                    'Todo',
                ),
            ],
        )

    def test_get_review_tasks_uses_review_state_only(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            review_state='To Verify',
            review_state_field='State',
            issue_states=["Todo", "Open"],
        )
        client = Mock()

        data_access = TaskDataAccess(config, client)
        data_access.get_review_tasks()

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['To Verify'],
        )

    def test_validates_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            progress_state_field='State',
            progress_state='In Progress',
            review_state_field='State',
            review_state='To Verify',
            issue_states=["Todo", "Open"],
        )
        data_access = TaskDataAccess(
            config,
            types.SimpleNamespace(
                add_comment=Mock(),
                get_assigned_tasks=Mock(),
                move_issue_to_state=Mock(),
            ),
        )

        with self.assertRaisesRegex(PermissionError, 'issue_id'):
            data_access.add_pull_request_comment(['PROJ-1'], 'https://bitbucket/pr/1')

        with self.assertRaisesRegex(PermissionError, 'assignee'):
            data_access.get_assigned_tasks(assignee=17)

        with self.assertRaisesRegex(PermissionError, 'states'):
            data_access.get_assigned_tasks(states='Open')

        with self.assertRaisesRegex(PermissionError, 'issue_id'):
            data_access.move_task_to_review(['PROJ-1'])

        with self.assertRaisesRegex(PermissionError, 'issue_id'):
            data_access.move_task_to_in_progress(['PROJ-1'])

        with self.assertRaisesRegex(PermissionError, 'issue_id'):
            data_access.move_task_to_open(['PROJ-1'])

    def test_uses_legacy_issue_state_and_default_review_config(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_state="Todo",
        )
        client = Mock()

        data_access = TaskDataAccess(config, client)
        data_access.get_assigned_tasks()
        data_access.move_task_to_in_progress('PROJ-1')
        data_access.move_task_to_review('PROJ-1')
        data_access.move_task_to_open('PROJ-1')

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo'],
        )
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

        data_access = TaskDataAccess(config, client)
        data_access.get_assigned_tasks()
        data_access.move_task_to_in_progress('PROJ-1')
        data_access.move_task_to_review('PROJ-1')
        data_access.move_task_to_open('PROJ-1')

        client.get_assigned_tasks.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['To Do', 'In Progress'],
        )
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

        data_access = TaskDataAccess(config, client)
        data_access.move_task_to_open('PROJ-1')

        client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'status',
            'Open',
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
        data_access = PullRequestDataAccess(
            config,
            types.SimpleNamespace(
                create_pull_request=Mock(),
            ),
        )

        with self.assertRaisesRegex(PermissionError, 'title'):
            data_access.create_pull_request(
                title=['PROJ-1: Fix bug'],
                source_branch='feature/proj-1',
                description='Ready for review',
            )

        with self.assertRaisesRegex(PermissionError, 'source_branch'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch=['feature/proj-1'],
                description='Ready for review',
            )

        with self.assertRaisesRegex(PermissionError, 'description'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1',
                description=['Ready for review'],
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

    def test_resolves_review_comment_via_client(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        comment = build_review_comment(
            resolution_target_id='thread-99',
            resolution_target_type='thread',
            resolvable=True,
        )
        client = types.SimpleNamespace(
            provider_name='bitbucket',
            resolve_review_comment=Mock(),
        )

        data_access = PullRequestDataAccess(config, client)
        data_access.resolve_review_comment(comment)

        client.resolve_review_comment.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
        )

    def test_validates_resolve_review_comment_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        data_access = PullRequestDataAccess(
            config,
            types.SimpleNamespace(
                resolve_review_comment=Mock(),
            ),
        )
        comment = build_review_comment()
        comment.comment_id = ['99']

        with self.assertRaisesRegex(PermissionError, ReviewCommentFields.COMMENT_ID):
            data_access.resolve_review_comment(comment)
