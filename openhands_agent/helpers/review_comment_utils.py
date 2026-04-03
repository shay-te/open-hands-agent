from dataclasses import dataclass

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from openhands_agent.helpers.text_utils import text_from_mapping


@dataclass(frozen=True)
class ReviewFixContext:
    repository_id: str
    branch_name: str
    session_id: str
    task_id: str
    task_summary: str


def review_comment_from_payload(payload: dict) -> ReviewComment:
    try:
        comment = ReviewComment(
            pull_request_id=str(payload[ReviewCommentFields.PULL_REQUEST_ID]),
            comment_id=str(payload[ReviewCommentFields.COMMENT_ID]),
            author=str(payload[ReviewCommentFields.AUTHOR]),
            body=str(payload[ReviewCommentFields.BODY]),
        )
        if PullRequestFields.REPOSITORY_ID in payload:
            setattr(
                comment,
                PullRequestFields.REPOSITORY_ID,
                str(payload[PullRequestFields.REPOSITORY_ID]),
            )
        setattr(
            comment,
            ReviewCommentFields.ALL_COMMENTS,
            normalize_comment_context(payload.get(ReviewCommentFields.ALL_COMMENTS, [])),
        )
        return comment
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'invalid review comment payload: {exc}') from exc


def comment_context_entry(comment: ReviewComment) -> dict[str, str]:
    return {
        ReviewCommentFields.COMMENT_ID: str(comment.comment_id),
        ReviewCommentFields.AUTHOR: str(comment.author),
        ReviewCommentFields.BODY: str(comment.body),
    }


def review_comment_resolution_key(comment: ReviewComment) -> tuple[str, str]:
    resolution_target_type = str(
        getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, '') or 'comment'
    ).strip() or 'comment'
    resolution_target_id = str(
        getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '')
        or comment.comment_id
        or ''
    ).strip()
    return resolution_target_type, resolution_target_id


def review_comment_fixed_comment(comment: ReviewComment) -> str:
    return (
        'OpenHands addressed review comment '
        f'{comment.comment_id} on pull request {comment.pull_request_id}.'
    )


def normalize_comment_context(all_comments) -> list[dict[str, str]]:
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


def review_fix_context_from_mapping(context: dict[str, str]) -> ReviewFixContext:
    return ReviewFixContext(
        repository_id=text_from_mapping(context, PullRequestFields.REPOSITORY_ID),
        branch_name=text_from_mapping(context, Task.branch_name.key),
        session_id=text_from_mapping(context, ImplementationFields.SESSION_ID),
        task_id=text_from_mapping(context, TaskFields.ID),
        task_summary=text_from_mapping(context, TaskFields.SUMMARY),
    )


def review_fix_result(
    comment: ReviewComment,
    review_context: ReviewFixContext,
) -> dict[str, str]:
    return {
        StatusFields.STATUS: StatusFields.UPDATED,
        ReviewCommentFields.PULL_REQUEST_ID: comment.pull_request_id,
        Task.branch_name.key: review_context.branch_name,
        PullRequestFields.REPOSITORY_ID: review_context.repository_id,
    }
