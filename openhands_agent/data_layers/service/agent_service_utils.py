from dataclasses import dataclass

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from openhands_agent.text_utils import (
    normalized_text,
    text_from_attr,
    text_from_mapping,
)


@dataclass
class PreparedTaskContext:
    repositories: list[object]
    repository_branches: dict[str, str]


@dataclass(frozen=True)
class ReviewFixContext:
    repository_id: str
    branch_name: str
    session_id: str
    task_id: str
    task_summary: str


def task_has_actionable_definition(task: Task) -> bool:
    description = normalized_text(task.description)
    if description and description.lower() != 'no description provided.':
        return True
    summary = normalized_text(task.summary)
    return len(summary) >= 24 or len(summary.split()) >= 4


def repository_ids_text(repositories: list[object]) -> str:
    repository_ids = [
        text_from_attr(repository, 'id')
        for repository in repositories
        if text_from_attr(repository, 'id')
    ]
    return ', '.join(repository_ids) if repository_ids else '<none>'


def repository_destination_text(repositories: list[object]) -> str:
    entries = []
    for repository in repositories:
        repository_id = text_from_attr(repository, 'id')
        destination_branch = text_from_attr(repository, 'destination_branch')
        if not repository_id:
            continue
        entries.append(f'{repository_id}->{destination_branch or "default"}')
    return ', '.join(entries) if entries else '<none>'


def repository_branch_text(repository_branches: dict[str, str]) -> str:
    if not repository_branches:
        return '<none>'
    return ', '.join(
        f'{repository_id}->{branch_name}'
        for repository_id, branch_name in repository_branches.items()
    )


def pull_request_repositories_text(pull_requests) -> str:
    if not isinstance(pull_requests, list):
        return '<none>'
    repository_ids = [
        text_from_mapping(pull_request, PullRequestFields.REPOSITORY_ID)
        for pull_request in pull_requests
        if isinstance(pull_request, dict)
    ]
    repository_ids = [repository_id for repository_id in repository_ids if repository_id]
    return ', '.join(repository_ids) if repository_ids else '<none>'


def session_suffix(payload: dict[str, str | bool]) -> str:
    session_id = text_from_mapping(payload, ImplementationFields.SESSION_ID)
    return f' (session {session_id})' if session_id else ''


def task_started_comment(task: Task) -> str:
    repositories = getattr(task, 'repositories', []) or []
    repository_ids = [
        str(repository.id).strip()
        for repository in repositories
        if str(repository.id).strip()
    ]
    if not repository_ids:
        return 'OpenHands agent started working on this task.'
    if len(repository_ids) == 1:
        return (
            'OpenHands agent started working on this task in repository '
            f'{repository_ids[0]}.'
        )
    return (
        'OpenHands agent started working on this task in repositories: '
        f'{", ".join(repository_ids)}.'
    )


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


def pull_request_summary_comment(
    task: Task,
    pull_requests: list[dict[str, str]],
    failed_repositories: list[str],
    validation_report: str = '',
) -> str:
    lines = [f'OpenHands completed task {task.id}: {task.summary}.']
    if validation_report:
        lines.append('')
        lines.append('Validation report:')
        lines.append(validation_report)
    if pull_requests:
        lines.append('')
        lines.append('Published review links:')
        for pull_request in pull_requests:
            lines.append(
                f'- {pull_request[PullRequestFields.REPOSITORY_ID]}: '
                f'{pull_request[PullRequestFields.URL]}'
            )
    if failed_repositories:
        lines.append('')
        lines.append('Failed repositories: ' + ', '.join(failed_repositories))
    return '\n'.join(lines)


def review_comment_fixed_comment(comment: ReviewComment) -> str:
    return (
        'OpenHands addressed review comment '
        f'{comment.comment_id} on pull request {comment.pull_request_id}.'
    )


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
