from __future__ import annotations

from pydantic import ValidationError

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data_access.implementation_data_access import ImplementationDataAccess
from openhands_agent.data_layers.data_access.pull_request_data_access import PullRequestDataAccess
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess


class AgentService:
    def __init__(
        self,
        task_data_access: TaskDataAccess,
        implementation_data_access: ImplementationDataAccess,
        pull_request_data_access: PullRequestDataAccess,
    ) -> None:
        self.task_data_access = task_data_access
        self.implementation_data_access = implementation_data_access
        self.pull_request_data_access = pull_request_data_access
        self.pull_request_branch_map: dict[str, str] = {}

    def process_assigned_tasks(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        tasks = self.task_data_access.get_assigned_tasks()

        for task in tasks:
            execution = self.implementation_data_access.implement_task(task)
            if not execution["success"]:
                continue

            pr = self.pull_request_data_access.create_pull_request(
                title=f"{task.id}: {task.summary}",
                source_branch=str(execution["branch_name"]),
                description=str(execution["summary"]),
            )
            self.pull_request_branch_map[pr["id"]] = str(execution["branch_name"])
            self.task_data_access.add_pull_request_comment(task.id, pr["url"])
            results.append(pr)

        return results

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        try:
            comment = ReviewComment.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"invalid review comment payload: {exc}") from exc

        branch_name = self.pull_request_branch_map.get(comment.pull_request_id)
        if not branch_name:
            raise ValueError(f"unknown pull request id: {comment.pull_request_id}")

        execution = self.implementation_data_access.fix_review_comment(comment, branch_name)
        if not execution["success"]:
            raise RuntimeError(f"failed to address comment {comment.comment_id}")

        return {
            "status": "updated",
            "pull_request_id": comment.pull_request_id,
            "branch_name": branch_name,
        }
