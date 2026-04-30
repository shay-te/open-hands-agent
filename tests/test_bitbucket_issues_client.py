import unittest
from unittest.mock import patch


from kato.client.bitbucket.issues_client import BitbucketIssuesClient
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import BitbucketIssueFields, TaskCommentFields
from utils import assert_client_basic_auth_and_timeout, mock_response


class BitbucketIssuesClientTests(unittest.TestCase):
    def test_validate_connection_checks_repository_issues(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        response = mock_response(json_data={'values': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('repo', 'reviewer', ['new'])

        mock_get.assert_called_once_with(
            '/repositories/workspace/repo/issues',
            params={'pagelen': 1},
        )

    def test_uses_basic_auth_when_username_is_configured(self) -> None:
        client = BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0',
            'bb-token',
            'workspace',
            'repo',
            username='bb-user',
        )

        assert_client_basic_auth_and_timeout(self, client, 'bb-user', 'bb-token', 30)

    def test_get_assigned_tasks_filters_by_assignee_and_loads_comments(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        issues_response = mock_response(
            json_data={
                'values': [
                {
                    'id': 17,
                    'title': 'Fix bug',
                    'content': {'raw': 'Details'},
                    'state': 'new',
                    'assignee': {'nickname': 'reviewer'},
                    BitbucketIssueFields.LABELS: ['repo:client', 'priority:high'],
                },
                {
                    'id': 18,
                    'title': 'Skip me',
                        'state': 'new',
                        'assignee': {'nickname': 'someone-else'},
                    },
                ]
            }
        )
        comments_response = mock_response(
            json_data={
                'values': [
                    {
                        'content': {'raw': 'Please add tests.'},
                        'user': {'display_name': 'Reviewer'},
                    }
                ]
            }
        )

        with patch.object(
            client,
            '_get',
            side_effect=[issues_response, comments_response],
        ):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], Task)
        self.assertEqual(tasks[0].id, '17')
        self.assertIn('Reviewer: Please add tests.', tasks[0].description)
        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])

    def test_get_assigned_tasks_uses_issue_labels_as_task_tags(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        issues_response = mock_response(
            json_data={
                'values': [
                    {
                        'id': 17,
                        'title': 'Fix bug',
                        'content': {'raw': 'Details'},
                        'state': 'new',
                        'assignee': {'nickname': 'reviewer'},
                        BitbucketIssueFields.LABELS: ['repo:client', 'priority:high'],
                    }
                ]
            }
        )
        comments_response = mock_response(json_data={'values': []})

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])

    def test_get_assigned_tasks_ignores_agent_operational_comments(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        issues_response = mock_response(
            json_data={
                'values': [
                    {
                        'id': 17,
                        'title': 'Fix bug',
                        'content': {'raw': 'Details'},
                        'state': 'new',
                        'assignee': {'nickname': 'reviewer'},
                    }
                ]
            }
        )
        comments_response = mock_response(
            json_data={
                'values': [
                    {
                        'content': {'raw': 'Kato agent could not safely process this task: timeout'},
                        'user': {'display_name': 'shay'},
                    },
                    {
                        'content': {'raw': 'Please add tests.'},
                        'user': {'display_name': 'Reviewer'},
                    },
                ]
            }
        )

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            tasks = client.get_assigned_tasks('repo', 'reviewer', ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertIn('Reviewer: Please add tests.', tasks[0].description)
        self.assertNotIn('could not safely process this task', tasks[0].description)
        self.assertEqual(
            getattr(tasks[0], TaskCommentFields.ALL_COMMENTS),
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato agent could not safely process this task: timeout'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'Reviewer',
                    TaskCommentFields.BODY: 'Please add tests.',
                },
            ],
        )

    def test_add_comment_posts_raw_content_payload(self) -> None:
        client = BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo')
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('17', 'Ready for review')

        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/issues/17/comments',
            json={'content': {'raw': 'Ready for review'}},
        )
