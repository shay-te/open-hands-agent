from abc import ABC, abstractmethod

from openhands_agent.client.retrying_client_base import RetryingClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment


class PullRequestClientBase(RetryingClientBase, ABC):
    provider_name = 'repository'

    @abstractmethod
    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        raise NotImplementedError
