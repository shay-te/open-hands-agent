from openhands_agent.client.retrying_client_base import RetryingClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import ImplementationFields, PullRequestFields


class OpenHandsClient(RetryingClientBase):
    DEFAULT_PRE_PULL_REQUEST_COMMANDS = [
        'Write tests that challenge the new code as much as possible.',
        'Make sure the tests are green. If not, fix them before creating the pull request.',
    ]

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = 3,
        pre_pull_request_commands: list[str] | None = None,
    ) -> None:
        super().__init__(base_url, api_key, timeout=300, max_retries=max_retries)
        self._pre_pull_request_commands = list(
            pre_pull_request_commands or self.DEFAULT_PRE_PULL_REQUEST_COMMANDS
        )

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
        prompt = (
            f'Implement task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}'
        )
        if not self._pre_pull_request_commands:
            return prompt

        commands = '\n'.join(f'- {command}' for command in self._pre_pull_request_commands)
        return f'{prompt}\n\nBefore creating the pull request:\n{commands}'

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
        return (
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'Comment by {comment.author}: {comment.body}'
        )

    @staticmethod
    def _normalized_payload(response) -> dict:
        payload = response.json() or {}
        return payload if isinstance(payload, dict) else {}
