from typing import Any

from core_lib.client.client_base import ClientBase

from openhands_agent.data_layers.data.task import Task


class YouTrackClient(ClientBase):
    COMMENT_FIELDS = 'id,text,author(login,name)'
    ATTACHMENT_FIELDS = 'id,name,mimeType,charset,metaData,url'
    MAX_TEXT_ATTACHMENT_CHARS = 5000

    def __init__(self, base_url: str, token: str) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(30)

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        query = self._build_assigned_tasks_query(project, assignee, states)
        response = self._get(
            '/api/issues',
            params={'query': query, 'fields': 'idReadable,summary,description'},
        )
        response.raise_for_status()
        return [self._to_task(item) for item in response.json()]

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        response = self._post(
            f'/api/issues/{issue_id}/comments',
            json={'text': f'Pull request created: {pull_request_url}'},
        )
        response.raise_for_status()

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = payload['idReadable']
        comments = self._get_issue_comments(issue_id)
        attachments = self._get_issue_attachments(issue_id)
        return Task(
            id=issue_id,
            summary=payload.get(Task.summary.key, ''),
            description=self._build_task_description(
                payload.get(Task.description.key) or '',
                comments,
                attachments,
            ),
            branch_name=f'feature/{issue_id.lower()}',
        )

    @staticmethod
    def _build_assigned_tasks_query(project: str, assignee: str, states: list[str]) -> str:
        state_filter = ', '.join(f'{{{state}}}' for state in states)
        return f'project: {project} assignee: {assignee} State: {state_filter}'

    def _get_issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        response = self._get(
            f'/api/issues/{issue_id}/comments',
            params={'fields': self.COMMENT_FIELDS},
        )
        response.raise_for_status()
        return list(response.json())

    def _get_issue_attachments(self, issue_id: str) -> list[dict[str, Any]]:
        response = self._get(
            f'/api/issues/{issue_id}/attachments',
            params={'fields': self.ATTACHMENT_FIELDS},
        )
        response.raise_for_status()
        return list(response.json())

    def _build_task_description(
        self,
        description: str,
        comments: list[dict[str, Any]],
        attachments: list[dict[str, Any]],
    ) -> str:
        sections = [description.strip() or 'No description provided.']

        comment_lines = self._format_comments(comments)
        if comment_lines:
            sections.append('Issue comments:\n' + '\n'.join(comment_lines))

        text_attachment_lines = self._format_text_attachments(attachments)
        if text_attachment_lines:
            sections.append('Text attachments:\n' + '\n\n'.join(text_attachment_lines))

        screenshot_lines = self._format_screenshot_attachments(attachments)
        if screenshot_lines:
            sections.append('Screenshot attachments:\n' + '\n'.join(screenshot_lines))

        return '\n\n'.join(section for section in sections if section)

    @staticmethod
    def _format_comments(comments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for comment in comments:
            text = (comment.get('text') or '').strip()
            if not text:
                continue
            author = comment.get('author') or {}
            author_name = author.get('name') or author.get('login') or 'unknown'
            lines.append(f'- {author_name}: {text}')
        return lines

    def _format_text_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not self._is_text_attachment(attachment):
                continue
            content = self._read_text_attachment(attachment)
            if not content:
                continue
            lines.append(f'Attachment {attachment.get("name", "unknown")}:\n{content}')
        return lines

    @staticmethod
    def _format_screenshot_attachments(attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            mime_type = attachment.get('mimeType') or ''
            if not mime_type.startswith('image/'):
                continue
            metadata = attachment.get('metaData') or 'no metadata'
            url = attachment.get('url') or ''
            lines.append(f'- {attachment.get("name", "unknown")} ({metadata}) {url}'.strip())
        return lines

    def _read_text_attachment(self, attachment: dict[str, Any]) -> str:
        url = attachment.get('url')
        if not url:
            return ''

        response = self._get(url)
        response.raise_for_status()
        content = getattr(response, 'text', '')
        if isinstance(content, str) and content:
            return content[: self.MAX_TEXT_ATTACHMENT_CHARS]

        raw_content = getattr(response, 'content', b'')
        if not raw_content:
            return ''

        charset = attachment.get('charset') or 'utf-8'
        return raw_content.decode(charset, errors='replace')[: self.MAX_TEXT_ATTACHMENT_CHARS]

    @staticmethod
    def _is_text_attachment(attachment: dict[str, Any]) -> bool:
        mime_type = attachment.get('mimeType') or ''
        return mime_type.startswith('text/') or mime_type in {
            'application/json',
            'application/xml',
            'application/yaml',
        }
