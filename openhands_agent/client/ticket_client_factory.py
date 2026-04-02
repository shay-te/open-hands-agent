from openhands_agent.client.bitbucket_issues_client import BitbucketIssuesClient
from openhands_agent.client.github_issues_client import GitHubIssuesClient
from openhands_agent.client.gitlab_issues_client import GitLabIssuesClient
from openhands_agent.client.jira_client import JiraClient
from openhands_agent.client.youtrack_client import YouTrackClient


def build_ticket_client(ticket_system: str, config, max_retries: int):
    normalized = str(ticket_system or 'youtrack').strip().lower()
    if normalized == 'youtrack':
        return YouTrackClient(config.base_url, config.token, max_retries)
    if normalized == 'jira':
        return JiraClient(
            config.base_url,
            config.token,
            getattr(config, 'email', ''),
            max_retries,
        )
    if normalized in {'github', 'github_issues'}:
        return GitHubIssuesClient(
            config.base_url,
            config.token,
            getattr(config, 'owner', ''),
            getattr(config, 'repo', ''),
            max_retries,
        )
    if normalized in {'gitlab', 'gitlab_issues'}:
        return GitLabIssuesClient(
            config.base_url,
            config.token,
            getattr(config, 'project', ''),
            max_retries,
        )
    if normalized in {'bitbucket', 'bitbucket_issues'}:
        username = getattr(config, 'username', '')
        if username:
            return BitbucketIssuesClient(
                config.base_url,
                config.token,
                getattr(config, 'workspace', ''),
                getattr(config, 'repo_slug', ''),
                max_retries,
                username=username,
            )
        return BitbucketIssuesClient(
            config.base_url,
            config.token,
            getattr(config, 'workspace', ''),
            getattr(config, 'repo_slug', ''),
            max_retries,
        )
    raise ValueError(f'unsupported issue platform: {ticket_system}')
