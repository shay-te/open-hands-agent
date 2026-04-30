from __future__ import annotations

from typing import Any

from kato.client.bitbucket.auth import bitbucket_basic_auth_header
from kato.client.pull_request_client_base import PullRequestClientBase
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields
from kato.helpers.text_utils import dict_from_mapping, list_from_mapping, normalized_text, text_from_attr

# Bitbucket rejected pagelen values around 100 in live API checks ("Invalid pagelen"),
# so keep PR and PR-comment pagination at a smaller safe value.
BITBUCKET_PAGE_LENGTH = 50


class BitbucketClient(PullRequestClientBase):
    provider_name = 'bitbucket'

    def __init__(
        self,
        base_url: str,
        token: str,
        max_retries: int = 3,
        *,
        username: str = '',
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        auth_username = normalized_text(username)
        if auth_username:
            self.set_headers({'Authorization': bitbucket_basic_auth_header(auth_username, token)})

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
            params={'pagelen': BITBUCKET_PAGE_LENGTH, 'sort': 'created_on'},
        )
        response.raise_for_status()
        return self._normalize_comments(response.json(), pull_request_id)

    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        response = self._get_with_retry(
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests',
            params={'pagelen': BITBUCKET_PAGE_LENGTH},
        )
        response.raise_for_status()
        payload = response.json() or {}
        values = list_from_mapping(payload, 'values')
        normalized_source_branch = normalized_text(source_branch)
        normalized_title_prefix = normalized_text(title_prefix)
        matches: list[dict[str, str]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            source = dict_from_mapping(item, 'source')
            branch = dict_from_mapping(source, 'branch')
            item_source_branch = normalized_text(branch.get('name', ''))
            if normalized_source_branch and item_source_branch != normalized_source_branch:
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
        resolution_target_id = text_from_attr(
            comment,
            ReviewCommentFields.RESOLUTION_TARGET_ID,
        ) or normalized_text(comment.comment_id)
        if not resolution_target_id:
            raise ValueError('bitbucket review comment id is required to resolve the thread')
        response = self._post_with_retry(
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests/{comment.pull_request_id}/comments/{resolution_target_id}/resolve',
        )
        response.raise_for_status()

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        parent_id_text = text_from_attr(
            comment,
            ReviewCommentFields.RESOLUTION_TARGET_ID,
        ) or normalized_text(comment.comment_id)
        if not parent_id_text:
            raise ValueError('bitbucket review comment id is required to post a reply')
        try:
            parent_id = int(parent_id_text)
        except ValueError as exc:
            raise ValueError(
                f'invalid bitbucket review comment id for reply: {parent_id_text}'
            ) from exc
        request_body = {
            'content': {'raw': normalized_text(body)},
            'parent': {'id': parent_id},
        }
        response = self._post_with_retry(
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests/{comment.pull_request_id}/comments',
            json=request_body,
        )
        if not response.ok:
            # Bubble Bitbucket's actual response body up — the bare
            # `requests.HTTPError` only carries the status code, which
            # makes 400s impossible to debug. Common causes here:
            # parent comment is itself a nested reply (Bitbucket forbids
            # >1 level of nesting), parent comment was deleted, or the
            # body is empty / too long.
            detail = ''
            try:
                detail = response.text[:1000]
            except Exception:
                pass
            raise RuntimeError(
                f'bitbucket rejected reply to PR {comment.pull_request_id} '
                f'comment {parent_id}: HTTP {response.status_code} — {detail}'
            )

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

    @classmethod
    def _normalize_pr(cls, payload: dict[str, Any]) -> dict[str, str]:
        links = dict_from_mapping(payload, 'links')
        html_link = dict_from_mapping(links, 'html')
        return cls._normalized_pull_request(
            payload,
            id_key=PullRequestFields.ID,
            url=html_link.get('href', ''),
        )

    @classmethod
    def _normalize_comments(cls, payload: dict[str, Any], pull_request_id: str) -> list[ReviewComment]:
        values = list_from_mapping(payload, 'values')
        comments: list[ReviewComment] = []
        for item in values:
            if not isinstance(item, dict) or item.get('deleted'):
                continue
            parent = dict_from_mapping(item, 'parent')
            if item.get('resolution') or parent.get('resolution'):
                continue
            content = dict_from_mapping(item, 'content')
            author = dict_from_mapping(item, 'user')
            display_name = author.get('display_name', '')
            nickname = author.get('nickname', '')
            resolution_target_id = normalized_text(
                parent.get('id', '') or item.get('id', '')
            )
            comment = cls._review_comment_from_values(
                pull_request_id=pull_request_id,
                comment_id=item.get('id', ''),
                author=display_name or nickname or '',
                body=content.get('raw', ''),
                resolution_target_id=resolution_target_id,
                resolution_target_type='comment',
            )
            comments.append(comment)
        return [comment for comment in comments if comment.comment_id]
