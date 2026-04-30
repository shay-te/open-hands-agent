import unittest
from unittest.mock import patch


from kato.client.gitlab.issues_client import GitLabIssuesClient
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import GitLabCommentFields, GitLabIssueFields, TaskCommentFields
from utils import mock_response


class GitLabIssuesClientTests(unittest.TestCase):
    def test_validate_connection_checks_project_issues(self) -> None:
        client = GitLabIssuesClient('https://gitlab.example/api/v4', 'gl-token', 'group/repo')
        response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('group/repo', 'developer', ['opened'])

        self.assertEqual(client.headers, {'PRIVATE-TOKEN': 'gl-token'})
        mock_get.assert_called_once_with(
            '/projects/group%2Frepo/issues',
            params={'assignee_username': 'developer', 'state': 'all', 'per_page': 1},
        )

    def test_get_assigned_tasks_loads_notes(self) -> None:
        client = GitLabIssuesClient('https://gitlab.example/api/v4', 'gl-token', 'group/repo')
        issues_response = mock_response(
            json_data=[
                {
                    'iid': 17,
                    'title': 'Fix bug',
                    'description': 'Details',
                    'state': 'opened',
                    GitLabIssueFields.LABELS: ['repo:client', 'priority:high'],
                }
            ]
        )
        notes_response = mock_response(
            json_data=[
                {
                    GitLabCommentFields.BODY: 'Please add tests.',
                    GitLabCommentFields.AUTHOR: {GitLabCommentFields.NAME: 'Reviewer'},
                }
            ]
        )

        with patch.object(
            client,
            '_get',
            side_effect=[issues_response, notes_response],
        ):
            tasks = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], Task)
        self.assertEqual(tasks[0].id, '17')
        self.assertIn('Reviewer: Please add tests.', tasks[0].description)
        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])

    def test_get_assigned_tasks_uses_issue_labels_as_task_tags(self) -> None:
        client = GitLabIssuesClient('https://gitlab.example/api/v4', 'gl-token', 'group/repo')
        issues_response = mock_response(
            json_data=[
                {
                    'iid': 17,
                    'title': 'Fix bug',
                    'description': 'Details',
                    'state': 'opened',
                    GitLabIssueFields.LABELS: ['repo:client', 'priority:high'],
                }
            ]
        )
        notes_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            tasks = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])

    def test_get_assigned_tasks_ignores_agent_operational_comments(self) -> None:
        client = GitLabIssuesClient('https://gitlab.example/api/v4', 'gl-token', 'group/repo')
        issues_response = mock_response(
            json_data=[
                {
                    'iid': 17,
                    'title': 'Fix bug',
                    'description': 'Details',
                    'state': 'opened',
                }
            ]
        )
        notes_response = mock_response(
            json_data=[
                {
                    GitLabCommentFields.BODY: 'Kato agent could not safely process this task: timeout',
                    GitLabCommentFields.AUTHOR: {GitLabCommentFields.NAME: 'shay'},
                },
                {
                    GitLabCommentFields.BODY: 'Please add tests.',
                    GitLabCommentFields.AUTHOR: {GitLabCommentFields.NAME: 'Reviewer'},
                },
            ]
        )

        with patch.object(client, '_get', side_effect=[issues_response, notes_response]):
            tasks = client.get_assigned_tasks('group/repo', 'developer', ['opened'])

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

    def test_move_issue_to_review_adds_labels(self) -> None:
        client = GitLabIssuesClient('https://gitlab.example/api/v4', 'gl-token', 'group/repo')
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.move_issue_to_state('17', 'labels', 'In Review')

        mock_put.assert_called_once_with(
            '/projects/group%2Frepo/issues/17',
            json={'add_labels': 'In Review'},
        )
