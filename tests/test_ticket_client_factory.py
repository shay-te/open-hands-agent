import unittest
from unittest.mock import patch


from kato.client.ticket_client_factory import build_ticket_client
from utils import build_test_cfg


class TicketClientFactoryTests(unittest.TestCase):
    def test_builds_youtrack_client_by_default(self) -> None:
        cfg = build_test_cfg()

        with patch('kato.client.ticket_client_factory.YouTrackClient') as mock_client_cls:
            client = build_ticket_client('youtrack', cfg.kato.youtrack, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.youtrack.base_url,
            cfg.kato.youtrack.token,
            5,
        )

    def test_builds_jira_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato.client.ticket_client_factory.JiraClient') as mock_client_cls:
            client = build_ticket_client('jira', cfg.kato.jira, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.jira.base_url,
            cfg.kato.jira.token,
            cfg.kato.jira.email,
            5,
        )

    def test_builds_github_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato.client.ticket_client_factory.GitHubIssuesClient') as mock_client_cls:
            client = build_ticket_client('github', cfg.kato.github_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.github_issues.base_url,
            cfg.kato.github_issues.token,
            cfg.kato.github_issues.owner,
            cfg.kato.github_issues.repo,
            5,
        )

    def test_builds_gitlab_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato.client.ticket_client_factory.GitLabIssuesClient') as mock_client_cls:
            client = build_ticket_client('gitlab', cfg.kato.gitlab_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.gitlab_issues.base_url,
            cfg.kato.gitlab_issues.token,
            cfg.kato.gitlab_issues.project,
            5,
        )

    def test_builds_bitbucket_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato.client.ticket_client_factory.BitbucketIssuesClient') as mock_client_cls:
            client = build_ticket_client('bitbucket', cfg.kato.bitbucket_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.bitbucket_issues.base_url,
            cfg.kato.bitbucket_issues.token,
            cfg.kato.bitbucket_issues.workspace,
            cfg.kato.bitbucket_issues.repo_slug,
            5,
            username='',
        )

    def test_builds_bitbucket_issues_client_with_username(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.bitbucket_issues.username = 'bb-user'

        with patch('kato.client.ticket_client_factory.BitbucketIssuesClient') as mock_client_cls:
            client = build_ticket_client('bitbucket', cfg.kato.bitbucket_issues, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.bitbucket_issues.base_url,
            cfg.kato.bitbucket_issues.token,
            cfg.kato.bitbucket_issues.workspace,
            cfg.kato.bitbucket_issues.repo_slug,
            5,
            username='bb-user',
        )

    def test_rejects_unknown_ticket_system(self) -> None:
        cfg = build_test_cfg()

        with self.assertRaisesRegex(ValueError, 'unsupported issue platform'):
            build_ticket_client('linear', cfg.kato.youtrack, 5)
