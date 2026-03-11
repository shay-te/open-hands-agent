from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task


class ImplementationService:
    def __init__(self, client: OpenHandsClient) -> None:
        self.client = client

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        return self.client.implement_task(task)

    def review_comment_from_payload(self, payload: dict) -> ReviewComment:
        try:
            return ReviewComment(
                pull_request_id=str(payload[ReviewComment.pull_request_id.key]),
                comment_id=str(payload[ReviewComment.comment_id.key]),
                author=str(payload[ReviewComment.author.key]),
                body=str(payload[ReviewComment.body.key]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'invalid review comment payload: {exc}') from exc

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        return self.client.fix_review_comment(comment, branch_name)
