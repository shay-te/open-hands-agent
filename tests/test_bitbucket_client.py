import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.bitbucket_client import BitbucketClient
from openhands_agent.fields import PullRequestFields
from utils import assert_client_headers_and_timeout


class BitbucketClientTests(unittest.TestCase):
    def test_create_pull_request_normalizes_response(self) -> None:
        client = BitbucketClient('https://bitbucket.example', 'bb-token')
        response = Mock()
        response.json.return_value = {
            PullRequestFields.ID: 7,
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            'links': {'html': {'href': 'https://bitbucket/pr/7'}},
        }

        with patch.object(client, '_post', return_value=response) as mock_post:
            pr = client.create_pull_request(
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1',
                workspace='workspace',
                repo_slug='repo',
                destination_branch='main',
                description='Ready for review',
            )

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            pr,
            {
                PullRequestFields.ID: '7',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/7',
            },
        )
        assert_client_headers_and_timeout(self, client, 'bb-token', 30)
        mock_post.assert_called_once_with(
            '/repositories/workspace/repo/pullrequests',
            json={
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.DESCRIPTION: 'Ready for review',
                'source': {'branch': {'name': 'feature/proj-1'}},
                'destination': {'branch': {'name': 'main'}},
            },
        )
