from __future__ import annotations

from typing import Any
from urllib.parse import quote

from kato.client.pull_request_client_base import PullRequestClientBase
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields
from kato.helpers.text_utils import dict_from_mapping, list_from_mapping, normalized_text, text_from_attr, text_from_mapping


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

    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        params = {'state': 'opened', 'per_page': 100}
        normalized_source_branch = normalized_text(source_branch)
        if normalized_source_branch:
            params['source_branch'] = normalized_source_branch
        response = self._get_with_retry(
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests',
            params=params,
        )
        response.raise_for_status()
        payload = response.json() or []
        if not isinstance(payload, list):
            return []
        normalized_title_prefix = normalized_text(title_prefix)
        matches: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if normalized_source_branch and normalized_text(item.get('source_branch', '')) != normalized_source_branch:
                continue
            item_title = normalized_text(item.get(PullRequestFields.TITLE, ''))
            if normalized_title_prefix and not item_title.startswith(normalized_title_prefix):
                continue
            matches.append(self._normalize_pr(item))
        return matches

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        discussion_id = self._require_discussion_id(repo_owner, repo_slug, comment)
        response = self._put_with_retry(
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests/{comment.pull_request_id}/discussions/{discussion_id}',
            json={'resolved': True},
        )
        response.raise_for_status()

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        discussion_id = self._require_discussion_id(repo_owner, repo_slug, comment)
        response = self._post_with_retry(
            f'/projects/{self._project_path(repo_owner, repo_slug)}/merge_requests/{comment.pull_request_id}/discussions/{discussion_id}/notes',
            json={'body': normalized_text(body)},
        )
        response.raise_for_status()

    def _require_discussion_id(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> str:
        discussion_id = text_from_attr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID)
        if not discussion_id:
            discussion_id = self._discussion_id_for_comment(
                repo_owner,
                repo_slug,
                normalized_text(comment.pull_request_id),
                normalized_text(comment.comment_id),
            )
        if not discussion_id:
            raise ValueError(
                f'unable to determine GitLab discussion for comment {comment.comment_id}'
            )
        return discussion_id

    @staticmethod
    def _project_path(repo_owner: str, repo_slug: str) -> str:
        return quote(f'{repo_owner}/{repo_slug}', safe='')

    @classmethod
    def _normalize_pr(cls, payload: dict[str, Any]) -> dict[str, str]:
        return cls._normalized_pull_request(
            payload,
            id_key='iid',
            url=payload.get('web_url', '') if isinstance(payload, dict) else '',
        )

    @classmethod
    def _normalize_comments(cls, payload: Any, pull_request_id: str) -> list[ReviewComment]:
        if not isinstance(payload, list):
            return []

        comments: list[ReviewComment] = []
        for discussion in payload:
            if not isinstance(discussion, dict) or discussion.get('resolved'):
                continue
            discussion_id = text_from_mapping(discussion, 'id')
            notes = list_from_mapping(discussion, 'notes')
            for item in notes:
                if not isinstance(item, dict) or item.get('system'):
                    continue
                author = dict_from_mapping(item, 'author')
                comment = cls._review_comment_from_values(
                    pull_request_id=pull_request_id,
                    comment_id=item.get('id', ''),
                    author=author.get('username') or author.get('name') or '',
                    body=item.get('body', ''),
                    resolution_target_id=discussion_id,
                    resolution_target_type='discussion',
                )
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
        target_comment_id = normalized_text(comment_id)
        for discussion in self._discussion_payload(repo_owner, repo_slug, pull_request_id):
            if not isinstance(discussion, dict):
                continue
            notes = list_from_mapping(discussion, 'notes')
            if any(
                text_from_mapping(note, 'id') == target_comment_id
                for note in notes
                if isinstance(note, dict)
            ):
                return text_from_mapping(discussion, 'id')
        return ''
