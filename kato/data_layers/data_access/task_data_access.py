from omegaconf import DictConfig

from core_lib.data_layers.data_access.data_access import DataAccess
from core_lib.rule_validator.rule_validator import RuleValidator, ValueRuleValidator

from kato.helpers.retry_utils import retry_count
from kato.client.ticket_client_base import TicketClientBase
from kato.data_layers.data.task import Task


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

move_task_state_rule_validator = RuleValidator(
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

    @property
    def max_retries(self) -> int:
        return retry_count(getattr(self._client, 'max_retries', 1))

    def validate_connection(
        self,
        assignee: str,
        states: list[str],
    ) -> None:
        assigned_task_rule_validator.validate_dict(
            {
                'assignee': assignee,
                'states': states,
            }
        )
        self._client.validate_connection(
            project=self._config.project,
            assignee=assignee,
            states=states,
        )

    def get_assigned_tasks(
        self,
        assignee: str,
        states: list[str],
    ) -> list[Task]:
        assigned_task_rule_validator.validate_dict(
            {
                'assignee': assignee,
                'states': states,
            }
        )
        return self._client.get_assigned_tasks(
            project=self._config.project,
            assignee=assignee,
            states=states,
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        pull_request_comment_rule_validator.validate_dict(
            {
                'issue_id': issue_id,
                'comment': comment,
            }
        )
        self._client.add_comment(issue_id, comment)

    def move_task_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        move_task_state_rule_validator.validate_dict({'issue_id': issue_id})
        self._client.move_issue_to_state(issue_id, field_name, state_name)
