from kato.client.bitbucket.issues_client import BitbucketIssuesClient
from kato.client.github.issues_client import GitHubIssuesClient
from kato.client.gitlab.issues_client import GitLabIssuesClient
from kato.client.jira.client import JiraClient
from kato.client.youtrack.client import YouTrackClient


def build_ticket_client(issue_platform: str, config, max_retries: int):
    normalized = str(issue_platform or 'youtrack').strip().lower()
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
        return BitbucketIssuesClient(
            config.base_url,
            config.token,
            getattr(config, 'workspace', ''),
            getattr(config, 'repo_slug', ''),
            max_retries,
            username=getattr(config, 'username', ''),
        )
    raise ValueError(f'unsupported issue platform: {issue_platform}')
