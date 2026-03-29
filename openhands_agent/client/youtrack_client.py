from typing import Any
from urllib.parse import urlparse

from openhands_agent.client.retry_utils import run_with_retry
from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    TaskCommentFields,
    YouTrackAttachmentFields,
    YouTrackCommentFields,
    YouTrackCustomFieldFields,
)

class YouTrackClient(TicketClientBase):
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
        tasks: list[Task] = []
        for item in self._json_list(response):
            try:
                tasks.append(self._to_task(item))
            except (KeyError, TypeError, ValueError):
                self.logger.exception('failed to normalize youtrack issue payload')
                continue
        return tasks

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/comments',
            json={'text': comment},
        )
        response.raise_for_status()

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        field = self._get_issue_custom_field(
            issue_id,
            field_name,
            fields=self.DETAILED_CUSTOM_FIELD_FIELDS,
        )
        field_id = str(field.get(YouTrackCustomFieldFields.ID) or '').strip()
        if not field_id:
            raise ValueError(f'missing issue field id for: {field_name}')
        field_type = str(field.get(YouTrackCustomFieldFields.TYPE) or '').strip()
        if not field_type:
            raise ValueError(f'missing issue field type for: {field_name}')
        if self._field_value_name(field) == state_name:
            return

        if field_type == self.STATE_MACHINE_CUSTOM_FIELD_TYPE:
            updated_field = self._move_issue_state_machine_field(
                issue_id,
                field,
                state_name,
            )
        else:
            updated_field = self._move_issue_value_field(
                issue_id,
                field_id,
                field_name,
                field_type,
                state_name,
            )

        updated_state_name = self._field_value_name(updated_field)
        if updated_state_name != state_name:
            verified_field = self._get_issue_custom_field(
                issue_id,
                field_name,
                fields=self.DETAILED_CUSTOM_FIELD_FIELDS,
            )
            verified_state_name = self._field_value_name(verified_field)
            if verified_state_name != state_name:
                current_state = verified_state_name or updated_state_name or '<unknown>'
                raise ValueError(
                    f'issue {issue_id} field {field_name} did not move to state '
                    f'{state_name}; current state is {current_state}'
                )

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = payload['idReadable']
        comment_entries = self._task_comment_entries(self._get_issue_comments(issue_id))
        attachments = self._get_issue_attachments(issue_id)
        task = Task(
            id=issue_id,
            summary=str(payload.get(Task.summary.key, '') or ''),
            description=self._build_task_description(
                payload.get(Task.description.key),
                comment_entries,
                attachments,
            ),
            branch_name=f'feature/{issue_id.lower()}',
        )
        self._set_task_comments(task, comment_entries)
        return task

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
        for field in self._json_list(response):
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
        field_id = str(field.get(YouTrackCustomFieldFields.ID) or '').strip()
        field_name = str(field.get(YouTrackCustomFieldFields.NAME) or '').strip() or '<unknown>'
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
            event_id = str(event.get(YouTrackCustomFieldFields.ID) or '').strip()
            presentation = str(event.get('presentation') or '').strip()
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
            return str(value.get(YouTrackCustomFieldFields.NAME) or '').strip()
        return ''

    @staticmethod
    def _normalized_state_token(value: str) -> str:
        return ''.join(character for character in str(value or '').lower() if character.isalnum())

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
        try:
            response = self._get_with_retry(
                f'/api/issues/{issue_id}/{suffix}',
                params={'fields': fields, '$top': 100},
            )
            response.raise_for_status()
            return self._json_list(response)
        except Exception:
            self.logger.exception('failed to fetch %s for issue %s', item_label, issue_id)
            return []

    def _build_task_description(
        self,
        description,
        comment_entries: list[dict[str, str]],
        attachments: list[dict[str, Any]],
    ) -> str:
        base_description = str(description or '').strip()
        sections = [base_description or 'No description provided.']
        self._append_comment_section(sections, comment_entries)

        text_attachment_lines = self._format_text_attachments(attachments)
        if text_attachment_lines:
            sections.append(
                f'{self.UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE}:\n'
                + '\n\n'.join(text_attachment_lines)
            )

        screenshot_lines = self._format_screenshot_attachments(attachments)
        if screenshot_lines:
            sections.append(
                f'{self.UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE}:\n'
                + '\n'.join(screenshot_lines)
            )

        return self._join_task_description_sections(sections)

    @classmethod
    def _task_comment_entries(
        cls,
        comments: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            text = str(comment.get(YouTrackCommentFields.TEXT) or '').strip()
            if not text:
                continue
            entries.append(
                {
                    TaskCommentFields.AUTHOR: cls._comment_author_name(comment),
                    TaskCommentFields.BODY: text,
                }
            )
        return entries

    def _format_text_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if not self._is_text_attachment(attachment):
                continue
            content = self._read_text_attachment(attachment)
            if content is None:
                lines.append(self._attachment_download_failure_message(attachment))
                continue
            if not content:
                continue
            lines.append(f'Attachment {self._attachment_name(attachment)}:\n{content}')
        return lines

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
        url = attachment.get(YouTrackAttachmentFields.URL)
        if not url:
            return ''

        try:
            response = self._get_attachment_with_retry(str(url))
            response.raise_for_status()
            content = getattr(response, 'text', '')
            if isinstance(content, str) and content:
                return self._truncate_attachment_content(content)

            raw_content = getattr(response, 'content', b'')
            if not raw_content:
                return ''

            charset = attachment.get(YouTrackAttachmentFields.CHARSET) or 'utf-8'
            content = raw_content.decode(charset, errors='replace')
            return self._truncate_attachment_content(content)
        except Exception:
            self.logger.exception(
                'failed to read text attachment %s',
                self._attachment_name(attachment),
            )
            return None

    def _get_attachment_with_retry(self, url: str):
        parsed_url = urlparse(url)
        if parsed_url.scheme and parsed_url.netloc:
            return run_with_retry(
                lambda: self.session.get(url, **self.process_kwargs()),
                self.max_retries,
            )
        return self._get_with_retry(url)

    @staticmethod
    def _is_text_attachment(attachment: dict[str, Any]) -> bool:
        mime_type = attachment.get(YouTrackAttachmentFields.MIME_TYPE) or ''
        return mime_type.startswith('text/') or mime_type in {
            'application/json',
            'application/xml',
            'application/yaml',
        }

    @staticmethod
    def _json_list(response) -> list[dict[str, Any]]:
        payload = response.json() or []
        return list(payload) if isinstance(payload, list) else []

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

    @classmethod
    def _attachment_download_failure_message(cls, attachment: dict[str, Any]) -> str:
        return f'Attachment {cls._attachment_name(attachment)} could not be downloaded.'

    def _truncate_attachment_content(self, content: str) -> str:
        return content[: self.MAX_TEXT_ATTACHMENT_CHARS]
