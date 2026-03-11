from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.pull_request_data_access import PullRequestDataAccess
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService


class AgentService:
    def __init__(
        self,
        task_data_access: TaskDataAccess,
        implementation_service: ImplementationService,
        pull_request_data_access: PullRequestDataAccess,
    ) -> None:
        self.task_data_access = task_data_access
        self.implementation_service = implementation_service
        self.pull_request_data_access = pull_request_data_access
        self.pull_request_branch_map: dict[str, str] = {}

    def process_assigned_tasks(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        tasks = self.task_data_access.get_assigned_tasks()

        for task in tasks:
            execution = self.implementation_service.implement_task(task)
            if not execution[ImplementationFields.SUCCESS]:
                continue

            pr = self.pull_request_data_access.create_pull_request(
                title=f'{task.id}: {task.summary}',
                source_branch=str(execution[Task.branch_name.key]),
                description=str(execution[Task.summary.key]),
            )
            self.pull_request_branch_map[pr[PullRequestFields.ID]] = str(execution[Task.branch_name.key])
            self.task_data_access.add_pull_request_comment(task.id, pr[PullRequestFields.URL])
            results.append(pr)

        return results

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        comment = self.implementation_service.review_comment_from_payload(payload)
        branch_name = self.pull_request_branch_map.get(comment.pull_request_id)
        if not branch_name:
            raise ValueError(f'unknown pull request id: {comment.pull_request_id}')

        execution = self.implementation_service.fix_review_comment(comment, branch_name)
        if not execution[ImplementationFields.SUCCESS]:
            raise RuntimeError(f'failed to address comment {comment.comment_id}')

        return {
            StatusFields.STATUS: StatusFields.UPDATED,
            ReviewComment.pull_request_id.key: comment.pull_request_id,
            Task.branch_name.key: branch_name,
        }
