import unittest
from unittest.mock import patch


from kato.client.bitbucket.client import BitbucketClient
from kato.client.github.client import GitHubClient
from kato.client.gitlab.client import GitLabClient
from kato.client.pull_request_client_factory import (
    build_pull_request_client,
    detect_pull_request_provider,
)


class PullRequestClientFactoryTests(unittest.TestCase):
    def test_detects_provider_from_repository_base_url(self) -> None:
        self.assertEqual(detect_pull_request_provider('https://api.github.com'), 'github')
        self.assertEqual(detect_pull_request_provider('https://gitlab.example/api/v4'), 'gitlab')
        self.assertEqual(detect_pull_request_provider('https://api.bitbucket.org/2.0'), 'bitbucket')

    def test_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unsupported repository provider'):
            detect_pull_request_provider('https://code.example.com/api')

    def test_builds_github_client(self) -> None:
        config = type('Config', (), {'base_url': 'https://api.github.com', 'token': 'gh-token'})
        self.assertIsInstance(build_pull_request_client(config, 3), GitHubClient)

    def test_builds_gitlab_client(self) -> None:
        config = type('Config', (), {'base_url': 'https://gitlab.example/api/v4', 'token': 'gl-token'})
        self.assertIsInstance(build_pull_request_client(config, 3), GitLabClient)

    def test_builds_bitbucket_client(self) -> None:
        config = type('Config', (), {'base_url': 'https://api.bitbucket.org/2.0', 'token': 'bb-token'})
        self.assertIsInstance(build_pull_request_client(config, 3), BitbucketClient)

    def test_builds_bitbucket_client_with_username(self) -> None:
        config = type(
            'Config',
            (),
            {
                'base_url': 'https://api.bitbucket.org/2.0',
                'token': 'bb-token',
                'username': 'bb-user',
            },
        )
        self.assertIsInstance(build_pull_request_client(config, 3), BitbucketClient)

    def test_builds_bitbucket_client_with_api_email(self) -> None:
        config = type(
            'Config',
            (),
            {
                'base_url': 'https://api.bitbucket.org/2.0',
                'token': 'bb-token',
                'api_email': 'bb-user@example.com',
            },
        )
        self.assertIsInstance(build_pull_request_client(config, 3), BitbucketClient)
