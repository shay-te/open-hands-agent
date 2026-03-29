from typing import Any
from urllib.parse import urlparse

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.client.retry_utils import run_with_retry
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.fields import PullRequestFields, ReviewCommentFields


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

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        thread_id = str(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '') or ''
        ).strip()
        if not thread_id:
            thread_id = self._thread_id_for_comment(
                repo_owner,
                repo_slug,
                str(comment.pull_request_id or ''),
                str(comment.comment_id or ''),
            )
        if not thread_id:
            raise ValueError(
                f'unable to determine GitHub review thread for comment {comment.comment_id}'
            )
        self._graphql_with_retry(
            self._RESOLVE_REVIEW_THREAD_MUTATION,
            {'threadId': thread_id},
        )

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
        for thread in payload:
            if not isinstance(thread, dict) or thread.get('isResolved'):
                continue
            thread_id = str(thread.get('id', '') or '').strip()
            comments_payload = thread.get('comments') if isinstance(thread.get('comments'), dict) else {}
            nodes = comments_payload.get('nodes', []) if isinstance(comments_payload.get('nodes', []), list) else []
            for item in nodes:
                if not isinstance(item, dict):
                    continue
                author = item.get('author') if isinstance(item.get('author'), dict) else {}
                comment = ReviewComment(
                    pull_request_id=str(pull_request_id),
                    comment_id=str(item.get('databaseId', '')),
                    author=str(author.get('login', '')),
                    body=str(item.get('body', '')),
                )
                setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, thread_id)
                setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
                setattr(comment, ReviewCommentFields.RESOLVABLE, bool(thread_id))
                comments.append(comment)
        return [comment for comment in comments if comment.comment_id]

    def _review_thread_nodes(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[dict[str, Any]]:
        try:
            pull_request_number = int(str(pull_request_id or '').strip())
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
        pull_request = repository.get('pullRequest') if isinstance(repository, dict) else {}
        review_threads = pull_request.get('reviewThreads') if isinstance(pull_request, dict) else {}
        nodes = review_threads.get('nodes', []) if isinstance(review_threads, dict) else []
        return nodes if isinstance(nodes, list) else []

    def _thread_id_for_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
        comment_id: str,
    ) -> str:
        target_comment_id = str(comment_id or '').strip()
        for thread in self._review_thread_nodes(repo_owner, repo_slug, pull_request_id):
            if not isinstance(thread, dict):
                continue
            comments_payload = thread.get('comments') if isinstance(thread.get('comments'), dict) else {}
            nodes = comments_payload.get('nodes', []) if isinstance(comments_payload.get('nodes', []), list) else []
            if any(
                str(item.get('databaseId', '') or '').strip() == target_comment_id
                for item in nodes
                if isinstance(item, dict)
            ):
                return str(thread.get('id', '') or '').strip()
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
        )
        response.raise_for_status()
        payload = response.json() or {}
        if not isinstance(payload, dict):
            raise ValueError('invalid GitHub GraphQL response payload')
        errors = payload.get('errors', [])
        if isinstance(errors, list) and errors:
            messages = [
                str(error.get('message', '') or '').strip()
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
