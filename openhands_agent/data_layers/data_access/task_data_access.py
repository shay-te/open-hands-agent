from omegaconf import DictConfig

from core_lib.rule_validator.rule_validator import RuleValidator, ValueRuleValidator

from openhands_agent.client.youtrack_client import YouTrackClient
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
        ValueRuleValidator('pull_request_url', str),
    ]
)


class TaskDataAccess:
    def __init__(self, config: DictConfig, client: YouTrackClient) -> None:
        self.config = config
        self.client = client

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
        return self.client.get_assigned_tasks(
            project=self.config.project,
            assignee=assignee or self.config.assignee,
            states=states or self._configured_issue_states(),
        )

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        pull_request_comment_rule_validator.validate(
            {
                'issue_id': issue_id,
                'pull_request_url': pull_request_url,
            }
        )
        self.client.add_pull_request_comment(issue_id, pull_request_url)

    def _configured_issue_states(self) -> list[str]:
        if hasattr(self.config, 'issue_states'):
            return list(self.config.issue_states)
        return [self.config.issue_state]
