from typing import Any
from urllib.parse import quote

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.fields import PullRequestFields


class GitLabClient(PullRequestClientBase):
    provider_name = 'gitlab'

    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)

    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        response = self._get_with_retry(f'/projects/{self._project_path(repo_owner, repo_slug)}')
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
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests',
            json={
                PullRequestFields.TITLE: title,
                'source_branch': source_branch,
                'target_branch': destination_branch,
                PullRequestFields.DESCRIPTION: description,
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
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests/{pull_request_id}/notes',
            params={'sort': 'asc', 'order_by': 'created_at', 'per_page': 100},
        )
        response.raise_for_status()
        return self._normalize_comments(response.json(), pull_request_id)

    @staticmethod
    def _project_path(repo_owner: str, repo_slug: str) -> str:
        return quote(f'{repo_owner}/{repo_slug}', safe='')

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict) or 'iid' not in payload:
            raise ValueError('invalid pull request response payload')
        return {
            PullRequestFields.ID: str(payload['iid']),
            PullRequestFields.TITLE: str(payload.get(PullRequestFields.TITLE, '')),
            PullRequestFields.URL: str(payload.get('web_url', '')),
        }

    @staticmethod
    def _normalize_comments(payload: Any, pull_request_id: str) -> list[ReviewComment]:
        if not isinstance(payload, list):
            return []

        comments: list[ReviewComment] = []
        for item in payload:
            if not isinstance(item, dict) or item.get('system'):
                continue
            author = item.get('author') if isinstance(item.get('author'), dict) else {}
            comments.append(
                ReviewComment(
                    pull_request_id=str(pull_request_id),
                    comment_id=str(item.get('id', '')),
                    author=str(author.get('username') or author.get('name') or ''),
                    body=str(item.get('body', '')),
                )
            )
        return [comment for comment in comments if comment.comment_id]
