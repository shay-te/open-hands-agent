import logging

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import PullRequestFields


class ImplementationService:
    def __init__(self, client: OpenHandsClient) -> None:
        self._client = client
        self.logger = logging.getLogger(self.__class__.__name__)

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        self.logger.info('delegating implementation for task %s', task.id)
        return self._client.implement_task(task)

    def review_comment_from_payload(self, payload: dict) -> ReviewComment:
        try:
            comment = ReviewComment(
                pull_request_id=str(payload[ReviewComment.pull_request_id.key]),
                comment_id=str(payload[ReviewComment.comment_id.key]),
                author=str(payload[ReviewComment.author.key]),
                body=str(payload[ReviewComment.body.key]),
            )
            if PullRequestFields.REPOSITORY_ID in payload:
                setattr(comment, PullRequestFields.REPOSITORY_ID, str(payload[PullRequestFields.REPOSITORY_ID]))
            return comment
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'invalid review comment payload: {exc}') from exc

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        self.logger.info(
            'delegating review fix for pull request %s comment %s',
            comment.pull_request_id,
            comment.comment_id,
        )
        return self._client.fix_review_comment(comment, branch_name)
