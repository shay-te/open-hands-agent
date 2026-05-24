from __future__ import annotations

from typing import TYPE_CHECKING

from core_lib.data_layers.service.service import Service

from kato_core_lib.helpers.retry_utils import retry_count
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from kato_core_lib.helpers.logging_utils import configure_logger

if TYPE_CHECKING:
    from agent_provider_contracts.agent_provider_contracts.agent_provider import (
        AgentProvider,
    )


class ImplementationService(Service):
    """Wrap the active agent client for implementation and review-comment fixing."""

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

    def delete_conversation(self, conversation_id: str) -> None:
        self._client.delete_conversation(conversation_id)

    def stop_all_conversations(self) -> None:
        self._client.stop_all_conversations()

    def implement_task(
        self,
        task: Task,
        agent_session_id: str = '',
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('delegating implementation for task %s', task.id)
        return self._client.implement_task(
            task,
            agent_session_id,
            prepared_task=prepared_task,
        )

    def fix_review_comment(
        self,
        comment,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        return self._client.fix_review_comment(
            comment,
            branch_name,
            agent_session_id,
            task_id=task_id,
            task_summary=task_summary,
        )

    def fix_review_comments(
        self,
        comments,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
    ) -> dict[str, str | bool]:
        """Address every comment in ``comments`` via the agent client.

        Newer clients (Claude, OpenHands) implement ``fix_review_comments``
        natively and address the whole batch in one agent spawn — that's
        the efficiency win. Older clients (or test stubs) that only
        expose ``fix_review_comment`` get auto-fanned-out: one client call
        per comment, results merged. Behaviour-preserving back-compat for
        anyone who wrote a custom agent client against the old API.

        ``mode='answer'`` routes the agent through the question-answering
        prompt — service caller skips the push step in that case.
        """
        if hasattr(self._client, 'fix_review_comments'):
            return self._client.fix_review_comments(
                comments,
                branch_name,
                agent_session_id=agent_session_id,
                task_id=task_id,
                task_summary=task_summary,
                mode=mode,
            )
        # Fallback: iterate. Loses the batching efficiency, but
        # preserves correctness — every comment still gets addressed.
        # Older clients without ``mode`` support fall through to fix
        # mode silently; the service-level skip-push branch still
        # applies, so worst case the agent makes an unnecessary
        # commit that nobody pushes.
        last_result: dict[str, str | bool] = {}
        for comment in comments:
            last_result = self._client.fix_review_comment(
                comment,
                branch_name,
                agent_session_id,
                task_id=task_id,
                task_summary=task_summary,
            )
        return last_result
