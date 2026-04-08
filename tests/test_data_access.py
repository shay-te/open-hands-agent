import types
import unittest
from unittest.mock import Mock


from kato.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from kato.data_layers.data_access.task_data_access import TaskDataAccess
from kato.data_layers.data.fields import ReviewCommentFields
from utils import build_review_comment


class TaskDataAccessTests(unittest.TestCase):
    def test_uses_project_and_runtime_values_for_client_calls(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        client = Mock()

        data_access = TaskDataAccess(config, client)
        data_access.validate_connection('me', ['Todo', 'Open'])
        data_access.get_assigned_tasks('me', ['Todo', 'Open'])
        data_access.add_comment('PROJ-1', 'Pull request created: https://bitbucket/pr/1')
        data_access.move_task_to_state('PROJ-1', 'State', 'In Progress')

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
        client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'State',
            'In Progress',
        )

    def test_validates_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        data_access = TaskDataAccess(
            config,
            types.SimpleNamespace(
                validate_connection=Mock(),
                add_comment=Mock(),
                get_assigned_tasks=Mock(),
                move_issue_to_state=Mock(),
            ),
        )

        with self.assertRaisesRegex(PermissionError, 'assignee'):
            data_access.validate_connection(17, ['Open'])

        with self.assertRaisesRegex(PermissionError, 'issue_id'):
            data_access.add_comment(['PROJ-1'], 'https://bitbucket/pr/1')

        with self.assertRaisesRegex(PermissionError, 'assignee'):
            data_access.get_assigned_tasks(assignee=17, states=['Open'])

        with self.assertRaisesRegex(PermissionError, 'states'):
            data_access.get_assigned_tasks(assignee='me', states='Open')

        with self.assertRaisesRegex(PermissionError, 'issue_id'):
            data_access.move_task_to_state(['PROJ-1'], 'State', 'Open')


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
            destination_branch='main',
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
                destination_branch='main',
                description='Ready for review',
            )

        with self.assertRaisesRegex(PermissionError, 'source_branch'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch=['feature/proj-1'],
                destination_branch='main',
                description='Ready for review',
            )

        with self.assertRaisesRegex(PermissionError, 'destination_branch'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1',
                destination_branch=['main'],
                description='Ready for review',
            )

        with self.assertRaisesRegex(PermissionError, 'description'):
            data_access.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1',
                destination_branch='main',
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

    def test_finds_pull_requests_via_client(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        client = types.SimpleNamespace(
            provider_name='bitbucket',
            find_pull_requests=Mock(return_value=['pr']),
        )

        data_access = PullRequestDataAccess(config, client)
        pull_requests = data_access.find_pull_requests(
            source_branch='PROJ-1',
            title_prefix='PROJ-1 ',
        )

        self.assertEqual(pull_requests, ['pr'])
        client.find_pull_requests.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            source_branch='PROJ-1',
            title_prefix='PROJ-1 ',
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

    def test_replies_to_review_comment_via_client(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            owner="workspace",
            repo_slug="repo",
            destination_branch="main",
        )
        comment = build_review_comment()
        client = types.SimpleNamespace(
            provider_name='bitbucket',
            reply_to_review_comment=Mock(),
        )

        data_access = PullRequestDataAccess(config, client)
        data_access.reply_to_review_comment(
            comment,
            'Done. Added the missing branch guard.',
        )

        client.reply_to_review_comment.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
            body='Done. Added the missing branch guard.',
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

    def test_validates_review_comment_reply_values(self) -> None:
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
                reply_to_review_comment=Mock(),
            ),
        )
        comment = build_review_comment()

        with self.assertRaisesRegex(PermissionError, 'body'):
            data_access.reply_to_review_comment(comment, ['not text'])
