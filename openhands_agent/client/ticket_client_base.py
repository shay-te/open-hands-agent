from openhands_agent.client.retrying_client_base import RetryingClientBase


class TicketClientBase(RetryingClientBase):
    provider_name = 'issue_platform'

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        raise NotImplementedError

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]):
        raise NotImplementedError

    def add_comment(self, issue_id: str, comment: str) -> None:
        raise NotImplementedError

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        raise NotImplementedError
