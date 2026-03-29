from typing import Any
from urllib.parse import quote

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.fields import PullRequestFields, ReviewCommentFields


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
        return self._normalize_comments(
            self._discussion_payload(repo_owner, repo_slug, pull_request_id),
            pull_request_id,
        )

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        discussion_id = str(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '') or ''
        ).strip()
        if not discussion_id:
            discussion_id = self._discussion_id_for_comment(
                repo_owner,
                repo_slug,
                str(comment.pull_request_id or ''),
                str(comment.comment_id or ''),
            )
        if not discussion_id:
            raise ValueError(
                f'unable to determine GitLab discussion for comment {comment.comment_id}'
            )
        response = self._put_with_retry(
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests/{comment.pull_request_id}/discussions/{discussion_id}',
            json={'resolved': True},
        )
        response.raise_for_status()

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
        for discussion in payload:
            if not isinstance(discussion, dict) or discussion.get('resolved'):
                continue
            discussion_id = str(discussion.get('id', '') or '').strip()
            notes = discussion.get('notes', []) if isinstance(discussion.get('notes', []), list) else []
            for item in notes:
                if not isinstance(item, dict) or item.get('system'):
                    continue
                author = item.get('author') if isinstance(item.get('author'), dict) else {}
                comment = ReviewComment(
                    pull_request_id=str(pull_request_id),
                    comment_id=str(item.get('id', '')),
                    author=str(author.get('username') or author.get('name') or ''),
                    body=str(item.get('body', '')),
                )
                setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, discussion_id)
                setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'discussion')
                setattr(comment, ReviewCommentFields.RESOLVABLE, bool(discussion_id))
                comments.append(comment)
        return [comment for comment in comments if comment.comment_id]

    def _discussion_payload(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[dict[str, Any]]:
        response = self._get_with_retry(
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests/{pull_request_id}/discussions',
            params={'per_page': 100},
        )
        response.raise_for_status()
        payload = response.json() or []
        return payload if isinstance(payload, list) else []

    def _discussion_id_for_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
        comment_id: str,
    ) -> str:
        target_comment_id = str(comment_id or '').strip()
        for discussion in self._discussion_payload(repo_owner, repo_slug, pull_request_id):
            if not isinstance(discussion, dict):
                continue
            notes = discussion.get('notes', []) if isinstance(discussion.get('notes', []), list) else []
            if any(
                str(note.get('id', '') or '').strip() == target_comment_id
                for note in notes
                if isinstance(note, dict)
            ):
                return str(discussion.get('id', '') or '').strip()
        return ''
