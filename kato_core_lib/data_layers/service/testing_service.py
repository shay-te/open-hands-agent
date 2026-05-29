from __future__ import annotations

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_client_service import _AgentClientService
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


class TestingService(_AgentClientService):
    """Delegate testing validation for a task to the active agent client."""

    def test_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('delegating testing validation for task %s', task.id)
        return self._client.test_task(task, prepared_task=prepared_task)
