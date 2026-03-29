from core_lib.data_layers.service.service import Service

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.data_layers.data.task import Task
from openhands_agent.logging_utils import configure_logger


class TestingService(Service):
    def __init__(self, client: OpenHandsClient) -> None:
        self._client = client
        self.logger = configure_logger(self.__class__.__name__)

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def test_task(self, task: Task) -> dict[str, str | bool]:
        self.logger.info('delegating testing validation for task %s', task.id)
        return self._client.test_task(task)
