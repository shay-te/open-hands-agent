from __future__ import annotations

from omegaconf import DictConfig

from core_lib.data_layers.service.service import Service

from kato.data_layers.data.task import Task
from kato.data_layers.data_access.task_data_access import TaskDataAccess
from kato.helpers.kato_config_utils import parse_issue_states
from kato.helpers.text_utils import alphanumeric_lower_text


class TaskService(Service):
    """Wrap ticket-system task retrieval, queue filtering, and comments."""
    _STATE_VALUE_DEFAULTS = {
        'progress': 'In Progress',
        'review': 'In Review',
    }

    def __init__(self, config: DictConfig, task_data_access: TaskDataAccess) -> None:
        self._config = config
        self._task_data_access = task_data_access

    @property
    def provider_name(self) -> str:
        return self._task_data_access.provider_name

    @property
    def max_retries(self) -> int:
        return self._task_data_access.max_retries

    def validate_connection(self) -> None:
        self._task_data_access.validate_connection(
            assignee=self._configured_assignee(),
            states=self._configured_issue_states(),
        )

    def get_assigned_tasks(
        self,
        assignee: str | None = None,
        states: list[str] | None = None,
    ) -> list[Task]:
        return self._task_data_access.get_assigned_tasks(
            assignee=assignee or self._configured_assignee(),
            states=states or self._configured_issue_states(),
        )

    def get_review_tasks(self, assignee: str | None = None) -> list[Task]:
        return self.get_assigned_tasks(
            assignee=assignee,
            states=[self._configured_state_value('review')],
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        self._task_data_access.add_comment(issue_id, comment)

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def _configured_assignee(self) -> str:
        return self._config.assignee

    def _configured_issue_states(self) -> list[str]:
        configured_states = self._raw_configured_issue_states()
        filtered_states = self._exclude_non_queue_states(configured_states)
        return filtered_states

    def _raw_configured_issue_states(self) -> list[str]:
        return parse_issue_states(self._config)

    def _exclude_non_queue_states(self, states: list[str]) -> list[str]:
        non_queue_tokens = {
            self._normalized_state_token(self._configured_state_value('progress')),
            self._normalized_state_token(self._configured_state_value('review')),
        }
        filtered_states: list[str] = []
        seen_tokens: set[str] = set()
        for state in states:
            normalized_state = self._normalized_state_token(state)
            if not normalized_state or normalized_state in non_queue_tokens:
                continue
            if normalized_state in seen_tokens:
                continue
            seen_tokens.add(normalized_state)
            filtered_states.append(state)
        return filtered_states

    @staticmethod
    def _normalized_state_token(value: str) -> str:
        return alphanumeric_lower_text(value)

    def _configured_state_value(self, state_key: str) -> str:
        return getattr(
            self._config,
            f'{state_key}_state',
            self._STATE_VALUE_DEFAULTS[state_key],
        )
