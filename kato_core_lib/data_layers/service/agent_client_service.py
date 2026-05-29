from __future__ import annotations

from typing import TYPE_CHECKING

from core_lib.data_layers.service.service import Service

from kato_core_lib.helpers.retry_utils import retry_count
from kato_core_lib.helpers.logging_utils import configure_logger

if TYPE_CHECKING:
    from agent_provider_contracts.agent_provider_contracts.agent_provider import (
        AgentProvider,
    )


class _AgentClientService(Service):
    """Shared base for services that wrap the active agent client.

    Holds the common client handle, logger setup, retry budget, and the
    connection/model-access/stop-all delegations that ImplementationService
    and TestingService both expose unchanged.
    """

    def __init__(self, client: 'AgentProvider') -> None:
        self._client = client
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def max_retries(self) -> int:
        return retry_count(getattr(self._client, 'max_retries', 1))

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def validate_model_access(self) -> None:
        self._client.validate_model_access()

    def stop_all_conversations(self) -> None:
        self._client.stop_all_conversations()
