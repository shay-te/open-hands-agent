from typing import Any

from kato.client.ticket_client_base import TicketClientBase
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import GitHubCommentFields, GitHubIssueFields


class GitHubIssuesClient(TicketClientBase):
    provider_name = 'github'

    def __init__(self, base_url: str, token: str, owner: str, repo: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._owner = str(owner).strip()
        self._repo = str(repo).strip()
        self.set_headers(
            {
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
            }
        )

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues',
            params={'assignee': assignee, 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        response = self._get_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues',
            params={
                'assignee': assignee,
                'state': 'all',
                'sort': 'updated',
                'direction': 'desc',
                'per_page': 100,
            },
        )
        response.raise_for_status()
        allowed_states = self._normalized_allowed_states(states)
        return self._normalize_issue_tasks(
            self._json_items(response),
            to_task=self._to_task,
            include=lambda issue: (
                not issue.get(GitHubIssueFields.PULL_REQUEST)
                and self._matches_allowed_state(
                    issue.get(GitHubIssueFields.STATE),
                    allowed_states,
                )
            ),
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/comments',
            json={GitHubCommentFields.BODY: comment},
        )
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        normalized_field = str(field_name or '').strip().lower()
        if normalized_field in {'labels', 'label'}:
            response = self._post_with_retry(
                f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/labels',
                json={GitHubIssueFields.LABELS: [state_name]},
            )
            response.raise_for_status()
            return
        response = self._patch_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues/{issue_id}',
            json={normalized_field or GitHubIssueFields.STATE: state_name.lower()},
        )
        response.raise_for_status()

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = str(payload[GitHubIssueFields.NUMBER])
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        return self._build_task(
            issue_id=issue_id,
            summary=payload.get(GitHubIssueFields.TITLE),
            description=self._build_task_description_with_comments(
                payload.get(GitHubIssueFields.BODY),
                comment_entries,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(payload.get(GitHubIssueFields.LABELS)),
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_issue_response_items(
            issue_id,
            item_label='comments',
            path=f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/comments',
            params={'per_page': 100},
        )

    @classmethod
    def _task_comment_entries(cls, comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        return cls._build_comment_entries(
            comments,
            extract_body=lambda c: str(c.get(GitHubCommentFields.BODY, '') or '').strip(),
            extract_author=lambda c: cls._safe_dict(c, GitHubCommentFields.USER).get(GitHubCommentFields.LOGIN),
        )
