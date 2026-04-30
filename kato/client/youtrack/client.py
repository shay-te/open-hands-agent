from __future__ import annotations

from typing import Any

from kato.client.ticket_client_base import TicketClientBase
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import (
    YouTrackAttachmentFields,
    YouTrackCommentFields,
    YouTrackCustomFieldFields,
    YouTrackTagFields,
)
from kato.helpers.text_utils import (
    alphanumeric_lower_text,
    normalized_text,
    text_from_mapping,
)


class YouTrackClient(TicketClientBase):
    provider_name = 'youtrack'
    EVENT_FIELDS = 'id,presentation,$type'
    FIELD_VALUE_FIELDS = 'id,name,$type'
    COMMENT_FIELDS = ','.join(
        [
            YouTrackCommentFields.ID,
            YouTrackCommentFields.TEXT,
            (
                f'{YouTrackCommentFields.AUTHOR}'
                f'({YouTrackCommentFields.LOGIN},{YouTrackCommentFields.NAME})'
            ),
        ]
    )
    ATTACHMENT_FIELDS = ','.join(
        [
            YouTrackAttachmentFields.ID,
            YouTrackAttachmentFields.NAME,
            YouTrackAttachmentFields.MIME_TYPE,
            YouTrackAttachmentFields.CHARSET,
            YouTrackAttachmentFields.METADATA,
            YouTrackAttachmentFields.URL,
        ]
    )
    CUSTOM_FIELD_FIELDS = ','.join(
        [
            YouTrackCustomFieldFields.ID,
            YouTrackCustomFieldFields.NAME,
            YouTrackCustomFieldFields.TYPE,
        ]
    )
    DETAILED_CUSTOM_FIELD_FIELDS = ','.join(
        [
            YouTrackCustomFieldFields.ID,
            YouTrackCustomFieldFields.NAME,
            YouTrackCustomFieldFields.TYPE,
            f'value({FIELD_VALUE_FIELDS})',
            f'possibleEvents({EVENT_FIELDS})',
        ]
    )
    MAX_TEXT_ATTACHMENT_CHARS = 5000
    STATE_MACHINE_CUSTOM_FIELD_TYPE = 'StateMachineIssueCustomField'
    TAG_FIELDS = YouTrackTagFields.NAME

    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            '/api/issues',
            params={
                'query': self._build_assigned_tasks_query(project, assignee, states),
                'fields': 'idReadable',
                '$top': 1,
            },
        )
        response.raise_for_status()

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        query = self._build_assigned_tasks_query(project, assignee, states)
        response = self._get_with_retry(
            '/api/issues',
            params={'query': query, 'fields': 'idReadable,summary,description', '$top': 100},
        )
        response.raise_for_status()
        return self._normalize_issue_tasks(
            self._json_items(response),
            to_task=self._to_task,
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/comments',
            json={'text': comment},
        )
        response.raise_for_status()

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        field = self._issue_state_field(issue_id, field_name)
        if self._field_value_name(field) == state_name:
            return
        updated_field = self._updated_issue_state_field(
            issue_id,
            field_name,
            state_name,
            field,
        )
        self._assert_issue_state(issue_id, field_name, state_name, updated_field)

    def _issue_state_field(
        self,
        issue_id: str,
        field_name: str,
    ) -> dict[str, Any]:
        field = self._get_issue_custom_field(
            issue_id,
            field_name,
            fields=self.DETAILED_CUSTOM_FIELD_FIELDS,
        )
        field_id = text_from_mapping(field, YouTrackCustomFieldFields.ID)
        if not field_id:
            raise ValueError(f'missing issue field id for: {field_name}')
        field_type = text_from_mapping(field, YouTrackCustomFieldFields.TYPE)
        if not field_type:
            raise ValueError(f'missing issue field type for: {field_name}')
        return field

    def _updated_issue_state_field(
        self,
        issue_id: str,
        field_name: str,
        state_name: str,
        field: dict[str, Any],
    ) -> dict[str, Any]:
        field_id = text_from_mapping(field, YouTrackCustomFieldFields.ID)
        field_type = text_from_mapping(field, YouTrackCustomFieldFields.TYPE)
        if field_type == self.STATE_MACHINE_CUSTOM_FIELD_TYPE:
            return self._move_issue_state_machine_field(issue_id, field, state_name)
        return self._move_issue_value_field(
            issue_id,
            field_id,
            field_name,
            field_type,
            state_name,
        )

    def _assert_issue_state(
        self,
        issue_id: str,
        field_name: str,
        state_name: str,
        updated_field: dict[str, Any],
    ) -> None:
        updated_state_name = self._field_value_name(updated_field)
        if updated_state_name == state_name:
            return
        verified_field = self._get_issue_custom_field(
            issue_id,
            field_name,
            fields=self.DETAILED_CUSTOM_FIELD_FIELDS,
        )
        verified_state_name = self._field_value_name(verified_field)
        if verified_state_name == state_name:
            return
        current_state = verified_state_name or updated_state_name or '<unknown>'
        raise ValueError(
            f'issue {issue_id} field {field_name} did not move to state '
            f'{state_name}; current state is {current_state}'
        )

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = payload['idReadable']
        tags = self._task_tags(issue_id)
        comment_entries = self._task_comment_entries(self._get_issue_comments(issue_id))
        attachments = self._get_issue_attachments(issue_id)
        return self._build_task(
            issue_id=issue_id,
            summary=payload.get(Task.summary.key),
            description=self._build_task_description(
                payload.get(Task.description.key),
                comment_entries,
                attachments,
            ),
            comment_entries=comment_entries,
            tags=tags,
        )

    def _task_tags(self, issue_id: str) -> list[str]:
        response = self._get_with_retry(
            f'/api/issues/{issue_id}/tags',
            params={'fields': self.TAG_FIELDS, '$top': 100},
        )
        response.raise_for_status()
        tags: list[str] = []
        for item in self._json_items(response):
            if not isinstance(item, dict):
                continue
            tag_name = normalized_text(item.get(YouTrackTagFields.NAME))
            if tag_name:
                tags.append(tag_name)
        return tags

    @staticmethod
    def _build_assigned_tasks_query(project: str, assignee: str, states: list[str]) -> str:
        if not states:
            raise ValueError('states must not be empty')
        state_filter = ', '.join(f'{{{state}}}' for state in states)
        return f'project: {project} assignee: {assignee} State: {state_filter}'

    def _get_issue_custom_field(
        self,
        issue_id: str,
        field_name: str,
        fields: str | None = None,
    ) -> dict[str, Any]:
        response = self._get_with_retry(
            f'/api/issues/{issue_id}/customFields',
            params={'fields': fields or self.CUSTOM_FIELD_FIELDS},
        )
        response.raise_for_status()
        for field in self._json_items(response):
            if isinstance(field, dict) and field.get(YouTrackCustomFieldFields.NAME) == field_name:
                return field
        raise ValueError(f'unknown issue field: {field_name}')

    def _move_issue_value_field(
        self,
        issue_id: str,
        field_id: str,
        field_name: str,
        field_type: str,
        state_name: str,
    ) -> dict[str, Any]:
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/customFields/{field_id}',
            params={'fields': self.DETAILED_CUSTOM_FIELD_FIELDS},
            json={
                YouTrackCustomFieldFields.ID: field_id,
                YouTrackCustomFieldFields.NAME: field_name,
                YouTrackCustomFieldFields.TYPE: field_type,
                'value': {'name': state_name},
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _move_issue_state_machine_field(
        self,
        issue_id: str,
        field: dict[str, Any],
        state_name: str,
    ) -> dict[str, Any]:
        field_id = text_from_mapping(field, YouTrackCustomFieldFields.ID)
        field_name = text_from_mapping(field, YouTrackCustomFieldFields.NAME) or '<unknown>'
        event = self._matching_state_machine_event(field, state_name)
        if event is None:
            raise ValueError(
                f'no YouTrack transition event matched state {state_name} for field {field_name}'
            )
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/customFields/{field_id}',
            params={'fields': self.DETAILED_CUSTOM_FIELD_FIELDS},
            json={
                YouTrackCustomFieldFields.ID: field_id,
                YouTrackCustomFieldFields.TYPE: self.STATE_MACHINE_CUSTOM_FIELD_TYPE,
                'event': event,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _matching_state_machine_event(
        cls,
        field: dict[str, Any],
        state_name: str,
    ) -> dict[str, Any] | None:
        desired_token = cls._normalized_state_token(state_name)
        for event in field.get('possibleEvents') or []:
            if not isinstance(event, dict):
                continue
            event_id = text_from_mapping(event, YouTrackCustomFieldFields.ID)
            presentation = text_from_mapping(event, 'presentation')
            if (
                cls._normalized_state_token(presentation) != desired_token
                and cls._normalized_state_token(event_id) != desired_token
            ):
                continue
            payload = {
                key: value
                for key, value in event.items()
                if key in {YouTrackCustomFieldFields.ID, 'presentation', YouTrackCustomFieldFields.TYPE}
            }
            payload.setdefault(YouTrackCustomFieldFields.TYPE, 'Event')
            return payload
        return None

    @staticmethod
    def _field_value_name(field: dict[str, Any]) -> str:
        value = field.get('value')
        if isinstance(value, dict):
            return text_from_mapping(value, YouTrackCustomFieldFields.NAME)
        return ''

    @staticmethod
    def _normalized_state_token(value: str) -> str:
        return alphanumeric_lower_text(value)

    def _get_issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._get_issue_items(
            issue_id,
            suffix='comments',
            fields=self.COMMENT_FIELDS,
            item_label='comments',
        )

    def _get_issue_attachments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._get_issue_items(
            issue_id,
            suffix='attachments',
            fields=self.ATTACHMENT_FIELDS,
            item_label='attachments',
        )

    def _get_issue_items(
        self,
        issue_id: str,
        suffix: str,
        fields: str,
        item_label: str,
    ) -> list[dict[str, Any]]:
        return self._best_effort_issue_response_items(
            issue_id,
            item_label=item_label,
            path=f'/api/issues/{issue_id}/{suffix}',
            params={'fields': fields, '$top': 100},
        )

    def _build_task_description(
        self,
        description,
        comment_entries: list[dict[str, str]],
        attachments: list[dict[str, Any]],
    ) -> str:
        return self._build_task_description_with_attachment_sections(
            description,
            comment_entries,
            text_attachment_lines=self._format_text_attachments(attachments),
            screenshot_lines=self._format_screenshot_attachments(attachments),
        )

    @classmethod
    def _task_comment_entries(cls, comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        return cls._build_comment_entries(
            comments,
            extract_body=lambda c: text_from_mapping(c, YouTrackCommentFields.TEXT),
            extract_author=cls._comment_author_name,
        )

    def _format_text_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        return self._format_text_attachment_lines(
            attachments,
            is_text_attachment=self._is_text_attachment,
            read_text_attachment=self._read_text_attachment,
            attachment_name=self._attachment_name,
        )

    @staticmethod
    def _format_screenshot_attachments(attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            mime_type = attachment.get(YouTrackAttachmentFields.MIME_TYPE) or ''
            if not mime_type.startswith('image/'):
                continue
            metadata = attachment.get(YouTrackAttachmentFields.METADATA) or 'no metadata'
            url = attachment.get(YouTrackAttachmentFields.URL) or ''
            lines.append(f'- {YouTrackClient._attachment_name(attachment)} ({metadata}) {url}'.strip())
        return lines

    def _read_text_attachment(self, attachment: dict[str, Any]) -> str | None:
        return self._download_text_attachment(
            attachment.get(YouTrackAttachmentFields.URL),
            attachment_name=self._attachment_name(attachment),
            max_chars=self.MAX_TEXT_ATTACHMENT_CHARS,
            charset=str(attachment.get(YouTrackAttachmentFields.CHARSET) or 'utf-8'),
        )

    @classmethod
    def _is_text_attachment(cls, attachment: dict[str, Any]) -> bool:
        return cls._is_text_attachment_mime_type(
            attachment.get(YouTrackAttachmentFields.MIME_TYPE) or ''
        )

    @staticmethod
    def _comment_author_name(comment: dict[str, Any]) -> str:
        author = comment.get(YouTrackCommentFields.AUTHOR) or {}
        if not isinstance(author, dict):
            author = {}
        return str(
            author.get(YouTrackCommentFields.NAME)
            or author.get(YouTrackCommentFields.LOGIN)
            or 'unknown'
        )

    @staticmethod
    def _attachment_name(attachment: dict[str, Any]) -> str:
        return str(attachment.get(YouTrackAttachmentFields.NAME, 'unknown'))
