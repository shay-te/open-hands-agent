from core_lib.data_layers.service.service import Service

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.review_comment_utils import (
    ReviewFixContext,
    comment_context_entry,
    review_comment_fixed_comment,
    review_comment_resolution_key,
    review_fix_context_from_mapping,
    review_fix_result,
)
from openhands_agent.helpers.text_utils import text_from_attr


class ReviewCommentService(Service):
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
        comment = self._implementation_service.review_comment_from_payload(payload)
        return self.process_review_comment(comment)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        self.logger.info(
            'processing review comment %s for pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        review_context = self._review_fix_context(comment)
        repository = self._repository_service.get_repository(review_context.repository_id)
        try:
            self._prepare_review_fix_branch(repository, review_context)
            self._run_review_comment_fix(comment, review_context)
            self._publish_review_comment_fix(comment, repository, review_context)
            self._complete_review_fix(comment, review_context)
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
            review_pull_request_keys = self._review_pull_request_keys()
        except Exception:
            self.logger.exception('failed to determine review-state pull requests to poll')
            return new_comments
        if not review_pull_request_keys:
            return new_comments

        for context in self._state_registry.tracked_pull_request_contexts():
            new_comments.extend(
                self._new_pull_request_comments_for_context(
                    context,
                    review_pull_request_keys,
                )
            )

        return new_comments

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
        for comment in reversed(comments):
            setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
            setattr(comment, ReviewCommentFields.ALL_COMMENTS, list(comment_context))
            resolution_key = review_comment_resolution_key(comment)
            if resolution_key in seen_resolution_targets:
                continue
            seen_resolution_targets.add(resolution_key)
            if self._state_registry.is_review_comment_processed(
                repository_id,
                pull_request_id,
                comment.comment_id,
            ):
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
        self.logger.info(
            'published review fix for pull request %s comment %s',
            comment.pull_request_id,
            comment.comment_id,
        )
        self.logger.info(
            'resolving review comment %s on pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        self._repository_service.resolve_review_comment(repository, comment)
        self.logger.info(
            'resolved review comment %s on pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )

    def _complete_review_fix(
        self,
        comment: ReviewComment,
        review_context: ReviewFixContext,
    ) -> None:
        self._state_registry.mark_review_comment_processed(
            review_context.repository_id,
            comment.pull_request_id,
            comment.comment_id,
        )
        self._comment_review_fix_completed(
            comment,
            review_context.repository_id,
        )

    @staticmethod
    def _review_fix_commit_message() -> str:
        return 'Address review comments'

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

    def _review_pull_request_keys(self) -> set[tuple[str, str]]:
        review_pull_request_keys: set[tuple[str, str]] = set()
        for task in self._task_service.get_review_tasks():
            for pull_request in self._state_registry.processed_task_pull_requests(task.id):
                if not isinstance(pull_request, dict):
                    continue
                pull_request_id = str(pull_request.get(PullRequestFields.ID, '') or '').strip()
                repository_id = str(
                    pull_request.get(PullRequestFields.REPOSITORY_ID, '') or ''
                ).strip()
                if pull_request_id and repository_id:
                    review_pull_request_keys.add((pull_request_id, repository_id))
        return review_pull_request_keys

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
