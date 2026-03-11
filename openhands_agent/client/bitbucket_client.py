from typing import Any

from core_lib.client.client_base import ClientBase

from openhands_agent.fields import PullRequestFields


class BitbucketClient(ClientBase):
    def __init__(self, base_url: str, token: str) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(30)

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        workspace: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        response = self._post(
            f'/repositories/{workspace}/{repo_slug}/pullrequests',
            json={
                PullRequestFields.TITLE: title,
                PullRequestFields.DESCRIPTION: description,
                'source': {'branch': {'name': source_branch}},
                'destination': {'branch': {'name': destination_branch}},
            },
        )
        response.raise_for_status()
        return self._normalize_pr(response.json())

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        return {
            PullRequestFields.ID: str(payload[PullRequestFields.ID]),
            PullRequestFields.TITLE: payload.get(PullRequestFields.TITLE, ''),
            PullRequestFields.URL: payload.get('links', {}).get('html', {}).get('href', ''),
        }
