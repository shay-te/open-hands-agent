import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data.task import Task
from utils import assert_client_headers_and_timeout


class YouTrackClientTests(unittest.TestCase):
    def test_get_assigned_tasks_builds_query_and_maps_tasks(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        issue_response = Mock()
        issue_response.json.return_value = [
            {'idReadable': 'PROJ-1', 'summary': 'Fix bug', 'description': 'Details'}
        ]
        comments_response = Mock()
        comments_response.json.return_value = [
            {'text': 'Please keep the fix minimal.', 'author': {'name': 'Product Manager'}}
        ]
        attachments_response = Mock()
        attachments_response.json.return_value = [
            {
                'name': 'notes.txt',
                'mimeType': 'text/plain',
                'charset': 'utf-8',
                'url': '/api/files/notes.txt',
                'metaData': 'plain text',
            },
            {
                'name': 'bug.png',
                'mimeType': 'image/png',
                'url': '/api/files/bug.png',
                'metaData': '1920x1080',
            },
        ]
        text_attachment_response = Mock()
        text_attachment_response.text = 'Stack trace details'

        with patch.object(
            client,
            '_get',
            side_effect=[
                issue_response,
                comments_response,
                attachments_response,
                text_attachment_response,
            ],
        ) as mock_get:
            tasks = client.get_assigned_tasks(
                project='PROJ',
                assignee='me',
                states=['Todo', 'Open'],
            )

        issue_response.raise_for_status.assert_called_once_with()
        comments_response.raise_for_status.assert_called_once_with()
        attachments_response.raise_for_status.assert_called_once_with()
        text_attachment_response.raise_for_status.assert_called_once_with()
        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], Task)
        self.assertEqual(tasks[0].id, "PROJ-1")
        self.assertEqual(tasks[0].summary, "Fix bug")
        self.assertIn('Details', tasks[0].description)
        self.assertIn('Issue comments:', tasks[0].description)
        self.assertIn('Product Manager: Please keep the fix minimal.', tasks[0].description)
        self.assertIn('Text attachments:', tasks[0].description)
        self.assertIn('Attachment notes.txt:\nStack trace details', tasks[0].description)
        self.assertIn('Screenshot attachments:', tasks[0].description)
        self.assertIn('bug.png (1920x1080) /api/files/bug.png', tasks[0].description)
        self.assertEqual(tasks[0].branch_name, "feature/proj-1")
        assert_client_headers_and_timeout(self, client, 'yt-token', 30)
        self.assertEqual(
            mock_get.call_args_list,
            [
                unittest.mock.call(
                    '/api/issues',
                    params={
                        'query': 'project: PROJ assignee: me State: {Todo}, {Open}',
                        'fields': 'idReadable,summary,description',
                    },
                ),
                unittest.mock.call(
                    '/api/issues/PROJ-1/comments',
                    params={'fields': 'id,text,author(login,name)'},
                ),
                unittest.mock.call(
                    '/api/issues/PROJ-1/attachments',
                    params={'fields': 'id,name,mimeType,charset,metaData,url'},
                ),
                unittest.mock.call('/api/files/notes.txt'),
            ],
        )

    def test_add_pull_request_comment_posts_expected_payload(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        response = Mock()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_pull_request_comment('PROJ-1', 'https://bitbucket/pr/1')

        response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            '/api/issues/PROJ-1/comments',
            json={'text': 'Pull request created: https://bitbucket/pr/1'},
        )
