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
    _POLL_INTERVAL_SECONDS = 1.0
    _MAX_POLL_ATTEMPTS = 300

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url, api_key, timeout=300, max_retries=max_retries)

    def validate_connection(self) -> None:
        response = self._get_with_retry(f'{self._APP_CONVERSATIONS_PATH}/count')
        response.raise_for_status()

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
            'When you finish, return only JSON with these keys:\n'
            '- success: true when the implementation is ready for testing, otherwise false.\n'
            '- summary: the pull request description.\n'
            '- commit_message: the commit message to use.\n\n'
            'The summary must list every changed file and, under each file name, add a short explanation of what changed.\n'
            'Use this format inside summary:\n'
            'Files changed:\n'
            '- path/to/file.ext\n'
            '  Short explanation.\n'
            '- another/file.ext\n'
            '  Short explanation.\n\n'
            'Return JSON only. Do not wrap it in markdown.'
        )

    def _build_testing_prompt(self, task: Task) -> str:
        repository_scope = self._repository_scope_text(task)
        return (
            f'Validate the implementation for task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            'Do not create a pull request.\n'
            'When you finish, return only JSON with these keys:\n'
            '- success: true when the implementation is ready for review, otherwise false.\n'
            '- summary: a short testing report.\n\n'
            'Return JSON only. Do not wrap it in markdown.'
        )

    @staticmethod
    def _repository_scope_text(task: Task) -> str:
        repository_branches = getattr(task, 'repository_branches', {}) or {}
        repositories = getattr(task, 'repositories', []) or []
        if not repositories:
            return f'Work on branch {task.branch_name}.'

        repository_lines = []
        for repository in repositories:
            branch_name = repository_branches.get(repository.id, task.branch_name)
            destination_branch = str(getattr(repository, 'destination_branch', '') or '').strip()
            destination_text = (
                destination_branch if destination_branch else 'the repository default branch'
            )
            repository_lines.append(
                f'- {repository.id} at {repository.local_path}: '
                f'use branch {branch_name} and open the pull request into {destination_text}.'
            )
        lines = '\n'.join(repository_lines)
        return f'Only modify these repositories:\n{lines}'

    @staticmethod
    def _build_review_prompt(comment: ReviewComment, branch_name: str) -> str:
        repository_id = getattr(comment, PullRequestFields.REPOSITORY_ID, '')
        repository_context = f' in repository {repository_id}' if repository_id else ''
        review_context = OpenHandsClient._review_comment_context_text(comment)
        return (
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'Comment by {comment.author}: {comment.body}'
            f'{review_context}\n\n'
            'When you finish, return only JSON with these keys:\n'
            '- success: true when the comment was addressed, otherwise false.\n'
            '- summary: a short description of what changed.\n'
            '- commit_message: the commit message to use.\n\n'
            'Return JSON only. Do not wrap it in markdown.'
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

        for attempt in range(self._MAX_POLL_ATTEMPTS):
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

        raise TimeoutError(f'openhands did not start a conversation after {self._MAX_POLL_ATTEMPTS} polls')

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
        for attempt in range(self._MAX_POLL_ATTEMPTS):
            conversation = self._get_conversation(conversation_id)
            execution_status = str(conversation.get('execution_status', '') or '').strip().lower()
            if execution_status in self._FAILED_EXECUTION_STATUSES:
                raise RuntimeError(f'openhands conversation failed with status: {execution_status}')
            if execution_status not in self._ACTIVE_EXECUTION_STATUSES:
                return self._get_result_payload(conversation_id)
            self._sleep_before_next_poll(attempt)

        raise TimeoutError(
            f'openhands conversation {conversation_id} did not finish after {self._MAX_POLL_ATTEMPTS} polls'
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
                message_text = self._assistant_message_text(event)
                if not message_text:
                    continue
                parsed_result = self._parse_result_json(message_text)
                if parsed_result is not None:
                    return parsed_result
        raise ValueError(f'openhands conversation {conversation_id} did not return a parseable result')

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
        if attempt >= self._MAX_POLL_ATTEMPTS - 1:
            return
        time.sleep(self._POLL_INTERVAL_SECONDS)

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
