from core_lib.data_layers.service.service import Service

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.retry_utils import retry_count
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import PullRequestFields, ReviewCommentFields
from openhands_agent.logging_utils import configure_logger


class ImplementationService(Service):
    def __init__(self, client: OpenHandsClient) -> None:
        self._client = client
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def max_retries(self) -> int:
        return retry_count(getattr(self._client, 'max_retries', 1))

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def implement_task(
        self,
        task: Task,
        session_id: str = '',
    ) -> dict[str, str | bool]:
        self.logger.info('delegating implementation for task %s', task.id)
        return self._client.implement_task(task)

    def review_comment_from_payload(self, payload: dict) -> ReviewComment:
        try:
            comment = ReviewComment(
                pull_request_id=str(payload[ReviewCommentFields.PULL_REQUEST_ID]),
                comment_id=str(payload[ReviewCommentFields.COMMENT_ID]),
                author=str(payload[ReviewCommentFields.AUTHOR]),
                body=str(payload[ReviewCommentFields.BODY]),
            )
            if PullRequestFields.REPOSITORY_ID in payload:
                setattr(comment, PullRequestFields.REPOSITORY_ID, str(payload[PullRequestFields.REPOSITORY_ID]))
            setattr(
                comment,
                ReviewCommentFields.ALL_COMMENTS,
                self._normalize_comment_context(payload.get(ReviewCommentFields.ALL_COMMENTS, [])),
            )
            return comment
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'invalid review comment payload: {exc}') from exc

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        self.logger.info(
            'delegating review fix for pull request %s comment %s',
            comment.pull_request_id,
            comment.comment_id,
        )
        return self._client.fix_review_comment(
            comment,
            branch_name,
            session_id,
            task_id=task_id,
            task_summary=task_summary,
        )

    @staticmethod
    def _normalize_comment_context(all_comments) -> list[dict[str, str]]:
        if not isinstance(all_comments, list):
            return []

        normalized_comments: list[dict[str, str]] = []
        for item in all_comments:
            if isinstance(item, ReviewComment):
                normalized_comments.append(
                    {
                        ReviewCommentFields.COMMENT_ID: str(item.comment_id),
                        ReviewCommentFields.AUTHOR: str(item.author),
                        ReviewCommentFields.BODY: str(item.body),
                    }
                )
                continue
            if not isinstance(item, dict):
                continue
            normalized_comment = {
                ReviewCommentFields.COMMENT_ID: str(item.get(ReviewCommentFields.COMMENT_ID, '')),
                ReviewCommentFields.AUTHOR: str(item.get(ReviewCommentFields.AUTHOR, '')),
                ReviewCommentFields.BODY: str(item.get(ReviewCommentFields.BODY, '')),
            }
            if not any(normalized_comment.values()):
                continue
            normalized_comments.append(normalized_comment)
        return normalized_comments
