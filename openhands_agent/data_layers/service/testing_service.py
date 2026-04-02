from core_lib.data_layers.service.service import Service

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.helpers.retry_utils import retry_count
from openhands_agent.data_layers.data.task import Task
from openhands_agent.helpers.task_context_utils import PreparedTaskContext
from openhands_agent.helpers.logging_utils import configure_logger


class TestingService(Service):
    def __init__(self, client: OpenHandsClient) -> None:
        self._client = client
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def max_retries(self) -> int:
        return retry_count(getattr(self._client, 'max_retries', 1))

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def validate_model_access(self) -> None:
        self._client.validate_model_access()

    def test_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('delegating testing validation for task %s', task.id)
        return self._client.test_task(task, prepared_task=prepared_task)
