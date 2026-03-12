from typing import Any

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.fields import PullRequestFields


class GitHubClient(PullRequestClientBase):
    provider_name = 'github'

    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)

    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        response = self._get_with_retry(f'/repos/{repo_owner}/{repo_slug}')
        response.raise_for_status()

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        response = self._post_with_retry(
            f'/repos/{repo_owner}/{repo_slug}/pulls',
            json={
                PullRequestFields.TITLE: title,
                'head': source_branch,
                'base': destination_branch,
                'body': description,
            },
        )
        response.raise_for_status()
        return self._normalize_pr(response.json())

    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        response = self._get_with_retry(
            f'/repos/{repo_owner}/{repo_slug}/pulls/{pull_request_id}/comments',
            params={'per_page': 100},
        )
        response.raise_for_status()
        return self._normalize_comments(response.json(), pull_request_id)

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict) or 'number' not in payload:
            raise ValueError('invalid pull request response payload')
        return {
            PullRequestFields.ID: str(payload['number']),
            PullRequestFields.TITLE: str(payload.get(PullRequestFields.TITLE, '')),
            PullRequestFields.URL: str(payload.get('html_url', '')),
        }

    @staticmethod
    def _normalize_comments(payload: Any, pull_request_id: str) -> list[ReviewComment]:
        if not isinstance(payload, list):
            return []

        comments: list[ReviewComment] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            user = item.get('user') if isinstance(item.get('user'), dict) else {}
            comments.append(
                ReviewComment(
                    pull_request_id=str(pull_request_id),
                    comment_id=str(item.get('id', '')),
                    author=str(user.get('login', '')),
                    body=str(item.get('body', '')),
                )
            )
        return [comment for comment in comments if comment.comment_id]
