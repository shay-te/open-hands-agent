from typing import Any
from urllib.parse import quote

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import GitLabCommentFields, GitLabIssueFields, TaskCommentFields


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
        issues = self._json_list(response)
        allowed_states = {str(state).strip().lower() for state in states}
        tasks: list[Task] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue_state = str(issue.get(GitLabIssueFields.STATE, '') or '').strip().lower()
            if allowed_states and issue_state not in allowed_states:
                continue
            try:
                tasks.append(self._to_task(issue))
            except (KeyError, TypeError, ValueError):
                self.logger.exception('failed to normalize gitlab issue payload')
        return tasks

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
        task = Task(
            id=issue_id,
            summary=str(payload.get(GitLabIssueFields.TITLE, '') or ''),
            description=self._build_task_description_with_comments(
                payload.get(GitLabIssueFields.DESCRIPTION),
                comment_entries,
            ),
            branch_name=f'feature/{issue_id.lower()}',
        )
        self._set_task_comments(task, comment_entries)
        return task

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        try:
            response = self._get_with_retry(
                f'/projects/{self._project}/issues/{issue_id}/notes',
                params={'per_page': 100},
            )
            response.raise_for_status()
            return self._json_list(response)
        except Exception:
            self.logger.exception('failed to fetch comments for gitlab issue %s', issue_id)
            return []

    @staticmethod
    def _task_comment_entries(comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for comment in comments:
            if not isinstance(comment, dict) or comment.get(GitLabCommentFields.SYSTEM):
                continue
            body = str(comment.get(GitLabCommentFields.BODY, '') or '').strip()
            if not body:
                continue
            author = comment.get(GitLabCommentFields.AUTHOR, {})
            if not isinstance(author, dict):
                author = {}
            entries.append(
                {
                    TaskCommentFields.AUTHOR: str(
                        author.get(GitLabCommentFields.NAME)
                        or author.get(GitLabCommentFields.USERNAME)
                        or 'unknown'
                    ).strip(),
                    TaskCommentFields.BODY: body,
                }
            )
        return entries

    @staticmethod
    def _json_list(response) -> list[dict[str, Any]]:
        payload = response.json() or []
        return list(payload) if isinstance(payload, list) else []
