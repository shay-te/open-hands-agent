from __future__ import annotations

from typing import Any

from core_lib.client.client_base import ClientBase


class BitbucketClient(ClientBase):
    def __init__(self, base_url: str) -> None:
        super().__init__(base_url.rstrip("/"))

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        token: str,
        workspace: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = "",
    ) -> dict[str, str]:
        response = self._post(
            f"/repositories/{workspace}/{repo_slug}/pullrequests",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            json={
                "title": title,
                "description": description,
                "source": {"branch": {"name": source_branch}},
                "destination": {"branch": {"name": destination_branch}},
            },
        )
        response.raise_for_status()
        return self._normalize_pr(response.json())

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        return {
            "id": str(payload["id"]),
            "title": payload.get("title", ""),
            "url": payload.get("links", {}).get("html", {}).get("href", ""),
        }
