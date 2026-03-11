from core_lib.client.client_base import ClientBase

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import ImplementationFields


class OpenHandsClient(ClientBase):
    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {api_key}'})
        self.set_timeout(300)

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        response = self._post(
            '/api/sessions',
            json={
                'prompt': (
                    f'Implement task {task.id}: {task.summary}\n\n'
                    f'{task.description}\n\n'
                    f'Work on branch {task.branch_name}.'
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            Task.branch_name.key: task.branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                f'Implement {task.id}',
            ),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        response = self._post(
            '/api/sessions',
            json={
                'prompt': (
                    f'Address pull request comment on branch {branch_name}.\n'
                    f'Comment by {comment.author}: {comment.body}'
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            Task.branch_name.key: branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                'Address review comments',
            ),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }
