from __future__ import annotations

from omegaconf import DictConfig

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task


class ImplementationDataAccess:
    def __init__(self, config: DictConfig) -> None:
        self.config = config
        self.client = OpenHandsClient(config.base_url)

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        return self.client.implement_task(self.config.api_key, task)

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        return self.client.fix_review_comment(self.config.api_key, comment, branch_name)
