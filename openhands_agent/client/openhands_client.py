import json
import time
from uuid import UUID

from openhands_agent.client.retrying_client_base import RetryingClientBase
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)


class OpenHandsClient(RetryingClientBase):
    _APP_CONVERSATIONS_PATH = '/api/v1/app-conversations'
    _SETTINGS_PATH = '/api/settings'
    _START_TASKS_PATH = '/api/v1/app-conversations/start-tasks'
    _EVENTS_PATH_TEMPLATE = '/api/v1/conversation/{conversation_id}/events/search'
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

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = 3,
        llm_settings: dict[str, str] | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        max_poll_attempts: int = _DEFAULT_MAX_POLL_ATTEMPTS,
    ) -> None:
        super().__init__(base_url, api_key, timeout=300, max_retries=max_retries)
        self._llm_settings = dict(llm_settings or {})
        self._poll_interval_seconds = max(0.1, float(poll_interval_seconds or 0))
        self._max_poll_attempts = max(1, int(max_poll_attempts or 0))

    def validate_connection(self) -> None:
        response = self._get_with_retry(f'{self._APP_CONVERSATIONS_PATH}/count')
        response.raise_for_status()
        self._sync_runtime_settings()

    def implement_task(
        self,
        task: Task,
        session_id: str = '',
    ) -> dict[str, str | bool]:
        self.logger.info('requesting implementation for task %s', task.id)
        payload = self._run_prompt(
            prompt=self._build_implementation_prompt(task),
            title=f'{task.id}: {task.summary}',
            session_id=session_id,
        )
        returned_session_id = self._payload_session_id(payload)
        result = {
            Task.branch_name.key: task.branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                f'Implement {task.id}',
            ),
            ImplementationFields.SUCCESS: self._success_flag(payload),
        }
        if returned_session_id:
            result[ImplementationFields.SESSION_ID] = returned_session_id
        self.logger.info(
            'implementation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def test_task(self, task: Task) -> dict[str, str | bool]:
        self.logger.info('requesting testing validation for task %s', task.id)
        payload = self._run_prompt(
            prompt=self._build_testing_prompt(task),
            title=f'Test {task.id}: {task.summary}',
        )
        result = {
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.SUCCESS: self._success_flag(payload),
        }
        returned_session_id = self._payload_session_id(payload)
        if returned_session_id:
            result[ImplementationFields.SESSION_ID] = returned_session_id
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
    ) -> dict[str, str | bool]:
        self.logger.info(
            'requesting review fix for pull request %s comment %s',
            comment.pull_request_id,
            comment.comment_id,
        )
        payload = self._run_prompt(
            prompt=self._build_review_prompt(comment, branch_name),
            title=f'Fix review comment {comment.comment_id}',
            session_id=session_id,
        )
        returned_session_id = self._payload_session_id(payload)
        result = {
            Task.branch_name.key: branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                'Address review comments',
            ),
            ImplementationFields.SUCCESS: self._success_flag(payload),
        }
        if returned_session_id:
            result[ImplementationFields.SESSION_ID] = returned_session_id
        self.logger.info(
            'review fix finished for pull request %s comment %s with success=%s',
            comment.pull_request_id,
            comment.comment_id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def _build_implementation_prompt(self, task: Task) -> str:
        repository_scope = self._repository_scope_text(task)
        return (
            f'Implement task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            f'{self._tool_guardrails_text()}\n\n'
            'When you finish, use the finish tool.\n'
            '- Put the pull request description in summary.\n'
            '- Put any extra implementation details in message.\n'
            '- Do not pass extra finish-tool arguments beyond the supported fields.\n\n'
            'The summary must list every changed file and, under each file name, add a short explanation of what changed.\n'
            'Use this format inside summary:\n'
            'Files changed:\n'
            '- path/to/file.ext\n'
            '  Short explanation.\n'
            '- another/file.ext\n'
            '  Short explanation.\n'
        )

    def _build_testing_prompt(self, task: Task) -> str:
        repository_scope = self._repository_scope_text(task)
        return (
            f'Validate the implementation for task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            f'{self._tool_guardrails_text()}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            'Do not create a pull request.\n'
            'When you finish, use the finish tool.\n'
            '- Put the testing report in summary.\n'
            '- Put any extra testing details in message.\n'
            '- Do not pass extra finish-tool arguments beyond the supported fields.\n'
        )

    @staticmethod
    def _repository_scope_text(task: Task) -> str:
        repository_branches = getattr(task, 'repository_branches', {}) or {}
        repositories = getattr(task, 'repositories', []) or []
        if not repositories:
            return (
                'Before making changes, try to pull the latest changes from the repository '
                'default branch without interactive auth prompts. If remote access is blocked, '
                'continue from the current local checkout and mention that limitation in your '
                f'finish message. Then create and work on a new branch named {task.branch_name}.'
            )

        repository_lines = []
        for repository in repositories:
            branch_name = repository_branches.get(repository.id, task.branch_name)
            destination_branch = str(getattr(repository, 'destination_branch', '') or '').strip()
            destination_text = (
                destination_branch if destination_branch else 'the repository default branch'
            )
            repository_lines.append(
                f'- {repository.id} at {repository.local_path}: '
                f'first try to pull the latest changes from {destination_text} without '
                'interactive auth prompts. If remote access is blocked, continue from the '
                'current local checkout and mention that limitation in your finish message. '
                f'Then create and work on a new branch named {branch_name}, and open the pull '
                f'request into {destination_text}.'
            )
        lines = '\n'.join(repository_lines)
        return f'Only modify these repositories:\n{lines}'

    @classmethod
    def _build_review_prompt(cls, comment: ReviewComment, branch_name: str) -> str:
        repository_id = getattr(comment, PullRequestFields.REPOSITORY_ID, '')
        repository_context = f' in repository {repository_id}' if repository_id else ''
        review_context = cls._review_comment_context_text(comment)
        return (
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'Comment by {comment.author}: {comment.body}'
            f'{review_context}\n\n'
            f'{cls._tool_guardrails_text()}\n\n'
            'When you finish, use the finish tool.\n'
            '- Put a short description of what changed in summary.\n'
            '- Put any extra details in message.\n'
            '- Do not pass extra finish-tool arguments beyond the supported fields.\n'
        )

    @staticmethod
    def _tool_guardrails_text() -> str:
        return (
            'Tool guardrails:\n'
            '- Prefer shell commands like rg, sed -n, and cat for quick file reads.\n'
            '- If you use the file_editor tool, always include its required command field.\n'
            '- Never call file_editor with only path, summary, or security_risk.'
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

    @staticmethod
    def _success_flag(payload: dict) -> bool:
        value = payload.get(ImplementationFields.SUCCESS, False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)

    @staticmethod
    def _payload_session_id(payload: dict) -> str:
        for key in (ImplementationFields.SESSION_ID, 'conversation_id'):
            value = str(payload.get(key, '') or '').strip()
            if value:
                return value
        return ''

    def _run_prompt(
        self,
        prompt: str,
        title: str,
        session_id: str = '',
    ) -> dict[str, str | bool]:
        conversation_id = self._start_conversation(prompt, title, session_id)
        payload = self._wait_for_conversation_result(conversation_id)
        payload[ImplementationFields.SESSION_ID] = conversation_id
        return payload

    def _sync_runtime_settings(self) -> None:
        payload = self._settings_update_payload()
        if not payload:
            return
        response = self._post_with_retry(self._SETTINGS_PATH, json=payload)
        response.raise_for_status()

    def _settings_update_payload(self) -> dict[str, str]:
        llm_model = str(self._llm_settings.get('llm_model', '') or '').strip()
        if not llm_model:
            return {}

        payload = {'llm_model': llm_model}
        llm_base_url = str(self._llm_settings.get('llm_base_url', '') or '').strip()
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

        response = self._post_with_retry(
            self._APP_CONVERSATIONS_PATH,
            json=request_body,
        )
        response.raise_for_status()
        start_task = self._normalized_payload(response)
        return self._wait_for_started_conversation_id(start_task)

    def _wait_for_started_conversation_id(self, start_task: dict) -> str:
        start_task_id = str(start_task.get('id', '') or '').strip()
        if not start_task_id:
            raise ValueError('openhands start task response did not include an id')

        for attempt in range(self._max_poll_attempts):
            task_info = self._get_start_task(start_task_id)
            status = str(task_info.get('status', '') or '').strip().upper()
            if status == self._START_TASK_READY:
                conversation_id = str(task_info.get('app_conversation_id', '') or '').strip()
                if conversation_id:
                    return conversation_id
                raise ValueError('openhands start task became ready without a conversation id')
            if status == self._START_TASK_ERROR:
                detail = str(task_info.get('detail', '') or '').strip()
                raise RuntimeError(detail or 'openhands failed to start a conversation')
            self._sleep_before_next_poll(attempt)

        raise TimeoutError(
            f'openhands did not start a conversation after {self._max_poll_attempts} polls'
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
        raise ValueError(f'openhands start task not found: {start_task_id}')

    def _wait_for_conversation_result(self, conversation_id: str) -> dict[str, str | bool]:
        for attempt in range(self._max_poll_attempts):
            conversation = self._get_conversation(conversation_id)
            execution_status = str(conversation.get('execution_status', '') or '').strip().lower()
            if execution_status in self._FAILED_EXECUTION_STATUSES:
                raise RuntimeError(f'openhands conversation failed with status: {execution_status}')
            if execution_status not in self._ACTIVE_EXECUTION_STATUSES:
                return self._get_result_payload(conversation_id)
            self._sleep_before_next_poll(attempt)

        raise TimeoutError(
            f'openhands conversation {conversation_id} did not finish after {self._max_poll_attempts} polls'
        )

    def _get_conversation(self, conversation_id: str) -> dict:
        response = self._get_with_retry(
            self._APP_CONVERSATIONS_PATH,
            params={'ids': [conversation_id]},
        )
        response.raise_for_status()
        conversations = self._normalized_items_payload(response)
        if conversations:
            return conversations[0]
        raise ValueError(f'openhands conversation not found: {conversation_id}')

    def _get_result_payload(self, conversation_id: str) -> dict[str, str | bool]:
        response = self._get_with_retry(
            self._EVENTS_PATH_TEMPLATE.format(conversation_id=conversation_id),
            params={'limit': 100, 'sort_order': 'TIMESTAMP_DESC'},
        )
        response.raise_for_status()
        payload = self._normalized_payload(response)
        events = payload.get('items', [])
        if not isinstance(events, list):
            raise ValueError('openhands events response did not include items')

        for candidate_events in (events, list(reversed(events))):
            for event in candidate_events:
                parsed_result = self._result_payload_from_event(event)
                if parsed_result is not None:
                    return parsed_result
        raise ValueError(f'openhands conversation {conversation_id} did not return a parseable result')

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
        if not isinstance(event, dict):
            return None
        if str(event.get('kind', '') or '').strip() != 'ActionEvent':
            return None
        if str(event.get('source', '') or '').strip() != 'agent':
            return None
        if str(event.get('tool_name', '') or '').strip() != 'finish':
            return None

        parsed_arguments: dict[str, str | bool] = {}
        tool_call = event.get('tool_call', {})
        if isinstance(tool_call, dict):
            arguments = str(tool_call.get('arguments', '') or '').strip()
            if arguments:
                try:
                    payload = json.loads(arguments)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    parsed_arguments = payload

        action = event.get('action', {})
        if not isinstance(action, dict):
            action = {}

        summary = str(
            parsed_arguments.get(Task.summary.key)
            or parsed_arguments.get('summary')
            or action.get('summary')
            or event.get('summary')
            or ''
        ).strip()
        message = str(
            parsed_arguments.get('message')
            or action.get('message')
            or ''
        ).strip()

        if not summary and not message:
            return None

        result: dict[str, str | bool] = {
            ImplementationFields.SUCCESS: OpenHandsClient._success_flag(parsed_arguments)
            if ImplementationFields.SUCCESS in parsed_arguments
            else True,
            Task.summary.key: summary or message,
        }
        commit_message = str(parsed_arguments.get(ImplementationFields.COMMIT_MESSAGE, '') or '').strip()
        if commit_message:
            result[ImplementationFields.COMMIT_MESSAGE] = commit_message
        return result

    @staticmethod
    def _assistant_message_text(event: object) -> str:
        if not isinstance(event, dict):
            return ''
        if str(event.get('kind', '') or '').strip() != 'MessageEvent':
            return ''
        if str(event.get('source', '') or '').strip() != 'agent':
            return ''
        llm_message = event.get('llm_message', {})
        if not isinstance(llm_message, dict):
            return ''
        if str(llm_message.get('role', '') or '').strip() != 'assistant':
            return ''
        content = llm_message.get('content', [])
        if not isinstance(content, list):
            return ''
        texts = [
            str(item.get('text', '') or '').strip()
            for item in content
            if isinstance(item, dict)
        ]
        return '\n'.join(text for text in texts if text)

    @staticmethod
    def _parse_result_json(message_text: str) -> dict[str, str | bool] | None:
        text = str(message_text or '').strip()
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
        return None

    @staticmethod
    def _normalized_uuid(value: str) -> str:
        normalized_value = str(value or '').strip()
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
        all_comments = getattr(comment, ReviewCommentFields.ALL_COMMENTS, [])
        if not isinstance(all_comments, list) or len(all_comments) <= 1:
            return ''

        lines: list[str] = []
        for item in all_comments:
            if not isinstance(item, dict):
                continue
            author = str(item.get(ReviewCommentFields.AUTHOR, '') or '').strip()
            body = str(item.get(ReviewCommentFields.BODY, '') or '').strip()
            if not body:
                continue
            label = author if author else 'reviewer'
            lines.append(f'- {label}: {body}')
        if not lines:
            return ''
        return '\n\nReview comment context:\n' + '\n'.join(lines)
