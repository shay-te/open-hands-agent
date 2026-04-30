from __future__ import annotations

import os
import json
import time
from uuid import UUID

from kato.client.openrouter import OpenRouterClient
from kato.client.retrying_client_base import RetryingClientBase
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from kato.helpers import agent_prompt_utils
from kato.helpers.kato_result_utils import build_openhands_result
from kato.helpers.kato_config_utils import is_openrouter_model
from kato.helpers.logging_utils import configure_logger
from kato.helpers.retry_utils import run_with_retry
from kato.helpers.task_context_utils import PreparedTaskContext
from kato.helpers.text_utils import (
    condensed_text,
    normalized_lower_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)


logger = configure_logger(__name__)


class KatoClient(RetryingClientBase):
    _APP_CONVERSATIONS_PATH = '/api/v1/app-conversations'
    _SETTINGS_PATH = '/api/settings'
    _START_TASKS_PATH = '/api/v1/app-conversations/start-tasks'
    _EVENTS_PATH_TEMPLATE = '/api/v1/conversation/{conversation_id}/events/search'
    _MODEL_SMOKE_TEST_TITLE = 'Kato model validation'
    _MODEL_SMOKE_TEST_PROMPT = (
        'Reply with exactly hi and use the finish tool immediately. '
        'Do not inspect files or run shell commands.'
    )
    _START_TASK_READY = 'READY'
    _START_TASK_ERROR = 'ERROR'
    _ACTIVE_EXECUTION_STATUSES = {
        '',
        'created',
        'queued',
        'running',
        'starting',
        'working',
    }
    _FAILED_EXECUTION_STATUSES = {
        'cancelled',
        'error',
        'failed',
    }
    _DEFAULT_POLL_INTERVAL_SECONDS = 2.0
    _DEFAULT_MAX_POLL_ATTEMPTS = 900
    _SHELL_TOOL_NAMES = {
        'bash',
        'execute_bash',
        'run',
        'run_command',
        'shell',
    }
    _MESSAGE_HIGHLIGHT_PREFIXES = (
        'Running ',
        'Ran ',
    )
    _RETRYABLE_START_TASK_ERROR_DETAILS = (
        'sandbox entered error state',
        'sandbox failed to boot',
    )

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = 3,
        llm_settings: dict[str, str] | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        max_poll_attempts: int = _DEFAULT_MAX_POLL_ATTEMPTS,
        model_smoke_test_enabled: bool = False,
    ) -> None:
        super().__init__(base_url, api_key, timeout=300, max_retries=max_retries)
        self._session_api_key = api_key
        self._llm_settings = dict(llm_settings or {})
        self._poll_interval_seconds = max(0.1, float(poll_interval_seconds or 0))
        self._max_poll_attempts = max(1, int(max_poll_attempts or 0))
        self._model_smoke_test_enabled = bool(model_smoke_test_enabled)
        self._model_access_smoke_test_ran = False

    def validate_connection(self) -> None:
        response = self._get_with_retry(f'{self._APP_CONVERSATIONS_PATH}/count')
        response.raise_for_status()
        self._sync_runtime_settings()
        self._validate_model_smoke_test()

    def validate_model_access(self) -> None:
        llm_model = text_from_mapping(self._llm_settings, 'llm_model')
        if not llm_model:
            return
        if is_openrouter_model(llm_model):
            self._validate_openrouter_connection(llm_model)
        self._validate_model_access_smoke_test()

    def implement_task(
        self,
        task: Task,
        session_id: str = '',
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting implementation for task %s', task.id)
        # Task work always starts in a fresh Kato conversation so each
        # task gets its own thread and pull request history.
        result = self._run_prompt_result(
            prompt=self._build_implementation_prompt(task, prepared_task),
            title=self._task_conversation_title(task),
            branch_name=self._task_branch_name(task, prepared_task),
            default_commit_message=f'Implement {task.id}',
        )
        self.logger.info(
            'implementation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def test_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting testing validation for task %s', task.id)
        result = self._run_prompt_result(
            prompt=self._build_testing_prompt(task, prepared_task),
            title=self._task_conversation_title(task, suffix=' [testing]'),
        )
        self.logger.info(
            'testing validation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        result = self._run_prompt_result(
            prompt=self._build_review_prompt(comment, branch_name),
            title=self._review_conversation_title(
                comment,
                task_id=task_id,
                task_summary=task_summary,
            ),
            session_id=session_id,
            branch_name=branch_name,
            default_commit_message='Address review comments',
        )
        self.logger.info(
            'review fix finished for pull request %s comment %s with success=%s',
            comment.pull_request_id,
            comment.comment_id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    @classmethod
    def _task_conversation_title(cls, task: Task, suffix: str = '') -> str:
        return agent_prompt_utils.task_conversation_title(task, suffix)

    @classmethod
    def _review_conversation_title(
        cls,
        comment: ReviewComment,
        task_id: str = '',
        task_summary: str = '',
    ) -> str:
        return agent_prompt_utils.review_conversation_title(
            comment,
            task_id=task_id,
            task_summary=task_summary,
        )

    def _build_implementation_prompt(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> str:
        repository_scope = self._repository_scope_text(task, prepared_task)
        finish_instructions = self._finish_tool_instructions_text()
        return (
            f'Implement task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            'When you finish, use the finish tool.\n'
            '- Put the text that should become the pull request description in summary.\n'
            '- Put any extra implementation details in message.\n'
            '- Make the smallest possible change needed to satisfy the task.\n'
            '- Prefer editing only the exact lines or blocks that need to change.\n'
            '- Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            '- Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            '- Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            '- If no dedicated tests are defined for this task, do not invent new ones; just finish after saving the change.\n'
            f'{finish_instructions}\n\n'
            'The summary must list every changed file and, under each file name, add a short explanation of what changed.\n'
            'Use this format inside summary:\n'
            'Files changed:\n'
            '- path/to/file.ext\n'
            '  Short explanation.\n'
            '- another/file.ext\n'
            '  Short explanation.\n'
        )

    def _build_testing_prompt(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> str:
        repository_scope = self._repository_scope_text(task, prepared_task)
        finish_instructions = self._finish_tool_instructions_text()
        return (
            f'Validate the implementation for task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            'Make the smallest possible change needed for the validation work.\n'
            'Prefer editing only the exact lines or blocks that need to change.\n'
            'Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            'Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            'Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            'Do not create a pull request.\n'
            'When you finish, use the finish tool.\n'
            '- Put the text that should become the testing report in summary.\n'
            '- Put any extra testing details in message.\n'
            '- If no dedicated tests are defined or available, do not invent new ones; just report that no testing was defined and finish after saving the change.\n'
            f'{finish_instructions}\n'
        )

    @staticmethod
    def _finish_tool_instructions_text() -> str:
        return (
            '- Do not report success until all intended changes are saved in the repository worktree.\n'
            '- Create validation_report.md in the repository root when the task succeeds.\n'
            '- Write the report that the orchestration layer will use as the pull request description.\n'
            '- Keep the report concise but explanatory.\n'
            '- If you have validation results, include them in validation_report.md too.\n'
            '- Do not commit or stage validation_report.md; the orchestration layer will read and remove it before opening the pull request.\n'
            '- Do not pass extra finish-tool arguments beyond the supported fields.'
        )

    @staticmethod
    def _task_branch_name(task: Task, prepared_task: PreparedTaskContext | None = None) -> str:
        return agent_prompt_utils.task_branch_name(task, prepared_task)

    @staticmethod
    def _repository_scope_text(
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> str:
        return agent_prompt_utils.repository_scope_text(task, prepared_task)

    @classmethod
    def _build_review_prompt(cls, comment: ReviewComment, branch_name: str) -> str:
        repository_context = agent_prompt_utils.review_repository_context(comment)
        review_context = cls._review_comment_context_text(comment)
        return (
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'Comment by {comment.author}: {comment.body}'
            f'{review_context}\n\n'
            f'{cls._execution_guardrails_text()}\n\n'
            'When you finish, use the finish tool.\n'
            '- Put a short description of what changed in summary.\n'
            '- Put any extra details in message.\n'
            '- Make the smallest possible change needed to address the review comment.\n'
            '- Prefer editing only the exact lines or blocks that need to change.\n'
            '- Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            '- Do not report success until all intended changes are saved in the repository worktree.\n'
            '- Do not pass extra finish-tool arguments beyond the supported fields.\n'
        )

    @staticmethod
    def _security_guardrails_text() -> str:
        return agent_prompt_utils.security_guardrails_text()

    @classmethod
    def _execution_guardrails_text(cls) -> str:
        return f'{cls._security_guardrails_text()}\n\n{cls._tool_guardrails_text()}'

    @staticmethod
    def _tool_guardrails_text() -> str:
        return (
            'Tool guardrails:\n'
            '- Prefer shell commands like rg, sed -n, and cat for quick file reads.\n'
            '- Prefer shell-based reads before editing so you know the exact surrounding text.\n'
            '- If you use the file_editor tool, always include its required command field.\n'
            '- For text replacement, use file_editor with command "str_replace" plus path, old_str, and new_str.\n'
            '- For file reads through file_editor, use command "view".\n'
            '- For insertions through file_editor, use command "insert".\n'
            '- Never call file_editor with only path, summary, security_risk, old_str, or new_str.\n'
            '- Never use create_pr or any pull-request or merge-request creation tool.\n'
            '- Do not call GitHub, GitLab, or Bitbucket APIs to publish a pull request yourself.\n'
            '- Do not run git checkout, git switch, git branch, git pull, or git push unless the orchestration layer explicitly asks you to edit an already-checked-out branch.'
        )

    @staticmethod
    def _normalized_payload(response) -> dict:
        payload = response.json() or {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _normalized_items_payload(response) -> list[dict]:
        payload = response.json() or []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _run_prompt(
        self,
        prompt: str,
        title: str,
        session_id: str = '',
    ) -> dict[str, str | bool]:
        conversation_id = self._start_conversation(prompt, title, session_id)
        payload = self._wait_for_conversation_result(conversation_id, title)
        payload[ImplementationFields.SESSION_ID] = conversation_id
        return payload

    def delete_conversation(self, conversation_id: str) -> None:
        self._delete_conversation(conversation_id)

    def _delete_conversation(self, conversation_id: str) -> None:
        try:
            response = self._delete(f'/api/conversations/{conversation_id}')
            response.raise_for_status()
        except Exception as exc:
            self.logger.warning(
                'failed to delete conversation %s after completion; '
                'agent-server container may need manual cleanup: %s',
                conversation_id,
                exc,
            )

    def stop_all_conversations(self) -> None:
        """Delete all conversations to stop and remove their agent-server containers.

        Called on shutdown to ensure no containers are left running after the process exits.
        """
        self.logger.info('stopping all conversations to remove agent-server containers')
        conversations = self._shutdown_conversations()
        if conversations is None:
            return
        for conversation in conversations:
            conversation_id = text_from_mapping(conversation, 'id')
            if conversation_id:
                self._delete_conversation(conversation_id)
        self._wait_for_conversations_to_stop()

    def _shutdown_conversations(self) -> list[dict] | None:
        try:
            response = self._get(self._APP_CONVERSATIONS_PATH)
            response.raise_for_status()
            return self._normalized_items_payload(response)
        except Exception as exc:
            self.logger.warning(
                'failed to list conversations for shutdown cleanup; '
                'skipping remaining container removal: %s',
                exc,
            )
            return None

    def _wait_for_conversations_to_stop(self) -> None:
        for attempt in range(self._max_poll_attempts):
            conversations = self._shutdown_conversations()
            if conversations is None:
                return
            if not conversations:
                return
            if attempt >= self._max_poll_attempts - 1:
                break
            self.logger.info(
                'waiting for %s OpenHands conversations to stop during shutdown',
                len(conversations),
            )
            time.sleep(self._poll_interval_seconds)
        self.logger.warning(
            'conversation cleanup did not finish after %s polls; '
            'some agent-server containers may need manual cleanup',
            self._max_poll_attempts,
        )

    def _run_prompt_result(
        self,
        *,
        prompt: str,
        title: str,
        session_id: str = '',
        branch_name: str = '',
        default_commit_message: str | None = None,
    ) -> dict[str, str | bool]:
        payload = self._run_prompt(
            prompt=prompt,
            title=title,
            session_id=session_id,
        )
        return build_openhands_result(
            payload,
            branch_name=branch_name,
            default_commit_message=default_commit_message,
        )

    def _sync_runtime_settings(self) -> None:
        payload = self._settings_update_payload()
        if not payload:
            return
        response = self._post_with_retry(self._SETTINGS_PATH, json=payload)
        response.raise_for_status()

    def _validate_model_smoke_test(self) -> None:
        if not self._model_smoke_test_enabled:
            return
        self._validate_model_access_smoke_test()

    def _validate_model_access_smoke_test(self) -> None:
        if self._model_access_smoke_test_ran:
            return
        self._run_model_access_validation()
        self._model_access_smoke_test_ran = True

    def _run_model_access_validation(self) -> None:
        llm_model = text_from_mapping(self._llm_settings, 'llm_model')
        if not llm_model:
            return

        self.logger.info('running Kato model access validation')
        result = self._run_prompt_result(
            prompt=self._MODEL_SMOKE_TEST_PROMPT,
            title=self._MODEL_SMOKE_TEST_TITLE,
        )
        if not result.get(ImplementationFields.SUCCESS, False):
            summary = condensed_text(text_from_mapping(result, 'summary'))
            detail = f': {summary}' if summary else ''
            raise RuntimeError(
                f'Kato model validation returned a failure result{detail}'
            )

    def _validate_openrouter_connection(self, llm_model: str) -> None:
        api_key = self._openrouter_api_key()
        if not api_key:
            raise RuntimeError('OpenRouter model validation requires LLM_API_KEY')
        base_url = text_from_mapping(self._llm_settings, 'llm_base_url')
        client = OpenRouterClient(base_url, api_key, self.max_retries)
        client.validate_model_available(llm_model)

    @staticmethod
    def _openrouter_api_key() -> str:
        return (
            normalized_text(os.environ.get('LLM_API_KEY', ''))
            or normalized_text(os.environ.get('OPENHANDS_LLM_API_KEY', ''))
        )

    def _settings_update_payload(self) -> dict[str, str]:
        llm_model = text_from_mapping(self._llm_settings, 'llm_model')
        if not llm_model:
            return {}

        payload = {'llm_model': llm_model}
        llm_base_url = text_from_mapping(self._llm_settings, 'llm_base_url')
        if llm_base_url:
            payload['llm_base_url'] = llm_base_url
        return payload

    def _start_conversation(self, prompt: str, title: str, session_id: str = '') -> str:
        request_body = {
            'title': title,
            'initial_message': {
                'role': 'user',
                'content': [{'text': prompt}],
            },
        }
        parent_conversation_id = self._normalized_uuid(session_id)
        if parent_conversation_id:
            request_body['parent_conversation_id'] = parent_conversation_id

        try:
            conversation_id = run_with_retry(
                lambda: self._start_conversation_once(request_body),
                self.max_retries,
                operation_name=self._retry_operation_name('POST', self._APP_CONVERSATIONS_PATH),
            )
        except TimeoutError as exc:
            if self._is_retryable_start_task_error(str(exc)):
                raise RuntimeError(str(exc)) from exc
            raise
        self._update_conversation_title(conversation_id, title)
        return conversation_id

    def _start_conversation_once(self, request_body: dict[str, object]) -> str:
        response = self._post_with_retry(
            self._APP_CONVERSATIONS_PATH,
            json=request_body,
        )
        response.raise_for_status()
        start_task = self._normalized_payload(response)
        return self._wait_for_started_conversation_id(start_task)

    def _update_conversation_title(self, conversation_id: str, title: str) -> None:
        normalized_title = condensed_text(title)
        if not conversation_id or not normalized_title:
            return
        response = self._patch_with_retry(
            f'/api/conversations/{conversation_id}',
            headers={'X-Session-API-Key': self._session_api_key},
            json={'title': normalized_title},
        )
        response.raise_for_status()

    def _wait_for_started_conversation_id(self, start_task: dict) -> str:
        start_task_id = text_from_mapping(start_task, 'id')
        if not start_task_id:
            raise ValueError('kato start task response did not include an id')

        for attempt in range(self._max_poll_attempts):
            task_info = self._get_start_task(start_task_id)
            status = text_from_mapping(task_info, 'status').upper()
            if status == self._START_TASK_READY:
                conversation_id = text_from_mapping(task_info, 'app_conversation_id')
                if conversation_id:
                    return conversation_id
                raise ValueError('kato start task became ready without a conversation id')
            if status == self._START_TASK_ERROR:
                detail = text_from_mapping(task_info, 'detail')
                if self._is_retryable_start_task_error(detail):
                    raise TimeoutError(detail or 'kato failed to start a conversation')
                raise RuntimeError(detail or 'kato failed to start a conversation')
            self._sleep_before_next_poll(attempt)

        raise TimeoutError(
            f'kato did not start a conversation after {self._max_poll_attempts} polls'
        )

    @classmethod
    def _is_retryable_start_task_error(cls, detail: str) -> bool:
        normalized_detail = normalized_lower_text(detail)
        if not normalized_detail:
            return False
        return any(
            error_detail in normalized_detail
            for error_detail in cls._RETRYABLE_START_TASK_ERROR_DETAILS
        )

    def _get_start_task(self, start_task_id: str) -> dict:
        response = self._get_with_retry(
            self._START_TASKS_PATH,
            params={'ids': [start_task_id]},
        )
        response.raise_for_status()
        tasks = self._normalized_items_payload(response)
        if tasks:
            return tasks[0]
        raise ValueError(f'kato start task not found: {start_task_id}')

    def _wait_for_conversation_result(
        self,
        conversation_id: str,
        conversation_title: str = '',
    ) -> dict[str, str | bool]:
        seen_highlights: set[str] = set()
        highlight_logging_enabled = True
        for attempt in range(self._max_poll_attempts):
            conversation = self._get_conversation(conversation_id)
            execution_status = normalized_lower_text(
                text_from_mapping(conversation, 'execution_status')
            )
            if execution_status in self._FAILED_EXECUTION_STATUSES:
                raise RuntimeError(
                    self._conversation_failure_message(
                        conversation_id,
                        execution_status,
                        conversation,
                    )
                )
            if execution_status not in self._ACTIVE_EXECUTION_STATUSES:
                return self._get_result_payload(conversation_id, conversation_title)
            if highlight_logging_enabled:
                highlight_logging_enabled = self._log_conversation_highlights(
                    conversation_id,
                    conversation_title,
                    seen_highlights,
                )
            self._sleep_before_next_poll(attempt)

        raise TimeoutError(
            f'kato conversation {conversation_id} did not finish after {self._max_poll_attempts} polls'
        )

    def _conversation_failure_message(
        self,
        conversation_id: str,
        execution_status: str,
        conversation: dict,
    ) -> str:
        detail = self._conversation_failure_detail(conversation_id, conversation)
        message = f'kato conversation failed with status: {execution_status}'
        if detail:
            message = f'{message}: {detail}'
        self.logger.error(
            'kato conversation %s failed with status %s%s',
            conversation_id,
            execution_status,
            f': {detail}' if detail else '',
        )
        return message

    def _conversation_failure_detail(self, conversation_id: str, conversation: dict) -> str:
        for key in (
            'detail',
            'error',
            'error_message',
            'message',
            'reason',
        ):
            value = condensed_text(text_from_mapping(conversation, key))
            if value:
                return value

        try:
            events = self._get_conversation_events(conversation_id)
        except Exception as exc:
            self.logger.warning(
                'kato conversation %s failed, but failure events could not be loaded: %s',
                conversation_id,
                exc,
            )
            return ''

        for event in reversed(events):
            detail = self._event_failure_detail(event)
            if detail:
                return detail

        event_summary = self._conversation_failure_event_summary(events)
        if event_summary:
            return event_summary
        return ''

    def _conversation_failure_event_summary(self, events: list[object]) -> str:
        summaries: list[str] = []
        for event in events[:5]:
            highlight = self._event_highlight_text(event)
            if not highlight:
                highlight = self._event_failure_detail(event)
            if not highlight:
                continue
            summaries.append(highlight)
        if not summaries:
            return ''
        return f'recent Kato activity: {"; ".join(summaries)}'

    def _event_failure_detail(self, event: object) -> str:
        if not isinstance(event, dict):
            return ''

        for key in (
            'detail',
            'error',
            'error_message',
            'message',
            'reason',
            'summary',
            'stderr',
            'stdout',
            'traceback',
        ):
            value = condensed_text(text_from_mapping(event, key))
            if value:
                return value

        message_text = self._assistant_message_text(event)
        if message_text:
            return condensed_text(message_text)
        return ''

    def _get_conversation(self, conversation_id: str) -> dict:
        response = self._get_with_retry(
            self._APP_CONVERSATIONS_PATH,
            params={'ids': [conversation_id]},
        )
        response.raise_for_status()
        conversations = self._normalized_items_payload(response)
        if conversations:
            return conversations[0]
        raise ValueError(f'kato conversation not found: {conversation_id}')

    def _get_result_payload(
        self,
        conversation_id: str,
        conversation_title: str = '',
    ) -> dict[str, str | bool]:
        events = self._get_conversation_events(conversation_id)

        for candidate_events in (events, list(reversed(events))):
            for event in candidate_events:
                parsed_result = self._result_payload_from_event(event)
                if parsed_result is not None:
                    return parsed_result
        fallback_summary = condensed_text(conversation_title) or conversation_id
        self.logger.warning(
            'kato conversation %s finished without a parseable result; '
            'falling back to title %s',
            conversation_id,
            fallback_summary,
        )
        return build_openhands_result(
            None,
            summary_fallback=fallback_summary,
            default_success=True,
        )

    def _get_conversation_events(self, conversation_id: str) -> list[object]:
        response = self._get_with_retry(
            self._EVENTS_PATH_TEMPLATE.format(conversation_id=conversation_id),
            params={'limit': 100, 'sort_order': 'TIMESTAMP_DESC'},
        )
        response.raise_for_status()
        payload = self._normalized_payload(response)
        events = payload.get('items', [])
        if not isinstance(events, list):
            raise ValueError('kato events response did not include items')
        return events

    def _log_conversation_highlights(
        self,
        conversation_id: str,
        conversation_title: str,
        seen_highlights: set[str],
    ) -> bool:
        try:
            events = self._get_conversation_events(conversation_id)
        except Exception as exc:
            self.logger.warning(
                'Mission %s: live Kato highlights unavailable; continuing without them: %s',
                conversation_title or conversation_id,
                exc,
            )
            return False

        for event in reversed(events):
            event_key = self._event_highlight_key(event)
            if event_key in seen_highlights:
                continue
            highlight = self._event_highlight_text(event)
            if not highlight:
                seen_highlights.add(event_key)
                continue
            highlight_key = self._event_highlight_log_key(highlight)
            if highlight_key in seen_highlights:
                seen_highlights.add(event_key)
                continue
            seen_highlights.update({event_key, highlight_key})
            self.logger.info(
                'Mission %s: Kato %s',
                conversation_title or conversation_id,
                highlight,
            )
        return True

    @staticmethod
    def _event_highlight_log_key(highlight: str) -> str:
        return f'highlight:{highlight}'

    def _event_highlight_key(self, event: object) -> str:
        if not isinstance(event, dict):
            return str(event)
        event_id = text_from_mapping(event, 'id')
        if event_id:
            return event_id
        parts = [
            text_from_mapping(event, 'kind'),
            text_from_mapping(event, 'source'),
            text_from_mapping(event, 'tool_name'),
            self._assistant_message_text(event),
        ]
        tool_call = event.get('tool_call', {})
        if isinstance(tool_call, dict):
            parts.append(text_from_mapping(tool_call, 'arguments'))
        return '|'.join(parts)

    @classmethod
    def _event_highlight_text(cls, event: object) -> str:
        if not isinstance(event, dict):
            return ''
        action_highlight = cls._action_event_highlight_text(event)
        if action_highlight:
            return action_highlight
        return cls._assistant_message_highlight_text(event)

    @classmethod
    def _action_event_highlight_text(cls, event: dict) -> str:
        if text_from_mapping(event, 'kind') != 'ActionEvent':
            return ''
        if text_from_mapping(event, 'source') != 'agent':
            return ''

        tool_name = text_from_mapping(event, 'tool_name')
        if not tool_name or tool_name == 'finish':
            return ''

        arguments = cls._tool_call_arguments(event)
        if tool_name in cls._SHELL_TOOL_NAMES:
            command = cls._shell_command(arguments)
            if command:
                return f'ran shell command: {cls._truncate(command)}'
            return 'ran a shell command'

        if tool_name == 'file_editor':
            file_command = text_from_mapping(arguments, 'command')
            path = text_from_mapping(arguments, 'path')
            if file_command in {'str_replace', 'insert'} and path:
                return f'edited {path} with {file_command}'
            if file_command == 'view' and path:
                return f'viewed {path}'
            if path:
                return f'used file_editor on {path}'
            return 'used file_editor'

        path = text_from_mapping(arguments, 'path')
        if path:
            return f'used {tool_name} on {path}'
        return f'used {tool_name}'

    @classmethod
    def _assistant_message_highlight_text(cls, event: dict) -> str:
        message_text = cls._assistant_message_text(event)
        if not message_text:
            return ''
        for line in message_text.splitlines():
            stripped = line.strip()
            if stripped.startswith(cls._MESSAGE_HIGHLIGHT_PREFIXES):
                return cls._truncate(stripped)
        return ''

    @staticmethod
    def _tool_call_arguments(event: dict) -> dict[str, object]:
        tool_call = event.get('tool_call', {})
        if not isinstance(tool_call, dict):
            return {}
        arguments_text = text_from_mapping(tool_call, 'arguments')
        if not arguments_text:
            return {}
        try:
            payload = json.loads(arguments_text)
        except json.JSONDecodeError:
            logger.warning(
                'failed to parse OpenHands tool arguments as JSON: %s',
                condensed_text(arguments_text),
            )
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _shell_command(arguments: dict[str, object]) -> str:
        for key in ('command', 'cmd'):
            value = text_from_mapping(arguments, key)
            if value:
                return value
        return ''

    @staticmethod
    def _truncate(text: str, limit: int = 160) -> str:
        normalized = condensed_text(text)
        if len(normalized) <= limit:
            return normalized
        return f'{normalized[: limit - 3].rstrip()}...'

    def _result_payload_from_event(self, event: object) -> dict[str, str | bool] | None:
        finish_payload = self._finish_action_payload(event)
        if finish_payload is not None:
            return finish_payload

        message_text = self._assistant_message_text(event)
        if not message_text:
            return None
        return self._parse_result_json(message_text)

    @staticmethod
    def _finish_action_payload(event: object) -> dict[str, str | bool] | None:
        if not KatoClient._is_finish_action_event(event):
            return None
        parsed_arguments = KatoClient._finish_action_arguments(event)
        summary, message = KatoClient._finish_action_summary(
            event,
            parsed_arguments,
        )
        if not summary and not message:
            return None
        return build_openhands_result(
            parsed_arguments,
            summary_fallback=summary or message,
            default_success=True,
        )

    @staticmethod
    def _is_finish_action_event(event: object) -> bool:
        return (
            isinstance(event, dict)
            and text_from_mapping(event, 'kind') == 'ActionEvent'
            and text_from_mapping(event, 'source') == 'agent'
            and text_from_mapping(event, 'tool_name') == 'finish'
        )

    @staticmethod
    def _finish_action_arguments(event: dict) -> dict[str, str | bool]:
        tool_call = event.get('tool_call', {})
        if not isinstance(tool_call, dict):
            return {}
        arguments = text_from_mapping(tool_call, 'arguments')
        if not arguments:
            return {}
        try:
            payload = json.loads(arguments)
        except json.JSONDecodeError:
            logger.warning(
                'failed to parse OpenHands finish arguments as JSON: %s',
                condensed_text(arguments),
            )
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _finish_action_summary(
        event: dict,
        parsed_arguments: dict[str, str | bool],
    ) -> tuple[str, str]:
        action = event.get('action', {})
        if not isinstance(action, dict):
            action = {}
        summary = normalized_text(
            parsed_arguments.get(Task.summary.key)
            or parsed_arguments.get('summary')
            or action.get('summary')
            or event.get('summary')
            or ''
        )
        message = normalized_text(
            parsed_arguments.get('message')
            or action.get('message')
            or ''
        )
        return summary, message

    @staticmethod
    def _assistant_message_text(event: object) -> str:
        if not isinstance(event, dict):
            return ''
        if text_from_mapping(event, 'kind') != 'MessageEvent':
            return ''
        if text_from_mapping(event, 'source') != 'agent':
            return ''
        llm_message = event.get('llm_message', {})
        if not isinstance(llm_message, dict):
            return ''
        if text_from_mapping(llm_message, 'role') != 'assistant':
            return ''
        content = llm_message.get('content', [])
        if not isinstance(content, list):
            return ''
        texts = [
            text_from_mapping(item, 'text')
            for item in content
            if isinstance(item, dict)
        ]
        return '\n'.join(text for text in texts if text)

    @staticmethod
    def _parse_result_json(message_text: str) -> dict[str, str | bool] | None:
        text = normalized_text(message_text)
        if not text:
            return None

        candidates = [text]
        if '```' in text:
            fenced_blocks = [
                block.strip()
                for block in text.split('```')
                if '{' in block and '}' in block
            ]
            candidates.extend(fenced_blocks)

        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            candidates.append(text[brace_start:brace_end + 1].strip())

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        if candidates:
            logger.warning(
                'failed to parse OpenHands result JSON from message: %s',
                condensed_text(message_text),
            )
        return None

    @staticmethod
    def _normalized_uuid(value: str) -> str:
        normalized_value = normalized_text(value)
        if not normalized_value:
            return ''
        try:
            return UUID(normalized_value).hex
        except (TypeError, ValueError, AttributeError):
            return ''

    def _sleep_before_next_poll(self, attempt: int) -> None:
        if attempt >= self._max_poll_attempts - 1:
            return
        time.sleep(self._poll_interval_seconds)

    @staticmethod
    def _review_comment_context_text(comment: ReviewComment) -> str:
        return agent_prompt_utils.review_comment_context_text(comment)
