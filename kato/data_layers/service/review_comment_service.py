from __future__ import annotations

import re
from urllib.parse import urlparse

from core_lib.data_layers.service.service import Service
from requests import HTTPError

from kato.client.ticket_client_base import TicketClientBase
from kato.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    TaskCommentFields,
)
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.service.agent_state_registry import AgentStateRegistry
from kato.data_layers.service.implementation_service import ImplementationService
from kato.data_layers.service.repository_service import RepositoryService
from kato.data_layers.service.task_service import TaskService
from kato.helpers.logging_utils import configure_logger
from kato.helpers.mission_logging_utils import log_mission_end, log_mission_start, log_mission_step
from kato.helpers.review_comment_utils import (
    ReviewFixContext,
    comment_context_entry,
    is_kato_review_comment_reply,
    review_comment_from_payload,
    review_comment_fixed_comment,
    review_comment_reply_body,
    review_comment_processing_keys,
    review_comment_resolution_key,
    review_fix_context_from_mapping,
    review_fix_result,
)
from kato.helpers.text_utils import normalized_text, text_from_attr

NON_FATAL_REVIEW_RESOLUTION_STATUS_CODES = {404, 409}
NON_FATAL_REVIEW_RESOLUTION_MESSAGES = (
    'already resolved',
    'could not resolve to a node',
    'not found',
    'was not found',
)


