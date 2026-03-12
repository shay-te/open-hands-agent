from omegaconf import DictConfig

from core_lib.data_layers.data_access.data_access import DataAccess
from core_lib.rule_validator.rule_validator import RuleValidator, ValueRuleValidator

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.task import Task


assigned_task_rule_validator = RuleValidator(
    [
        ValueRuleValidator('assignee', (str, type(None))),
        ValueRuleValidator('states', (list, type(None))),
    ]
)

pull_request_comment_rule_validator = RuleValidator(
    [
        ValueRuleValidator('issue_id', str),
        ValueRuleValidator('comment', str),
    ]
)

move_to_review_rule_validator = RuleValidator(
    [
        ValueRuleValidator('issue_id', str),
    ]
)


class TaskDataAccess(DataAccess):
    def __init__(self, config: DictConfig, client: TicketClientBase) -> None:
        self._config = config
        self._client = client

    @property
    def provider_name(self) -> str:
        return getattr(self._client, 'provider_name', 'ticket_system')

    def validate_connection(self) -> None:
        self._client.validate_connection(
            project=self._config.project,
            assignee=self._config.assignee,
            states=self._configured_issue_states(),
        )

    def get_assigned_tasks(
        self,
        assignee: str | None = None,
        states: list[str] | None = None,
    ) -> list[Task]:
        assigned_task_rule_validator.validate(
            {
                'assignee': assignee,
                'states': states,
            }
        )
        return self._client.get_assigned_tasks(
            project=self._config.project,
            assignee=assignee or self._config.assignee,
            states=states or self._configured_issue_states(),
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        pull_request_comment_rule_validator.validate(
            {
                'issue_id': issue_id,
                'comment': comment,
            }
        )
        self._client.add_comment(issue_id, comment)

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def move_task_to_review(self, issue_id: str) -> None:
        move_to_review_rule_validator.validate(
            {
                'issue_id': issue_id,
            }
        )
        self._client.move_issue_to_state(
            issue_id,
            self._configured_review_state_field(),
            self._configured_review_state(),
        )

    def _configured_issue_states(self) -> list[str]:
        if hasattr(self._config, 'issue_states'):
            issue_states = self._config.issue_states
            if isinstance(issue_states, str):
                return [state.strip() for state in issue_states.split(',') if state.strip()]
            return [str(state).strip() for state in issue_states if str(state).strip()]
        return [self._config.issue_state]

    def _configured_review_state_field(self) -> str:
        return getattr(self._config, 'review_state_field', 'State')

    def _configured_review_state(self) -> str:
        return getattr(self._config, 'review_state', 'In Review')
