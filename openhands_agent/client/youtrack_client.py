from __future__ import annotations

from typing import Any

from core_lib.client.client_base import ClientBase

from openhands_agent.data_layers.data.task import Task


class YouTrackClient(ClientBase):
    def __init__(self, base_url: str) -> None:
        super().__init__(base_url.rstrip("/"))

    def get_assigned_tasks(self, token: str, project: str, assignee: str, state: str) -> list[Task]:
        query = f"project: {project} assignee: {assignee} State: {{{state}}}"
        response = self._get(
            "/api/issues",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            params={"query": query, "fields": "idReadable,summary,description"},
        )
        response.raise_for_status()
        return [self._to_task(item) for item in response.json()]

    def add_pull_request_comment(self, token: str, issue_id: str, pull_request_url: str) -> None:
        response = self._post(
            f"/api/issues/{issue_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            json={"text": f"Pull request created: {pull_request_url}"},
        )
        response.raise_for_status()

    @staticmethod
    def _to_task(payload: dict[str, Any]) -> Task:
        issue_id = payload["idReadable"]
        return Task(
            id=issue_id,
            summary=payload.get("summary", ""),
            description=payload.get("description") or "",
            branch_name=f"feature/{issue_id.lower()}",
        )
