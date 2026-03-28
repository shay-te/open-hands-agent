from openhands_agent.client.retrying_client_base import RetryingClientBase
from openhands_agent.fields import TaskCommentFields


class TicketClientBase(RetryingClientBase):
    provider_name = 'issue_platform'
    AGENT_COMMENT_PREFIXES = (
        'OpenHands agent could not safely process this task:',
        'OpenHands agent skipped this task because it could not detect which repository',
        'OpenHands agent skipped this task because the task definition',
        'OpenHands agent started working on this task',
        'OpenHands agent stopped working on this task:',
        'OpenHands completed task ',
    )
    AGENT_RETRY_BLOCKING_PREFIXES = (
        'OpenHands agent could not safely process this task:',
        'OpenHands agent skipped this task because it could not detect which repository',
        'OpenHands agent skipped this task because the task definition',
        'OpenHands agent stopped working on this task:',
    )
    RETRY_OVERRIDE_PHRASES = (
        'try again',
        'retry',
        'rerun',
        're-run',
        'go ahead',
        'move forward',
        'proceed',
        'resume work',
        'resume this task',
    )
    NEGATIVE_RETRY_OVERRIDE_PHRASES = (
        "don't retry",
        'do not retry',
        'dont retry',
        "don't try again",
        'do not try again',
        'dont try again',
        "don't rerun",
        'do not rerun',
        'dont rerun',
        "don't re-run",
        'do not re-run',
        'dont re-run',
    )

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        raise NotImplementedError

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]):
        raise NotImplementedError

    def add_comment(self, issue_id: str, comment: str) -> None:
        raise NotImplementedError

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        raise NotImplementedError

    @classmethod
    def _set_task_comments(cls, task, comments: list[dict[str, str]]) -> None:
        setattr(task, TaskCommentFields.ALL_COMMENTS, comments)

    @classmethod
    def _build_task_description_with_comments(
        cls,
        description: object,
        comments: list[dict[str, str]],
    ) -> str:
        sections = [str(description or '').strip() or 'No description provided.']
        cls._append_comment_section(sections, comments)
        return cls._join_task_description_sections(sections)

    @classmethod
    def _append_comment_section(
        cls,
        sections: list[str],
        comments: list[dict[str, str]],
    ) -> None:
        comment_lines = cls._comment_lines(comments)
        if comment_lines:
            sections.append('Issue comments:\n' + '\n'.join(comment_lines))

    @classmethod
    def _comment_lines(cls, comments: list[dict[str, str]]) -> list[str]:
        lines: list[str] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            body = str(comment.get(TaskCommentFields.BODY, '') or '').strip()
            if not body or cls._is_agent_operational_comment(body):
                continue
            author = str(comment.get(TaskCommentFields.AUTHOR, '') or 'unknown').strip() or 'unknown'
            lines.append(f'- {author}: {body}')
        return lines

    @staticmethod
    def _join_task_description_sections(sections: list[str]) -> str:
        return '\n\n'.join(section for section in sections if section)

    @classmethod
    def _is_agent_operational_comment(cls, text: str) -> bool:
        normalized_text = str(text or '').strip()
        return any(normalized_text.startswith(prefix) for prefix in cls.AGENT_COMMENT_PREFIXES)

    @classmethod
    def active_retry_blocking_comment(cls, comments: list[dict[str, str]] | None) -> str:
        active_comment = ''
        for comment in comments or []:
            if not isinstance(comment, dict):
                continue
            text = str(comment.get(TaskCommentFields.BODY, '') or '').strip()
            if not text:
                continue
            if cls._is_retry_blocking_comment(text):
                active_comment = text
                continue
            if active_comment and cls._is_retry_override_comment(text):
                active_comment = ''
        return active_comment

    @classmethod
    def _is_retry_blocking_comment(cls, text: str) -> bool:
        normalized_text = str(text or '').strip()
        return any(
            normalized_text.startswith(prefix)
            for prefix in cls.AGENT_RETRY_BLOCKING_PREFIXES
        )

    @classmethod
    def _is_retry_override_comment(cls, text: str) -> bool:
        if cls._is_agent_operational_comment(text):
            return False
        normalized_text = str(text or '').strip().lower()
        if not normalized_text:
            return False
        if any(phrase in normalized_text for phrase in cls.NEGATIVE_RETRY_OVERRIDE_PHRASES):
            return False
        return any(phrase in normalized_text for phrase in cls.RETRY_OVERRIDE_PHRASES)