class ReviewCommentService(Service):
    """Handle review-comment polling, fix publication, and comment resolution."""

    def __init__(
        self,
        task_service: TaskService,
        implementation_service: ImplementationService,
        repository_service: RepositoryService,
        state_registry: AgentStateRegistry,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._implementation_service = implementation_service
        self._repository_service = repository_service
        self._state_registry = state_registry
        self.logger = logger or configure_logger(self.__class__.__name__)

    @property
    def state_registry(self) -> AgentStateRegistry:
        return self._state_registry

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        comment = review_comment_from_payload(payload)
        return self.process_review_comment(comment)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        review_context = self._review_fix_context(comment)
        display_name = self._review_pull_request_display_name(comment, review_context)
        log_mission_start(
            self.logger,
            review_context.task_id,
            'starting mission: %s (comment %s)',
            display_name,
            comment.comment_id,
        )
        repository = self._repository_service.get_repository(review_context.repository_id)
        try:
            self._prepare_review_fix_branch(repository, review_context)
            execution = self._run_review_comment_fix(comment, review_context)
            self._publish_review_comment_fix(comment, repository, review_context, execution)
            self._complete_review_fix(comment, review_context)
            log_mission_end(
                self.logger,
                review_context.task_id,
                'done working on mission: %s',
                display_name,
            )
            return review_fix_result(comment, review_context)
        except Exception:
            self._restore_review_comment_repository(comment, repository)
            self.logger.exception(
                'failed to process review comment %s for pull request %s',
                comment.comment_id,
                comment.pull_request_id,
            )
            raise

    def get_new_pull_request_comments(self) -> list[ReviewComment]:
        new_comments: list[ReviewComment] = []
        try:
            review_contexts = self._review_pull_request_contexts()
        except Exception:
            self.logger.exception('failed to determine review-state pull requests to poll')
            return new_comments
        if not review_contexts:
            return new_comments

        review_pull_request_keys = {
            (
                context[PullRequestFields.ID],
                context[PullRequestFields.REPOSITORY_ID],
            )
            for context in review_contexts
        }

        for context in review_contexts:
            new_comments.extend(
                self._new_pull_request_comments_for_context(
                    context,
                    review_pull_request_keys,
                )
            )

        return new_comments

    def _review_pull_request_contexts(self) -> list[dict[str, str]]:
        contexts: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for task in self._task_service.get_review_tasks():
            try:
                task_contexts = self._review_task_pull_request_contexts(task)
            except Exception:
                self.logger.exception(
                    'failed to determine review pull requests for task %s',
                    task.id,
                )
                continue
            for context in task_contexts:
                key = (
                    normalized_text(context.get(PullRequestFields.ID, '')),
                    normalized_text(context.get(PullRequestFields.REPOSITORY_ID, '')),
                )
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                contexts.append(context)
        return contexts

    def _review_task_pull_request_contexts(self, task) -> list[dict[str, str]]:
        repositories = self._repository_service.resolve_task_repositories(task)
        contexts: list[dict[str, str]] = []
        title_prefix = f'{task.id} '
        for repository in repositories:
            branch_name = self._repository_service.build_branch_name(task, repository)
            task_contexts = self._task_pull_request_contexts(
                task,
                repository,
                branch_name,
            )
            if task_contexts:
                contexts.extend(task_contexts)
                continue
            pull_requests = self._repository_service.find_pull_requests(
                repository,
                source_branch=branch_name,
                title_prefix=title_prefix,
            )
            for pull_request in pull_requests:
                pull_request_context = dict(pull_request)
                pull_request_context[PullRequestFields.REPOSITORY_ID] = repository.id
                self._state_registry.remember_pull_request_context(
                    pull_request_context,
                    branch_name,
                    task_id=str(task.id or ''),
                    task_summary=str(task.summary or ''),
                )
                contexts.append(
                    {
                        PullRequestFields.ID: pull_request[PullRequestFields.ID],
                        PullRequestFields.REPOSITORY_ID: repository.id,
                        'branch_name': branch_name,
                    }
                )
        return contexts

    def _task_pull_request_contexts(
        self,
        task,
        repository,
        branch_name: str,
    ) -> list[dict[str, str]]:
        contexts: list[dict[str, str]] = []
        for pull_request_id in self._task_pull_request_ids(task, repository):
            pull_request = {
                PullRequestFields.ID: pull_request_id,
                PullRequestFields.REPOSITORY_ID: repository.id,
            }
            self._state_registry.remember_pull_request_context(
                pull_request,
                branch_name,
                task_id=str(task.id or ''),
                task_summary=str(task.summary or ''),
            )
            contexts.append(
                {
                    PullRequestFields.ID: pull_request_id,
                    PullRequestFields.REPOSITORY_ID: repository.id,
                    'branch_name': branch_name,
                }
            )
        return contexts

    def _task_pull_request_ids(self, task, repository) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for text in self._task_pull_request_texts(task):
            for url in self._pull_request_urls(text):
                pull_request_id = self._repository_pull_request_id_from_url(url, repository)
                if not pull_request_id or pull_request_id in seen:
                    continue
                seen.add(pull_request_id)
                ids.append(pull_request_id)
        return ids

    @staticmethod
    def _task_pull_request_texts(task) -> list[str]:
        texts: list[str] = [str(getattr(task, 'description', '') or '')]
        comment_entries = getattr(task, TaskCommentFields.ALL_COMMENTS, [])
        if isinstance(comment_entries, list):
            for comment_entry in comment_entries:
                if not isinstance(comment_entry, dict):
                    continue
                texts.append(str(comment_entry.get(TaskCommentFields.BODY, '') or ''))
        return texts

    @staticmethod
    def _pull_request_urls(text: str) -> list[str]:
        return re.findall(r'https?://[^\s)]+', str(text or ''))

    def _repository_pull_request_id_from_url(self, url: str, repository) -> str:
        parsed = urlparse(str(url or '').strip())
        path_parts = [part for part in parsed.path.split('/') if part]
        if len(path_parts) < 3:
            return ''
        repository_path = '/'.join(
            [
                str(getattr(repository, 'owner', '') or '').strip('/'),
                str(getattr(repository, 'repo_slug', '') or '').strip('/'),
            ]
        ).strip('/')
        if not repository_path:
            return ''

        provider_base_url = str(getattr(repository, 'provider_base_url', '') or '').lower()
        if 'bitbucket' in provider_base_url:
            if len(path_parts) < 3 or path_parts[-2] != 'pull-requests':
                return ''
            candidate_repository_path = '/'.join(path_parts[:-2])
            return path_parts[-1] if candidate_repository_path == repository_path else ''
        if 'github' in provider_base_url:
            if len(path_parts) < 3 or path_parts[-2] != 'pull':
                return ''
            candidate_repository_path = '/'.join(path_parts[:-2])
            return path_parts[-1] if candidate_repository_path == repository_path else ''
        if 'gitlab' in provider_base_url:
            if '-/' not in parsed.path:
                return ''
            repository_path_part, merge_request_part = parsed.path.split('/-/', 1)
            if merge_request_part.count('/') < 1:
                return ''
            merge_request_parts = [part for part in merge_request_part.split('/') if part]
            if len(merge_request_parts) < 2 or merge_request_parts[0] != 'merge_requests':
                return ''
            candidate_repository_path = repository_path_part.strip('/')
            return (
                merge_request_parts[1]
                if candidate_repository_path == repository_path
                else ''
            )
        return ''

    def _new_pull_request_comments_for_context(
        self,
        context: dict[str, str],
        review_pull_request_keys: set[tuple[str, str]],
    ) -> list[ReviewComment]:
        repository_id = context[PullRequestFields.REPOSITORY_ID]
        pull_request_id = context[PullRequestFields.ID]
        if (pull_request_id, repository_id) not in review_pull_request_keys:
            return []
        comments = self._pull_request_comments(repository_id, pull_request_id)
        if not comments:
            return []
        comment_context = [comment_context_entry(comment) for comment in comments]
        return self._unprocessed_review_comments(
            comments,
            repository_id,
            pull_request_id,
            comment_context,
        )

    def _pull_request_comments(
        self,
        repository_id: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        try:
            repository = self._repository_service.get_repository(repository_id)
            return self._repository_service.list_pull_request_comments(
                repository,
                pull_request_id,
            )
        except Exception:
            self.logger.exception(
                'failed to fetch pull request comments for repository %s pull request %s',
                repository_id,
                pull_request_id,
            )
            return []

    def _unprocessed_review_comments(
        self,
        comments: list[ReviewComment],
        repository_id: str,
        pull_request_id: str,
        comment_context: list[dict[str, str]],
    ) -> list[ReviewComment]:
        new_comments: list[ReviewComment] = []
        seen_resolution_targets: set[tuple[str, str]] = set()
        already_handled_resolution_targets = {
            review_comment_resolution_key(comment)
            for comment in comments
            if is_kato_review_comment_reply(comment)
        }
        for comment in reversed(comments):
            setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
            setattr(comment, ReviewCommentFields.ALL_COMMENTS, list(comment_context))
            resolution_key = review_comment_resolution_key(comment)
            if resolution_key in already_handled_resolution_targets:
                continue
            if resolution_key in seen_resolution_targets:
                continue
            seen_resolution_targets.add(resolution_key)
            if self._is_review_comment_processed(repository_id, pull_request_id, comment):
                continue
            new_comments.append(comment)
        return new_comments

    def _review_fix_context(self, comment: ReviewComment) -> ReviewFixContext:
        repository_id = text_from_attr(comment, PullRequestFields.REPOSITORY_ID)
        context = self._state_registry.pull_request_context(
            comment.pull_request_id,
            repository_id,
        )
        if context is None:
            raise ValueError(f'unknown pull request id: {comment.pull_request_id}')
        review_context = review_fix_context_from_mapping(context)
        setattr(comment, PullRequestFields.REPOSITORY_ID, review_context.repository_id)
        return review_context

    @staticmethod
    def _review_pull_request_display_name(
        comment: ReviewComment,
        review_context: ReviewFixContext,
    ) -> str:
        return (
            normalized_text(review_context.pull_request_title)
            or f'pull request {comment.pull_request_id}'
        )

    def _prepare_review_fix_branch(
        self,
        repository,
        review_context: ReviewFixContext,
    ) -> None:
        self._repository_service.prepare_task_branches(
            [repository],
            {review_context.repository_id: review_context.branch_name},
        )

    def _run_review_comment_fix(
        self,
        comment: ReviewComment,
        review_context: ReviewFixContext,
    ) -> dict[str, str | bool]:
        execution = self._implementation_service.fix_review_comment(
            comment,
            review_context.branch_name,
            review_context.session_id,
            task_id=review_context.task_id,
            task_summary=review_context.task_summary,
        ) or {}
        if not execution.get(ImplementationFields.SUCCESS, False):
            raise RuntimeError(f'failed to address comment {comment.comment_id}')
        return execution

    def _publish_review_comment_fix(
        self,
        comment: ReviewComment,
        repository,
        review_context: ReviewFixContext,
        execution: dict[str, str | bool],
    ) -> None:
        self.logger.info(
            'publishing review fix for pull request %s comment %s on branch %s',
            comment.pull_request_id,
            comment.comment_id,
            review_context.branch_name,
        )
        self._repository_service.publish_review_fix(
            repository,
            review_context.branch_name,
            self._review_fix_commit_message(),
        )
        self._repository_service.reply_to_review_comment(
            repository,
            comment,
            review_comment_reply_body(execution),
        )
        self.logger.info(
            'replied to review comment %s on pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        if self._resolve_review_comment(repository, comment):
            self.logger.info(
                'resolved review comment %s on pull request %s',
                comment.comment_id,
                comment.pull_request_id,
            )
        else:
            self.logger.info(
                'skipped resolving review comment %s on pull request %s',
                comment.comment_id,
                comment.pull_request_id,
            )

    def _complete_review_fix(
        self,
        comment: ReviewComment,
        review_context: ReviewFixContext,
    ) -> None:
        for processing_key in review_comment_processing_keys(comment):
            self._state_registry.mark_review_comment_processed(
                review_context.repository_id,
                comment.pull_request_id,
                processing_key,
            )
        self._comment_review_fix_completed(
            comment,
            review_context.repository_id,
        )

    def _is_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment: ReviewComment,
    ) -> bool:
        return any(
            self._state_registry.is_review_comment_processed(
                repository_id,
                pull_request_id,
                processing_key,
            )
            for processing_key in review_comment_processing_keys(comment)
        )

    @staticmethod
    def _review_fix_commit_message() -> str:
        return 'Address review comments'

    def _resolve_review_comment(self, repository, comment: ReviewComment) -> bool:
        try:
            self._repository_service.resolve_review_comment(repository, comment)
        except HTTPError as exc:
            if not self._is_non_fatal_review_resolution_http_error(exc):
                raise
            status_code = getattr(getattr(exc, 'response', None), 'status_code', '')
            self.logger.warning(
                'review comment %s on pull request %s could not be resolved because '
                'the provider returned HTTP %s; continuing because the fix was already '
                'published and replied',
                comment.comment_id,
                comment.pull_request_id,
                status_code,
            )
            return False
        except RuntimeError as exc:
            if not self._is_non_fatal_review_resolution_runtime_error(exc):
                raise
            self.logger.warning(
                'review comment %s on pull request %s could not be resolved because '
                'the provider reported it is already resolved or unavailable; continuing '
                'because the fix was already published and replied: %s',
                comment.comment_id,
                comment.pull_request_id,
                exc,
            )
            return False
        return True

    @staticmethod
    def _is_non_fatal_review_resolution_http_error(exc: HTTPError) -> bool:
        response = getattr(exc, 'response', None)
        return getattr(response, 'status_code', None) in NON_FATAL_REVIEW_RESOLUTION_STATUS_CODES

    @staticmethod
    def _is_non_fatal_review_resolution_runtime_error(exc: RuntimeError) -> bool:
        message = normalized_text(str(exc)).lower()
        return any(token in message for token in NON_FATAL_REVIEW_RESOLUTION_MESSAGES)

    def _restore_review_comment_repository(self, comment: ReviewComment, repository) -> None:
        try:
            self.logger.info(
                'restoring repository branches after review comment failure for pull request %s comment %s',
                comment.pull_request_id,
                comment.comment_id,
            )
            self._repository_service.restore_task_repositories([repository], force=True)
        except Exception:
            self.logger.exception(
                'failed to restore repository %s after review comment failure',
                repository.id,
            )

    def _comment_review_fix_completed(
        self,
        comment: ReviewComment,
        repository_id: str,
    ) -> None:
        task_id = self._state_registry.task_id_for_pull_request(
            comment.pull_request_id,
            repository_id,
        )
        if not task_id:
            return
        self._task_service.add_comment(
            task_id,
            review_comment_fixed_comment(comment),
        )
