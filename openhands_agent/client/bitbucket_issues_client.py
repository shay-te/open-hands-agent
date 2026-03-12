from typing import Any

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import BitbucketIssueCommentFields, BitbucketIssueFields


class BitbucketIssuesClient(TicketClientBase):
    provider_name = 'bitbucket'

    def __init__(self, base_url: str, token: str, workspace: str, repo_slug: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._workspace = str(workspace).strip()
        self._repo_slug = str(repo_slug).strip()

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues',
            params={'pagelen': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        response = self._get_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues',
            params={'pagelen': 100},
        )
        response.raise_for_status()
        payload = response.json() or {}
        values = payload.get('values', []) if isinstance(payload, dict) else []
        allowed_states = {str(state).strip().lower() for state in states}
        normalized_assignee = str(assignee or '').strip().lower()
        tasks: list[Task] = []
        for issue in values if isinstance(values, list) else []:
            if not isinstance(issue, dict):
                continue
            if normalized_assignee and not self._matches_assignee(issue.get(BitbucketIssueFields.ASSIGNEE), normalized_assignee):
                continue
            issue_state = str(issue.get(BitbucketIssueFields.STATE, '') or '').strip().lower()
            if allowed_states and issue_state not in allowed_states:
                continue
            try:
                tasks.append(self._to_task(issue))
            except (KeyError, TypeError, ValueError):
                self.logger.exception('failed to normalize bitbucket issue payload')
        return tasks

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}/comments',
            json={BitbucketIssueCommentFields.CONTENT: {BitbucketIssueCommentFields.RAW: comment}},
        )
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        response = self._put_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}',
            json={str(field_name or BitbucketIssueFields.STATE): state_name},
        )
        response.raise_for_status()

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = str(payload[BitbucketIssueFields.ID])
        comments = self._issue_comments(issue_id)
        content = payload.get(BitbucketIssueFields.CONTENT, {})
        if not isinstance(content, dict):
            content = {}
        return Task(
            id=issue_id,
            summary=str(payload.get(BitbucketIssueFields.TITLE, '') or ''),
            description=self._build_task_description(content.get(BitbucketIssueFields.RAW), comments),
            branch_name=f'feature/{issue_id.lower()}',
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        try:
            response = self._get_with_retry(
                f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}/comments',
                params={'pagelen': 100},
            )
            response.raise_for_status()
            payload = response.json() or {}
            values = payload.get('values', []) if isinstance(payload, dict) else []
            return list(values) if isinstance(values, list) else []
        except Exception:
            self.logger.exception('failed to fetch comments for bitbucket issue %s', issue_id)
            return []

    def _build_task_description(self, description: object, comments: list[dict[str, Any]]) -> str:
        sections = [str(description or '').strip() or 'No description provided.']
        comment_lines = self._format_comments(comments)
        if comment_lines:
            sections.append('Issue comments:\n' + '\n'.join(comment_lines))
        return '\n\n'.join(section for section in sections if section)

    @staticmethod
    def _format_comments(comments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            content = comment.get(BitbucketIssueCommentFields.CONTENT, {})
            if not isinstance(content, dict):
                content = {}
            body = str(content.get(BitbucketIssueCommentFields.RAW, '') or '').strip()
            if not body:
                continue
            user = comment.get(BitbucketIssueCommentFields.USER, {})
            if not isinstance(user, dict):
                user = {}
            author = str(
                user.get(BitbucketIssueCommentFields.DISPLAY_NAME)
                or user.get(BitbucketIssueCommentFields.NICKNAME)
                or 'unknown'
            ).strip()
            lines.append(f'- {author}: {body}')
        return lines

    @staticmethod
    def _matches_assignee(assignee: Any, expected: str) -> bool:
        if not isinstance(assignee, dict):
            return False
        candidates = {
            str(assignee.get(BitbucketIssueFields.DISPLAY_NAME, '') or '').strip().lower(),
            str(assignee.get(BitbucketIssueFields.NICKNAME, '') or '').strip().lower(),
        }
        return expected in candidates
