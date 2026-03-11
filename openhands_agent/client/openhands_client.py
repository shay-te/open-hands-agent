from __future__ import annotations

from core_lib.client.client_base import ClientBase

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task


class OpenHandsClient(ClientBase):
    def __init__(self, base_url: str) -> None:
        super().__init__(base_url.rstrip("/"))

    def implement_task(self, api_key: str, task: Task) -> dict[str, str | bool]:
        response = self._post(
            "/api/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
            json={
                "prompt": (
                    f"Implement task {task.id}: {task.summary}\n\n"
                    f"{task.description}\n\n"
                    f"Work on branch {task.branch_name}."
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "branch_name": task.branch_name,
            "summary": payload.get("summary", ""),
            "commit_message": payload.get("commit_message", f"Implement {task.id}"),
            "success": bool(payload.get("success", True)),
        }

    def fix_review_comment(self, api_key: str, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        response = self._post(
            "/api/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
            json={
                "prompt": (
                    f"Address pull request comment on branch {branch_name}.\n"
                    f"Comment by {comment.author}: {comment.body}"
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "branch_name": branch_name,
            "summary": payload.get("summary", ""),
            "commit_message": payload.get("commit_message", "Address review comments"),
            "success": bool(payload.get("success", True)),
        }
