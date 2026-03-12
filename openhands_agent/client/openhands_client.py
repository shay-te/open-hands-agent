from openhands_agent.client.retrying_client_base import RetryingClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)


class OpenHandsClient(RetryingClientBase):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url, api_key, timeout=300, max_retries=max_retries)

    def validate_connection(self) -> None:
        response = self._get_with_retry('/api/sessions')
        response.raise_for_status()

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        self.logger.info('requesting implementation for task %s', task.id)
        response = self._post_with_retry(
            '/api/sessions',
            json={'prompt': self._build_implementation_prompt(task)},
        )
        response.raise_for_status()
        payload = self._normalized_payload(response)
        result = {
            Task.branch_name.key: task.branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                f'Implement {task.id}',
            ),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }
        self.logger.info(
            'implementation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def test_task(self, task: Task) -> dict[str, str | bool]:
        self.logger.info('requesting testing validation for task %s', task.id)
        response = self._post_with_retry(
            '/api/sessions',
            json={'prompt': self._build_testing_prompt(task)},
        )
        response.raise_for_status()
        payload = self._normalized_payload(response)
        result = {
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }
        self.logger.info(
            'testing validation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        self.logger.info(
            'requesting review fix for pull request %s comment %s',
            comment.pull_request_id,
            comment.comment_id,
        )
        response = self._post_with_retry(
            '/api/sessions',
            json={'prompt': self._build_review_prompt(comment, branch_name)},
        )
        response.raise_for_status()
        payload = self._normalized_payload(response)
        result = {
            Task.branch_name.key: branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                'Address review comments',
            ),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }
        self.logger.info(
            'review fix finished for pull request %s comment %s with success=%s',
            comment.pull_request_id,
            comment.comment_id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def _build_implementation_prompt(self, task: Task) -> str:
        repository_scope = self._repository_scope_text(task)
        return (
            f'Implement task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}'
        )

    def _build_testing_prompt(self, task: Task) -> str:
        repository_scope = self._repository_scope_text(task)
        return (
            f'Validate the implementation for task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            'Do not create a pull request. Return success=false if the changes are not ready.'
        )

    @staticmethod
    def _repository_scope_text(task: Task) -> str:
        repository_branches = getattr(task, 'repository_branches', {}) or {}
        repositories = getattr(task, 'repositories', []) or []
        if not repositories:
            return f'Work on branch {task.branch_name}.'

        repository_lines = []
        for repository in repositories:
            branch_name = repository_branches.get(repository.id, task.branch_name)
            destination_branch = str(getattr(repository, 'destination_branch', '') or '').strip()
            destination_text = (
                destination_branch if destination_branch else 'the repository default branch'
            )
            repository_lines.append(
                f'- {repository.id} at {repository.local_path}: '
                f'use branch {branch_name} and open the pull request into {destination_text}.'
            )
        lines = '\n'.join(repository_lines)
        return f'Only modify these repositories:\n{lines}'

    @staticmethod
    def _build_review_prompt(comment: ReviewComment, branch_name: str) -> str:
        repository_id = getattr(comment, PullRequestFields.REPOSITORY_ID, '')
        repository_context = f' in repository {repository_id}' if repository_id else ''
        review_context = OpenHandsClient._review_comment_context_text(comment)
        return (
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'Comment by {comment.author}: {comment.body}'
            f'{review_context}'
        )

    @staticmethod
    def _normalized_payload(response) -> dict:
        payload = response.json() or {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _review_comment_context_text(comment: ReviewComment) -> str:
        all_comments = getattr(comment, ReviewCommentFields.ALL_COMMENTS, [])
        if not isinstance(all_comments, list) or len(all_comments) <= 1:
            return ''

        lines: list[str] = []
        for item in all_comments:
            if not isinstance(item, dict):
                continue
            author = str(item.get(ReviewCommentFields.AUTHOR, '') or '').strip()
            body = str(item.get(ReviewCommentFields.BODY, '') or '').strip()
            if not body:
                continue
            label = author if author else 'reviewer'
            lines.append(f'- {label}: {body}')
        if not lines:
            return ''
        return '\n\nReview comment context:\n' + '\n'.join(lines)
