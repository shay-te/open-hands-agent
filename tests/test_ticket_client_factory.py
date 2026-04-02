import unittest
from unittest.mock import patch


from openhands_agent.client.ticket_client_factory import build_ticket_client
from utils import build_test_cfg


class TicketClientFactoryTests(unittest.TestCase):
    def test_builds_youtrack_client_by_default(self) -> None:
        cfg = build_test_cfg()

        with patch('openhands_agent.client.ticket_client_factory.YouTrackClient') as mock_client_cls:
            client = build_ticket_client('youtrack', cfg.openhands_agent.youtrack, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.openhands_agent.youtrack.base_url,
            cfg.openhands_agent.youtrack.token,
            5,
        )

    def test_builds_jira_client(self) -> None:
        cfg = build_test_cfg()

        with patch('openhands_agent.client.ticket_client_factory.JiraClient') as mock_client_cls:
            client = build_ticket_client('jira', cfg.openhands_agent.jira, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.openhands_agent.jira.base_url,
            cfg.openhands_agent.jira.token,
            cfg.openhands_agent.jira.email,
            5,
        )

    def test_builds_github_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('openhands_agent.client.ticket_client_factory.GitHubIssuesClient') as mock_client_cls:
            client = build_ticket_client('github', cfg.openhands_agent.github_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.openhands_agent.github_issues.base_url,
            cfg.openhands_agent.github_issues.token,
            cfg.openhands_agent.github_issues.owner,
            cfg.openhands_agent.github_issues.repo,
            5,
        )

    def test_builds_gitlab_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('openhands_agent.client.ticket_client_factory.GitLabIssuesClient') as mock_client_cls:
            client = build_ticket_client('gitlab', cfg.openhands_agent.gitlab_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.openhands_agent.gitlab_issues.base_url,
            cfg.openhands_agent.gitlab_issues.token,
            cfg.openhands_agent.gitlab_issues.project,
            5,
        )

    def test_builds_bitbucket_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('openhands_agent.client.ticket_client_factory.BitbucketIssuesClient') as mock_client_cls:
            client = build_ticket_client('bitbucket', cfg.openhands_agent.bitbucket_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.openhands_agent.bitbucket_issues.base_url,
            cfg.openhands_agent.bitbucket_issues.token,
            cfg.openhands_agent.bitbucket_issues.workspace,
            cfg.openhands_agent.bitbucket_issues.repo_slug,
            5,
            username='',
        )

    def test_builds_bitbucket_issues_client_with_username(self) -> None:
        cfg = build_test_cfg()
        cfg.openhands_agent.bitbucket_issues.username = 'bb-user'

        with patch('openhands_agent.client.ticket_client_factory.BitbucketIssuesClient') as mock_client_cls:
            client = build_ticket_client('bitbucket', cfg.openhands_agent.bitbucket_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.openhands_agent.bitbucket_issues.base_url,
            cfg.openhands_agent.bitbucket_issues.token,
            cfg.openhands_agent.bitbucket_issues.workspace,
            cfg.openhands_agent.bitbucket_issues.repo_slug,
            5,
            username='bb-user',
        )

    def test_rejects_unknown_ticket_system(self) -> None:
        cfg = build_test_cfg()

        with self.assertRaisesRegex(ValueError, 'unsupported issue platform'):
            build_ticket_client('linear', cfg.openhands_agent.youtrack, 5)
