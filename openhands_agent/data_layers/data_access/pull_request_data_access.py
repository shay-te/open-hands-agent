from __future__ import annotations

from omegaconf import DictConfig

from openhands_agent.client.bitbucket_client import BitbucketClient


class PullRequestDataAccess:
    def __init__(self, config: DictConfig) -> None:
        self.config = config
        self.client = BitbucketClient(config.base_url)

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        destination_branch: str | None = None,
        description: str = "",
    ) -> dict[str, str]:
        return self.client.create_pull_request(
            title=title,
            source_branch=source_branch,
            token=self.config.token,
            workspace=self.config.workspace,
            repo_slug=self.config.repo_slug,
            destination_branch=destination_branch or self.config.destination_branch,
            description=description,
        )
