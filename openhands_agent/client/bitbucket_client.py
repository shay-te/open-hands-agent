from typing import Any

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.fields import PullRequestFields, ReviewCommentFields


class BitbucketClient(PullRequestClientBase):
    provider_name = 'bitbucket'

    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)

    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        response = self._get_with_retry(f'/repositories/{repo_owner}/{repo_slug}')
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
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests',
            json=self._pull_request_payload(
                title=title,
                source_branch=source_branch,
                destination_branch=destination_branch,
                description=description,
            ),
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
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests/{pull_request_id}/comments',
            params={'pagelen': 100, 'sort': 'created_on'},
        )
        response.raise_for_status()
        return self._normalize_comments(response.json(), pull_request_id)

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        resolution_target_id = str(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '') or comment.comment_id or ''
        ).strip()
        if not resolution_target_id:
            raise ValueError('bitbucket review comment id is required to resolve the thread')
        response = self._post_with_retry(
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests/{comment.pull_request_id}/comments/{resolution_target_id}/resolve',
        )
        response.raise_for_status()

    @staticmethod
    def _pull_request_payload(
        title: str,
        source_branch: str,
        destination_branch: str | None,
        description: str,
    ) -> dict[str, Any]:
        payload = {
            PullRequestFields.TITLE: title,
            PullRequestFields.DESCRIPTION: description,
            'source': {'branch': {'name': source_branch}},
        }
        if destination_branch:
            payload['destination'] = {'branch': {'name': destination_branch}}
        return payload

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict) or PullRequestFields.ID not in payload:
            raise ValueError('invalid pull request response payload')
        links = payload.get('links')
        if not isinstance(links, dict):
            links = {}
        html_link = links.get('html')
        if not isinstance(html_link, dict):
            html_link = {}
        return {
            PullRequestFields.ID: str(payload[PullRequestFields.ID]),
            PullRequestFields.TITLE: str(payload.get(PullRequestFields.TITLE, '')),
            PullRequestFields.URL: str(html_link.get('href', '')),
        }

    @staticmethod
    def _normalize_comments(payload: dict[str, Any], pull_request_id: str) -> list[ReviewComment]:
        values = payload.get('values', []) if isinstance(payload, dict) else []
        if not isinstance(values, list):
            return []

        comments: list[ReviewComment] = []
        for item in values:
            if not isinstance(item, dict) or item.get('deleted'):
                continue
            parent = item.get('parent') if isinstance(item.get('parent'), dict) else {}
            if item.get('resolution') or parent.get('resolution'):
                continue
            content = item.get('content') if isinstance(item.get('content'), dict) else {}
            author = item.get('user') if isinstance(item.get('user'), dict) else {}
            display_name = author.get('display_name', '')
            nickname = author.get('nickname', '')
            comment = ReviewComment(
                pull_request_id=str(pull_request_id),
                comment_id=str(item.get('id', '')),
                author=str(display_name or nickname or ''),
                body=str(content.get('raw', '')),
            )
            resolution_target_id = str(parent.get('id', '') or item.get('id', '') or '').strip()
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, resolution_target_id)
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'comment')
            setattr(comment, ReviewCommentFields.RESOLVABLE, bool(resolution_target_id))
            comments.append(comment)
        return [comment for comment in comments if comment.comment_id]
