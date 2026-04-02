from typing import Any

from openhands_agent.client.bitbucket_auth import bitbucket_basic_auth_header
from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    BitbucketIssueCommentFields,
    BitbucketIssueFields,
)
from openhands_agent.text_utils import normalized_text


class BitbucketIssuesClient(TicketClientBase):
    provider_name = 'bitbucket'

    def __init__(
        self,
        base_url: str,
        token: str,
        workspace: str,
        repo_slug: str,
        max_retries: int = 3,
        *,
        username: str = '',
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._workspace = str(workspace).strip()
        self._repo_slug = str(repo_slug).strip()
        auth_username = normalized_text(username)
        if auth_username:
            self.set_headers({'Authorization': bitbucket_basic_auth_header(auth_username, token)})

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
        allowed_states = self._normalized_allowed_states(states)
        normalized_assignee = str(assignee or '').strip().lower()
        return self._normalize_issue_tasks(
            self._json_items(response, items_key='values'),
            to_task=self._to_task,
            include=lambda issue: (
                (
                    not normalized_assignee
                    or self._matches_assignee(
                        issue.get(BitbucketIssueFields.ASSIGNEE),
                        normalized_assignee,
                    )
                )
                and self._matches_allowed_state(
                    issue.get(BitbucketIssueFields.STATE),
                    allowed_states,
                )
            ),
        )

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
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        content = payload.get(BitbucketIssueFields.CONTENT, {})
        if not isinstance(content, dict):
            content = {}
        return self._build_task(
            issue_id=issue_id,
            summary=payload.get(BitbucketIssueFields.TITLE),
            description=self._build_task_description_with_comments(
                content.get(BitbucketIssueFields.RAW),
                comment_entries,
            ),
            comment_entries=comment_entries,
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_issue_response_items(
            issue_id,
            item_label='comments',
            path=f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}/comments',
            params={'pagelen': 100},
            items_key='values',
        )

    @staticmethod
    def _task_comment_entries(comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            content = comment.get(BitbucketIssueCommentFields.CONTENT, {})
            if not isinstance(content, dict):
                content = {}
            body = str(content.get(BitbucketIssueCommentFields.RAW, '') or '').strip()
            user = comment.get(BitbucketIssueCommentFields.USER, {})
            if not isinstance(user, dict):
                user = {}
            entry = BitbucketIssuesClient._task_comment_entry(
                user.get(BitbucketIssueCommentFields.DISPLAY_NAME)
                or user.get(BitbucketIssueCommentFields.NICKNAME),
                body,
            )
            if entry:
                entries.append(entry)
        return entries

    @staticmethod
    def _matches_assignee(assignee: Any, expected: str) -> bool:
        if not isinstance(assignee, dict):
            return False
        candidates = {
            str(assignee.get(BitbucketIssueFields.DISPLAY_NAME, '') or '').strip().lower(),
            str(assignee.get(BitbucketIssueFields.NICKNAME, '') or '').strip().lower(),
        }
        return expected in candidates
