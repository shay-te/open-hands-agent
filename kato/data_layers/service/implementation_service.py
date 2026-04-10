from __future__ import annotations

from core_lib.data_layers.service.service import Service

from kato.client.kato_client import KatoClient
from kato.helpers.retry_utils import retry_count
from kato.data_layers.data.task import Task
from kato.helpers.task_context_utils import PreparedTaskContext
from kato.helpers.logging_utils import configure_logger


class ImplementationService(Service):
    """Wrap the Kato client for implementation and review-comment fixing."""

    def __init__(self, client: KatoClient) -> None:
        self._client = client
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def max_retries(self) -> int:
        return retry_count(getattr(self._client, 'max_retries', 1))

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def validate_model_access(self) -> None:
        self._client.validate_model_access()

    def delete_conversation(self, conversation_id: str) -> None:
        self._client.delete_conversation(conversation_id)

    def stop_all_conversations(self) -> None:
        self._client.stop_all_conversations()

    def implement_task(
        self,
        task: Task,
        session_id: str = '',
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('delegating implementation for task %s', task.id)
        return self._client.implement_task(
            task,
            session_id,
            prepared_task=prepared_task,
        )

    def fix_review_comment(
        self,
        comment,
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        return self._client.fix_review_comment(
            comment,
            branch_name,
            session_id,
            task_id=task_id,
            task_summary=task_summary,
        )
