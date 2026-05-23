from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from github_core_lib.github_core_lib.client.github_client import GitHubClient
from github_core_lib.github_core_lib.client.github_issues_client import GitHubIssuesClient


class GitHubCoreLib(CoreLib):
    """Compose GitHub repository and issue clients."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        github_cfg = cfg.core_lib.github_core_lib
        repo = github_cfg.get('repo', '') or github_cfg.get('repo_slug', '')
        self.pull_request = GitHubClient(
            github_cfg.base_url,
            github_cfg.token,
            github_cfg.max_retries,
        )
        # ``assignee`` is the GitHub login the host scans tasks under
        # — re-used as ``bot_login`` so the client can filter
        # @-mentioned-elsewhere comments. Defaults to empty when the
        # older config yaml omits the key.
        self.issue = GitHubIssuesClient(
            github_cfg.base_url,
            github_cfg.token,
            github_cfg.owner,
            repo,
            github_cfg.max_retries,
            bot_login=str(getattr(github_cfg, 'assignee', '') or ''),
        )
