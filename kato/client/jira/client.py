from __future__ import annotations

from typing import Any

from kato.helpers.retry_utils import run_with_retry
from kato.client.ticket_client_base import TicketClientBase
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import (
    JiraAttachmentFields,
    JiraCommentFields,
    JiraIssueFields,
    JiraTransitionFields,
)


class JiraClient(TicketClientBase):
    provider_name = 'jira'
    MAX_TEXT_ATTACHMENT_CHARS = 5000

    def __init__(
        self,
        base_url: str,
        token: str,
        user_email: str = '',
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        if str(user_email or '').strip():
            self.headers = None
            self.set_auth((str(user_email).strip(), token))

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            '/rest/api/3/search',
            params={
                'jql': self._build_assigned_tasks_query(project, assignee, states),
                'fields': JiraIssueFields.KEY,
                'maxResults': 1,
            },
        )
        response.raise_for_status()

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        response = self._get_with_retry(
            '/rest/api/3/search',
            params={
                'jql': self._build_assigned_tasks_query(project, assignee, states),
                'fields': ','.join(
                    [
                        JiraIssueFields.SUMMARY,
                        JiraIssueFields.DESCRIPTION,
                        JiraIssueFields.COMMENT,
                        JiraIssueFields.ATTACHMENT,
                        JiraIssueFields.LABELS,
                    ]
                ),
                'maxResults': 100,
            },
        )
        response.raise_for_status()
        return self._normalize_issue_tasks(
            self._json_items(response, items_key='issues'),
            to_task=self._to_task,
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/rest/api/3/issue/{issue_id}/comment',
            json={'body': comment},
        )
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        normalized_field_name = str(field_name or '').strip().lower()
        if normalized_field_name in {'status', JiraIssueFields.STATUS.lower()}:
            transition = self._find_transition(issue_id, state_name)
            response = self._post_with_retry(
                f'/rest/api/3/issue/{issue_id}/transitions',
                json={'transition': {JiraTransitionFields.ID: transition[JiraTransitionFields.ID]}},
            )
            response.raise_for_status()
            return

        response = self._put_with_retry(
            f'/rest/api/3/issue/{issue_id}',
            json={'fields': {field_name: state_name}},
        )
        response.raise_for_status()

    def _put_with_retry(self, path: str, **kwargs):
        return run_with_retry(
            lambda: self._put(path, **kwargs),
            self.max_retries,
            operation_name=self._retry_operation_name('PUT', path),
        )

    def _find_transition(self, issue_id: str, state_name: str) -> dict[str, Any]:
        response = self._get_with_retry(
            f'/rest/api/3/issue/{issue_id}/transitions',
        )
        response.raise_for_status()
        payload = response.json() or {}
        transitions = payload.get('transitions', []) if isinstance(payload, dict) else []
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            transition_name = str(transition.get(JiraTransitionFields.NAME, '') or '').strip()
            target = transition.get(JiraTransitionFields.TO, {})
            target_name = ''
            if isinstance(target, dict):
                target_name = str(target.get(JiraTransitionFields.NAME, '') or '').strip()
            if state_name in {transition_name, target_name}:
                return transition
        raise ValueError(f'unknown jira transition: {state_name}')

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = str(payload[JiraIssueFields.KEY])
        fields = payload.get('fields', {})
        if not isinstance(fields, dict):
            fields = {}
        comment_entries = self._task_comment_entries(self._issue_comments(fields))
        attachments = self._issue_attachments(fields)
        return self._build_task(
            issue_id=issue_id,
            summary=fields.get(JiraIssueFields.SUMMARY),
            description=self._build_task_description(
                self._adf_to_text(fields.get(JiraIssueFields.DESCRIPTION)),
                comment_entries,
                attachments,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(fields.get(JiraIssueFields.LABELS)),
        )

    @staticmethod
    def _build_assigned_tasks_query(project: str, assignee: str, states: list[str]) -> str:
        if not states:
            raise ValueError('states must not be empty')
        normalized_states = ', '.join(f'"{state}"' for state in states)
        return (
            f'project = "{project}" AND assignee = "{assignee}" '
            f'AND status IN ({normalized_states}) ORDER BY updated DESC'
        )

    def _issue_comments(self, fields: dict[str, Any]) -> list[dict[str, Any]]:
        comments = fields.get(JiraIssueFields.COMMENT, {})
        if not isinstance(comments, dict):
            return []
        values = comments.get('comments', [])
        return list(values) if isinstance(values, list) else []

    def _issue_attachments(self, fields: dict[str, Any]) -> list[dict[str, Any]]:
        attachments = fields.get(JiraIssueFields.ATTACHMENT, [])
        return list(attachments) if isinstance(attachments, list) else []

    def _build_task_description(
        self,
        description: str,
        comment_entries: list[dict[str, str]],
        attachments: list[dict[str, Any]],
    ) -> str:
        return self._build_task_description_with_attachment_sections(
            description,
            comment_entries,
            text_attachment_lines=self._format_text_attachments(attachments),
            screenshot_lines=self._format_screenshot_attachments(attachments),
        )

    def _task_comment_entries(self, comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        return self._build_comment_entries(
            comments,
            extract_body=lambda c: self._adf_to_text(c.get(JiraCommentFields.BODY)),
            extract_author=lambda c: self._safe_dict(c, JiraCommentFields.AUTHOR).get(JiraCommentFields.DISPLAY_NAME),
        )

    def _format_text_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        return self._format_text_attachment_lines(
            attachments,
            is_text_attachment=self._is_text_attachment,
            read_text_attachment=self._read_text_attachment,
            attachment_name=self._attachment_name,
        )

    def _format_screenshot_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            mime_type = str(attachment.get(JiraAttachmentFields.MIME_TYPE, '') or '')
            if not mime_type.startswith('image/'):
                continue
            filename = self._attachment_name(attachment)
            content_url = str(attachment.get(JiraAttachmentFields.CONTENT, '') or '').strip()
            size = attachment.get(JiraAttachmentFields.SIZE)
            size_text = f'{size} bytes' if size else 'image attachment'
            lines.append(f'- {filename} ({size_text}) {content_url}'.strip())
        return lines

    def _read_text_attachment(self, attachment: dict[str, Any]) -> str | None:
        return self._download_text_attachment(
            attachment.get(JiraAttachmentFields.CONTENT),
            attachment_name=self._attachment_name(attachment),
            max_chars=self.MAX_TEXT_ATTACHMENT_CHARS,
            log_label='jira text attachment',
        )

    @classmethod
    def _is_text_attachment(cls, attachment: dict[str, Any]) -> bool:
        return cls._is_text_attachment_mime_type(
            attachment.get(JiraAttachmentFields.MIME_TYPE, '')
        )

    @staticmethod
    def _attachment_name(attachment: dict[str, Any]) -> str:
        return str(attachment.get(JiraAttachmentFields.FILENAME, 'unknown'))

    @classmethod
    def _adf_to_text(cls, value: Any) -> str:
        if value is None:
            return ''
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [cls._adf_to_text(item) for item in value]
            return ' '.join(part for part in parts if part).strip()
        if not isinstance(value, dict):
            return str(value).strip()

        text = str(value.get('text', '') or '').strip()
        parts = [text] if text else []
        content = value.get('content', [])
        if isinstance(content, list):
            for item in content:
                item_text = cls._adf_to_text(item)
                if item_text:
                    parts.append(item_text)
        separator = '\n' if value.get('type') in {'paragraph', 'heading'} else ' '
        return separator.join(part for part in parts if part).strip()
