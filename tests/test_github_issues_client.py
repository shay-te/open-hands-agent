import unittest
from unittest.mock import patch


from kato.client.github.issues_client import GitHubIssuesClient
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import GitHubCommentFields, GitHubIssueFields, TaskCommentFields
from utils import mock_response


class GitHubIssuesClientTests(unittest.TestCase):
    def test_validate_connection_checks_repository_issues(self) -> None:
        client = GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo')
        response = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('repo', 'octocat', ['open'])

        self.assertEqual(
            client.headers,
            {
                'Authorization': 'Bearer gh-token',
                'Accept': 'application/vnd.github+json',
            },
        )
        self.assertEqual(client.timeout, 30)
        mock_get.assert_called_once_with(
            '/repos/workspace/repo/issues',
            params={'assignee': 'octocat', 'state': 'all', 'per_page': 1},
        )

    def test_get_assigned_tasks_filters_pull_requests_and_loads_comments(self) -> None:
        client = GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo')
        issues_response = mock_response(
            json_data=[
                {
                    GitHubIssueFields.NUMBER: 17,
                    GitHubIssueFields.TITLE: 'Fix bug',
                    GitHubIssueFields.BODY: 'Details',
                    GitHubIssueFields.STATE: 'open',
                    GitHubIssueFields.LABELS: [
                        {GitHubIssueFields.NAME: 'repo:client'},
                        {GitHubIssueFields.NAME: 'priority:high'},
                    ],
                },
                {
                    GitHubIssueFields.NUMBER: 18,
                    GitHubIssueFields.STATE: 'open',
                    GitHubIssueFields.PULL_REQUEST: {'url': 'https://api.github.com/pr/18'},
                },
            ]
        )
        comments_response = mock_response(
            json_data=[
                {
                    GitHubCommentFields.BODY: 'Please add tests.',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'reviewer'},
                }
            ]
        )

        with patch.object(
            client,
            '_get',
            side_effect=[issues_response, comments_response],
        ) as mock_get:
            tasks = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], Task)
        self.assertEqual(tasks[0].id, '17')
        self.assertIn('reviewer: Please add tests.', tasks[0].description)
        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])
        self.assertEqual(mock_get.call_count, 2)

    def test_get_assigned_tasks_uses_issue_labels_as_task_tags(self) -> None:
        client = GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo')
        issues_response = mock_response(
            json_data=[
                {
                    GitHubIssueFields.NUMBER: 17,
                    GitHubIssueFields.TITLE: 'Fix bug',
                    GitHubIssueFields.BODY: 'Details',
                    GitHubIssueFields.STATE: 'open',
                    GitHubIssueFields.LABELS: [
                        {GitHubIssueFields.NAME: 'repo:client'},
                        {GitHubIssueFields.NAME: 'priority:high'},
                    ],
                }
            ]
        )
        comments_response = mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            tasks = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(tasks[0].tags, ['repo:client', 'priority:high'])

    def test_get_assigned_tasks_ignores_agent_operational_comments(self) -> None:
        client = GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo')
        issues_response = mock_response(
            json_data=[
                {
                    GitHubIssueFields.NUMBER: 17,
                    GitHubIssueFields.TITLE: 'Fix bug',
                    GitHubIssueFields.BODY: 'Details',
                    GitHubIssueFields.STATE: 'open',
                }
            ]
        )
        comments_response = mock_response(
            json_data=[
                {
                    GitHubCommentFields.BODY: 'Kato agent could not safely process this task: timeout',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'shay'},
                },
                {
                    GitHubCommentFields.BODY: 'Please add tests.',
                    GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'reviewer'},
                },
            ]
        )

        with patch.object(client, '_get', side_effect=[issues_response, comments_response]):
            tasks = client.get_assigned_tasks('repo', 'octocat', ['open'])

        self.assertEqual(len(tasks), 1)
        self.assertIn('reviewer: Please add tests.', tasks[0].description)
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
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'Please add tests.',
                },
            ],
        )

    def test_add_comment_posts_expected_payload(self) -> None:
        client = GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo')
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('17', 'Ready for review')

        mock_post.assert_called_once_with(
            '/repos/workspace/repo/issues/17/comments',
            json={GitHubCommentFields.BODY: 'Ready for review'},
        )

    def test_move_issue_to_review_adds_label_by_default(self) -> None:
        client = GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo')
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.move_issue_to_state('17', 'labels', 'In Review')

        mock_post.assert_called_once_with(
            '/repos/workspace/repo/issues/17/labels',
            json={GitHubIssueFields.LABELS: ['In Review']},
        )
