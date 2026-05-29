from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

from provider_client_base.provider_client_base.client.issue_client_base import (
    IssueClientBase,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord
from provider_client_base.provider_client_base.helpers.mention_utils import (
    is_comment_addressed_elsewhere,
)

from gitlab_core_lib.gitlab_core_lib.data.fields import (
    GitLabCommentFields,
    GitLabIssueFields,
)


class GitLabIssuesClient(IssueClientBase):
    provider_name = 'gitlab'

    def __init__(
        self,
        base_url: str,
        token: str,
        project: str,
        max_retries: int = 3,
        *,
        is_operational_comment: Callable[[str], bool] | None = None,
        bot_login: str = '',
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._project = quote(str(project).strip(), safe='')
        self._is_operational_comment: Callable[[str], bool] = (
            is_operational_comment or (lambda _: False)
        )
        # See provider_client_base.helpers.mention_utils for the rule;
        # empty value disables the @-mention filter.
        self._bot_login = str(bot_login or '').strip()
        self.set_headers({'PRIVATE-TOKEN': token})

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/projects/{self._project}/issues',
            params={'assignee_username': assignee, 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(
        self,
        project: str,
        assignee: str,
        states: list[str],
    ) -> list[IssueRecord]:
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
        return self._normalize_issue_records(
            self._json_items(response),
            to_record=self._to_record,
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

    def add_tag(self, issue_id: str, tag_name: str) -> None:
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'add_labels': tag_name},
        )
        response.raise_for_status()

    def remove_tag(self, issue_id: str, tag_name: str) -> None:
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'remove_labels': tag_name},
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
        state_event = (
            'reopen'
            if state_name.strip().lower() in {'open', 'opened', 'reopen'}
            else 'close'
        )
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'state_event': state_event},
        )
        response.raise_for_status()

    # ----- internal record builders -----

    def _to_record(self, payload: dict[str, Any]) -> IssueRecord:
        issue_id = str(payload[GitLabIssueFields.IID])
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        return self._build_record(
            issue_id=issue_id,
            summary=payload.get(GitLabIssueFields.TITLE),
            description=self._build_description_with_comments(
                payload.get(GitLabIssueFields.DESCRIPTION),
                comment_entries,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(payload.get(GitLabIssueFields.LABELS)),
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_response_items(
            issue_id,
            item_label='comments',
            path=f'/projects/{self._project}/issues/{issue_id}/notes',
            params={'per_page': 100},
        )

    def _task_comment_entries(
        self, comments: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        def extract_author(c: dict) -> object:
            author = self._safe_dict(c, GitLabCommentFields.AUTHOR)
            return author.get(GitLabCommentFields.NAME) or author.get(GitLabCommentFields.USERNAME)

        # Skip system notes AND comments addressed to humans other
        # than the kato bot. The former is GitLab-specific machinery
        # noise ("changed status to closed"); the latter is the
        # cross-platform @-mention filter — see
        # provider_client_base.helpers.mention_utils.
        def skip(c: dict) -> bool:
            if c.get(GitLabCommentFields.SYSTEM):
                return True
            return is_comment_addressed_elsewhere(
                c.get(GitLabCommentFields.BODY, ''),
                self._bot_login,
            )

        return self._build_comment_entries(
            comments,
            extract_body=lambda c: str(c.get(GitLabCommentFields.BODY, '') or '').strip(),
            extract_author=extract_author,
            skip=skip,
        )
