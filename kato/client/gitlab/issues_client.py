from typing import Any
from urllib.parse import quote

from kato.client.ticket_client_base import TicketClientBase
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import GitLabCommentFields, GitLabIssueFields


class GitLabIssuesClient(TicketClientBase):
    provider_name = 'gitlab'

    def __init__(self, base_url: str, token: str, project: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._project = quote(str(project).strip(), safe='')
        self.set_headers({'PRIVATE-TOKEN': token})

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/projects/{self._project}/issues',
            params={'assignee_username': assignee, 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        response = self._get_with_retry(
            f'/projects/{self._project}/issues',
            params={
                'assignee_username': assignee,
                'state': 'all',
                'order_by': 'updated_at',
                'sort': 'desc',
                'per_page': 100,
            },
        )
        response.raise_for_status()
        allowed_states = self._normalized_allowed_states(states)
        return self._normalize_issue_tasks(
            self._json_items(response),
            to_task=self._to_task,
            include=lambda issue: self._matches_allowed_state(
                issue.get(GitLabIssueFields.STATE),
                allowed_states,
            ),
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/projects/{self._project}/issues/{issue_id}/notes',
            json={GitLabCommentFields.BODY: comment},
        )
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        normalized_field = str(field_name or '').strip().lower()
        if normalized_field in {'labels', 'label'}:
            response = self._put_with_retry(
                f'/projects/{self._project}/issues/{issue_id}',
                json={'add_labels': state_name},
            )
            response.raise_for_status()
            return
        state_event = 'reopen' if state_name.strip().lower() in {'open', 'opened', 'reopen'} else 'close'
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'state_event': state_event},
        )
        response.raise_for_status()

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = str(payload[GitLabIssueFields.IID])
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        return self._build_task(
            issue_id=issue_id,
            summary=payload.get(GitLabIssueFields.TITLE),
            description=self._build_task_description_with_comments(
                payload.get(GitLabIssueFields.DESCRIPTION),
                comment_entries,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(payload.get(GitLabIssueFields.LABELS)),
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_issue_response_items(
            issue_id,
            item_label='comments',
            path=f'/projects/{self._project}/issues/{issue_id}/notes',
            params={'per_page': 100},
        )

    @classmethod
    def _task_comment_entries(cls, comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        def extract_author(c: dict) -> object:
            author = cls._safe_dict(c, GitLabCommentFields.AUTHOR)
            return author.get(GitLabCommentFields.NAME) or author.get(GitLabCommentFields.USERNAME)

        return cls._build_comment_entries(
            comments,
            extract_body=lambda c: str(c.get(GitLabCommentFields.BODY, '') or '').strip(),
            extract_author=extract_author,
            skip=lambda c: bool(c.get(GitLabCommentFields.SYSTEM)),
        )
