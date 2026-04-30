from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from kato.client.pull_request_client_base import PullRequestClientBase
from kato.helpers.retry_utils import run_with_retry
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields
from kato.helpers.text_utils import dict_from_mapping, list_from_mapping, normalized_text, text_from_attr, text_from_mapping


class GitHubClient(PullRequestClientBase):
    provider_name = 'github'
    _REVIEW_THREADS_QUERY = '''
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 100) {
            nodes {
              databaseId
              body
              author {
                login
              }
            }
          }
        }
      }
    }
  }
}
'''.strip()
    _RESOLVE_REVIEW_THREAD_MUTATION = '''
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    clientMutationId
  }
}
'''.strip()

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
        return self._normalize_comments(
            self._review_thread_nodes(repo_owner, repo_slug, pull_request_id),
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
        params = {'state': 'open', 'per_page': 100}
        normalized_source_branch = normalized_text(source_branch)
        if normalized_source_branch:
            params['head'] = f'{repo_owner}:{normalized_source_branch}'
        response = self._get_with_retry(
            f'/repos/{repo_owner}/{repo_slug}/pulls',
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
            if normalized_source_branch:
                head = dict_from_mapping(item, 'head')
                if normalized_text(head.get('ref', '')) != normalized_source_branch:
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
        thread_id = text_from_attr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID)
        if not thread_id:
            thread_id = self._thread_id_for_comment(
                repo_owner,
                repo_slug,
                normalized_text(comment.pull_request_id),
                normalized_text(comment.comment_id),
            )
        if not thread_id:
            raise ValueError(
                f'unable to determine GitHub review thread for comment {comment.comment_id}'
            )
        self._graphql_with_retry(
            self._RESOLVE_REVIEW_THREAD_MUTATION,
            {'threadId': thread_id},
        )

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        response = self._post_with_retry(
            f'/repos/{repo_owner}/{repo_slug}/pulls/{comment.pull_request_id}/comments/{comment.comment_id}/replies',
            json={'body': normalized_text(body)},
        )
        response.raise_for_status()

    @classmethod
    def _normalize_pr(cls, payload: dict[str, Any]) -> dict[str, str]:
        return cls._normalized_pull_request(
            payload,
            id_key='number',
            url=payload.get('html_url', '') if isinstance(payload, dict) else '',
        )

    @classmethod
    def _normalize_comments(cls, payload: Any, pull_request_id: str) -> list[ReviewComment]:
        if not isinstance(payload, list):
            return []

        comments: list[ReviewComment] = []
        for thread in payload:
            if not isinstance(thread, dict) or thread.get('isResolved'):
                continue
            thread_id = text_from_mapping(thread, 'id')
            comments_payload = dict_from_mapping(thread, 'comments')
            nodes = list_from_mapping(comments_payload, 'nodes')
            for item in nodes:
                if not isinstance(item, dict):
                    continue
                author = dict_from_mapping(item, 'author')
                comment = cls._review_comment_from_values(
                    pull_request_id=pull_request_id,
                    comment_id=item.get('databaseId', ''),
                    author=author.get('login', ''),
                    body=item.get('body', ''),
                    resolution_target_id=thread_id,
                    resolution_target_type='thread',
                )
                comments.append(comment)
        return [comment for comment in comments if comment.comment_id]

    def _review_thread_nodes(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[dict[str, Any]]:
        try:
            pull_request_number = int(normalized_text(pull_request_id))
        except ValueError as exc:
            raise ValueError(f'invalid GitHub pull request id: {pull_request_id}') from exc
        payload = self._graphql_with_retry(
            self._REVIEW_THREADS_QUERY,
            {
                'owner': repo_owner,
                'name': repo_slug,
                'number': pull_request_number,
            },
        )
        repository = payload.get('data', {}).get('repository', {})
        pull_request = dict_from_mapping(repository, 'pullRequest')
        review_threads = dict_from_mapping(pull_request, 'reviewThreads')
        return list_from_mapping(review_threads, 'nodes')

    def _thread_id_for_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
        comment_id: str,
    ) -> str:
        target_comment_id = normalized_text(comment_id)
        for thread in self._review_thread_nodes(repo_owner, repo_slug, pull_request_id):
            if not isinstance(thread, dict):
                continue
            comments_payload = dict_from_mapping(thread, 'comments')
            nodes = list_from_mapping(comments_payload, 'nodes')
            if any(
                text_from_mapping(item, 'databaseId') == target_comment_id
                for item in nodes
                if isinstance(item, dict)
            ):
                return text_from_mapping(thread, 'id')
        return ''

    def _graphql_with_retry(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = run_with_retry(
            lambda: self.session.post(
                self._graphql_url(),
                **self.process_kwargs(
                    json={'query': query, 'variables': variables},
                ),
            ),
            self.max_retries,
            operation_name=f'{self.__class__.__name__} POST {self._graphql_url()}',
        )
        response.raise_for_status()
        payload = response.json() or {}
        if not isinstance(payload, dict):
            raise ValueError('invalid GitHub GraphQL response payload')
        errors = payload.get('errors', [])
        if isinstance(errors, list) and errors:
            messages = [
                text_from_mapping(error, 'message')
                for error in errors
                if isinstance(error, dict)
            ]
            detail = '; '.join(message for message in messages if message)
            raise RuntimeError(detail or 'GitHub GraphQL request failed')
        return payload

    def _graphql_url(self) -> str:
        parsed = urlparse(self.base_url)
        path = parsed.path.rstrip('/')
        if path.endswith('/api/v3'):
            graphql_path = f'{path[:-3]}/graphql'
        elif path.endswith('/api'):
            graphql_path = f'{path}/graphql'
        elif not path:
            graphql_path = '/graphql'
        elif path.endswith('/graphql'):
            graphql_path = path
        else:
            graphql_path = f'{path}/graphql'
        return f'{parsed.scheme}://{parsed.netloc}{graphql_path}'
