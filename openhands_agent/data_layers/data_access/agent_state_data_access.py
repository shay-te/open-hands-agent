from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from core_lib.data_layers.data_access.data_access import DataAccess

from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import PullRequestFields, StatusFields


class AgentStateDataAccess(DataAccess):
    def __init__(self, file_path: str) -> None:
        self._path = Path(file_path).expanduser()
        self._lock = threading.Lock()

    def validate(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_state(self._empty_state())
            return
        self._read_state()

    def is_task_processed(self, task_id: str) -> bool:
        state = self._read_state()
        return str(task_id) in state['processed_tasks']

    def get_processed_task(self, task_id: str) -> dict:
        state = self._read_state()
        processed_task = state['processed_tasks'].get(str(task_id), {})
        return dict(processed_task) if isinstance(processed_task, dict) else {}

    def mark_task_processed(self, task_id: str, pull_requests: list[dict[str, str]]) -> None:
        def mutate(state: dict) -> None:
            state['processed_tasks'][str(task_id)] = {
                StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
                PullRequestFields.PULL_REQUESTS: self._serialize_pull_requests(pull_requests),
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }

        self._update_state(mutate)

    def remember_pull_request_context(
        self,
        pull_request_id: str,
        repository_id: str,
        branch_name: str,
    ) -> None:
        def mutate(state: dict) -> None:
            contexts = state['pull_request_contexts'].setdefault(str(pull_request_id), [])
            record = {
                PullRequestFields.REPOSITORY_ID: str(repository_id),
                Task.branch_name.key: str(branch_name),
            }
            if record not in contexts:
                contexts.append(record)

        self._update_state(mutate)

    def get_pull_request_contexts(self, pull_request_id: str) -> list[dict[str, str]]:
        state = self._read_state()
        contexts = state['pull_request_contexts'].get(str(pull_request_id), [])
        if not isinstance(contexts, list):
            return []

        normalized_contexts: list[dict[str, str]] = []
        for context in contexts:
            if not isinstance(context, dict):
                continue
            repository_id = str(context.get(PullRequestFields.REPOSITORY_ID, '') or '').strip()
            branch_name = str(context.get(Task.branch_name.key, '') or '').strip()
            if repository_id and branch_name:
                normalized_contexts.append(
                    {
                        PullRequestFields.REPOSITORY_ID: repository_id,
                        Task.branch_name.key: branch_name,
                    }
                )
        return normalized_contexts

    def _update_state(self, mutate) -> None:
        with self._lock:
            state = self._read_state()
            mutate(state)
            self._write_state(state)

    def _read_state(self) -> dict:
        if not self._path.exists():
            return self._empty_state()

        try:
            payload = json.loads(self._path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError(f'invalid agent state file: {self._path}') from exc

        return self._normalize_state(payload)

    def _write_state(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                'w',
                encoding='utf-8',
                dir=self._path.parent,
                prefix=f'{self._path.name}.',
                suffix='.tmp',
                delete=False,
            ) as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, self._path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _empty_state() -> dict:
        return {
            'processed_tasks': {},
            'pull_request_contexts': {},
            'processed_review_comments': {},
        }

    def _normalize_state(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError(f'invalid agent state file: {self._path}')

        processed_tasks = payload.get('processed_tasks', {})
        pull_request_contexts = payload.get('pull_request_contexts', {})
        processed_review_comments = payload.get('processed_review_comments', {})
        if (
            not isinstance(processed_tasks, dict)
            or not isinstance(pull_request_contexts, dict)
            or not isinstance(processed_review_comments, dict)
        ):
            raise ValueError(f'invalid agent state file: {self._path}')

        return {
            'processed_tasks': processed_tasks,
            'pull_request_contexts': pull_request_contexts,
            'processed_review_comments': processed_review_comments,
        }

    @staticmethod
    def _serialize_pull_requests(pull_requests: list[dict[str, str]]) -> list[dict[str, str]]:
        serialized_pull_requests: list[dict[str, str]] = []
        for pull_request in pull_requests:
            if not isinstance(pull_request, dict):
                continue
            serialized_pull_requests.append(
                {
                    key: str(value)
                    for key, value in pull_request.items()
                    if value is not None
                }
            )
        return serialized_pull_requests

    def list_pull_request_contexts(self) -> list[dict[str, str]]:
        state = self._read_state()
        contexts: list[dict[str, str]] = []
        for pull_request_id, pull_request_contexts in state['pull_request_contexts'].items():
            if not isinstance(pull_request_contexts, list):
                continue
            for context in pull_request_contexts:
                if not isinstance(context, dict):
                    continue
                repository_id = str(context.get(PullRequestFields.REPOSITORY_ID, '') or '').strip()
                branch_name = str(context.get(Task.branch_name.key, '') or '').strip()
                if repository_id and branch_name:
                    contexts.append(
                        {
                            PullRequestFields.ID: str(pull_request_id),
                            PullRequestFields.REPOSITORY_ID: repository_id,
                            Task.branch_name.key: branch_name,
                        }
                    )
        return contexts

    def is_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> bool:
        state = self._read_state()
        repository_comments = state['processed_review_comments'].get(str(repository_id), {})
        if not isinstance(repository_comments, dict):
            return False
        comment_ids = repository_comments.get(str(pull_request_id), [])
        if not isinstance(comment_ids, list):
            return False
        return str(comment_id) in {str(value) for value in comment_ids}

    def mark_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> None:
        def mutate(state: dict) -> None:
            repository_comments = state['processed_review_comments'].setdefault(
                str(repository_id),
                {},
            )
            comment_ids = repository_comments.setdefault(str(pull_request_id), [])
            normalized_comment_id = str(comment_id)
            if normalized_comment_id not in comment_ids:
                comment_ids.append(normalized_comment_id)

        self._update_state(mutate)
