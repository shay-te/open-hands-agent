from openhands_agent.client.retrying_client_base import RetryingClientBase


class TicketClientBase(RetryingClientBase):
    provider_name = 'issue_platform'
    AGENT_COMMENT_PREFIXES = (
        'OpenHands agent could not safely process this task:',
        'OpenHands agent skipped this task because it could not detect which repository',
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
    def _is_agent_operational_comment(cls, text: str) -> bool:
        normalized_text = str(text or '').strip()
        return any(normalized_text.startswith(prefix) for prefix in cls.AGENT_COMMENT_PREFIXES)
