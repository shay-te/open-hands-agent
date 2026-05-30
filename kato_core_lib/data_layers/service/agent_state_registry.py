from __future__ import annotations

from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from kato_core_lib.data_layers.data.fields import ImplementationFields, PullRequestFields, StatusFields, TaskFields
from kato_core_lib.helpers.pull_request_context_utils import (
    build_pull_request_context,
    pull_request_context_key,
)
from kato_core_lib.helpers.text_utils import normalized_text


class AgentStateRegistry(object):
    def __init__(self) -> None:
        self.pull_request_context_map: dict[str, list[dict[str, str]]] = {}
        self.pull_request_task_map: dict[tuple[str, str], str] = {}
        self.processed_task_map: dict[str, dict[str, object]] = {}
        self.processed_review_comment_map: dict[tuple[str, str], set[str]] = {}

    def remember_pull_request_context(
        self,
        pull_request: dict[str, str],
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> None:
        pull_request_id = pull_request[PullRequestFields.ID]
        context = build_pull_request_context(
            pull_request[PullRequestFields.REPOSITORY_ID],
            branch_name,
            agent_session_id,
            task_id,
            task_summary,
            normalized_text(pull_request.get(PullRequestFields.TITLE, '')),
        )
        existing_contexts = self.pull_request_context_map.setdefault(pull_request_id, [])
        if pull_request_context_key(context) not in {
            pull_request_context_key(existing_context)
            for existing_context in existing_contexts
        }:
            existing_contexts.append(context)
        normalized_task_id = str(task_id or '').strip()
        if normalized_task_id:
            self.pull_request_task_map[
                (
                    str(pull_request[PullRequestFields.REPOSITORY_ID]).strip(),
                    pull_request_id,
                )
            ] = normalized_task_id

    def pull_request_context(
        self,
        pull_request_id: str,
        repository_id: str = '',
    ) -> dict[str, str] | None:
        pull_request_contexts = self.pull_request_context_map.get(pull_request_id, [])
        if repository_id:
            pull_request_contexts = [
                context
                for context in pull_request_contexts
                if context[PullRequestFields.REPOSITORY_ID] == repository_id
            ]
        if not pull_request_contexts:
            return None
        if len(pull_request_contexts) > 1:
            raise ValueError(
                f'ambiguous pull request id across repositories: {pull_request_id}'
            )
        return pull_request_contexts[0]

    def mark_task_processed(self, task_id: str, pull_requests: list[dict[str, str]]) -> None:
        self.processed_task_map[str(task_id)] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                dict(pull_request)
                for pull_request in pull_requests
                if isinstance(pull_request, dict)
            ],
        }

    def is_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> bool:
        key = (str(repository_id), str(pull_request_id))
        return str(comment_id) in self.processed_review_comment_map.get(key, set())

    def mark_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> None:
        key = (str(repository_id), str(pull_request_id))
        self.processed_review_comment_map.setdefault(key, set()).add(str(comment_id))

    def tracked_task_ids(self) -> set[str]:
        """Return all task IDs that have tracked pull-request contexts."""
        task_ids: set[str] = set()
        for task_id in self.pull_request_task_map.values():
            if task_id:
                task_ids.add(str(task_id))
        for contexts in self.pull_request_context_map.values():
            for context in contexts:
                task_id = str(context.get(TaskFields.ID, '') or '').strip()
                if task_id:
                    task_ids.add(task_id)
        return task_ids

    def session_ids_for_task(self, task_id: str) -> list[str]:
        """Return all session IDs stored in PR contexts for the given task."""
        normalized = str(task_id or '').strip()
        session_ids: list[str] = []
        seen: set[str] = set()
        for contexts in self.pull_request_context_map.values():
            for context in contexts:
                if str(context.get(TaskFields.ID, '') or '').strip() != normalized:
                    continue
                agent_session_id = fix_session_id(context.get(ImplementationFields.AGENT_SESSION_ID))
                if agent_session_id and agent_session_id not in seen:
                    seen.add(agent_session_id)
                    session_ids.append(agent_session_id)
        return session_ids

    def forget_task(self, task_id: str) -> None:
        """Remove all registry entries associated with the given task."""
        normalized = str(task_id or '').strip()
        if not normalized:
            return

        # Remove PR context entries that belong exclusively to this task.
        pr_ids_to_remove: list[str] = []
        for pr_id, contexts in self.pull_request_context_map.items():
            remaining = [
                ctx for ctx in contexts
                if str(ctx.get(TaskFields.ID, '') or '').strip() != normalized
            ]
            if not remaining:
                pr_ids_to_remove.append(pr_id)
            else:
                self.pull_request_context_map[pr_id] = remaining
        for pr_id in pr_ids_to_remove:
            del self.pull_request_context_map[pr_id]

        # Remove PR task-map entries for this task.
        stale_keys = [
            key for key, tid in self.pull_request_task_map.items()
            if str(tid or '').strip() == normalized
        ]
        for key in stale_keys:
            del self.pull_request_task_map[key]

    def task_id_for_pull_request(
        self,
        pull_request_id: str,
        repository_id: str,
    ) -> str:
        key = (str(repository_id).strip(), str(pull_request_id).strip())
        task_id = self.pull_request_task_map.get(key, '')
        if task_id:
            return task_id
        for processed_task_id, processed_task in self.processed_task_map.items():
            pull_requests = processed_task.get(PullRequestFields.PULL_REQUESTS, [])
            if not isinstance(pull_requests, list):
                continue
            for pull_request in pull_requests:
                if not isinstance(pull_request, dict):
                    continue
                tracked_pull_request_id = str(
                    pull_request.get(PullRequestFields.ID, '') or ''
                ).strip()
                tracked_repository_id = str(
                    pull_request.get(PullRequestFields.REPOSITORY_ID, '') or ''
                ).strip()
                if (
                    tracked_pull_request_id == str(pull_request_id).strip()
                    and tracked_repository_id == str(repository_id).strip()
                ):
                    self.pull_request_task_map[key] = str(processed_task_id)
                    return str(processed_task_id)
        return ''
