from omegaconf import DictConfig

from core_lib.data_layers.service.service import Service

from kato.data_layers.data_access.task_data_access import TaskDataAccess
from kato.helpers.text_utils import alphanumeric_lower_text, normalized_text


class TaskStateService(Service):
    """Wrap ticket-system task state transitions."""
    _STATE_FIELD_DEFAULTS = {
        'progress': 'review',
        'review': 'State',
        'open': 'progress',
    }
    _STATE_VALUE_DEFAULTS = {
        'progress': 'In Progress',
        'review': 'In Review',
    }

    def __init__(self, config: DictConfig, task_data_access: TaskDataAccess) -> None:
        self._config = config
        self._task_data_access = task_data_access

    def move_task_to_in_progress(self, issue_id: str) -> None:
        self._move_task_to_configured_state(issue_id, 'progress')

    def move_task_to_review(self, issue_id: str) -> None:
        self._move_task_to_configured_state(issue_id, 'review')

    def move_task_to_open(self, issue_id: str) -> None:
        self._task_data_access.move_task_to_state(
            issue_id,
            self._configured_state_field('open'),
            self._configured_open_state(),
        )

    def _move_task_to_configured_state(self, issue_id: str, state_key: str) -> None:
        self._task_data_access.move_task_to_state(
            issue_id,
            self._configured_state_field(state_key),
            self._configured_state_value(state_key),
        )

    def _configured_state_field(self, state_key: str) -> str:
        config_key = f'{state_key}_state_field'
        default = self._STATE_FIELD_DEFAULTS[state_key]
        if default in self._STATE_FIELD_DEFAULTS:
            default = self._configured_state_field(default)
        return getattr(self._config, config_key, default)

    def _configured_state_value(self, state_key: str) -> str:
        return getattr(
            self._config,
            f'{state_key}_state',
            self._STATE_VALUE_DEFAULTS[state_key],
        )

    def _configured_open_state(self) -> str:
        explicit_open_state = normalized_text(getattr(self._config, 'open_state', ''))
        if explicit_open_state:
            return explicit_open_state
        configured_issue_states = self._configured_issue_states()
        if configured_issue_states:
            return configured_issue_states[0]
        return 'Open'

    def _configured_issue_states(self) -> list[str]:
        if hasattr(self._config, 'issue_states'):
            issue_states = self._config.issue_states
            if isinstance(issue_states, str):
                return [state.strip() for state in issue_states.split(',') if state.strip()]
            return [str(state).strip() for state in issue_states if str(state).strip()]
        return [self._config.issue_state]

    @staticmethod
    def _normalized_state_token(value: str) -> str:
        return alphanumeric_lower_text(value)
