from __future__ import annotations

import re
from urllib.parse import urlparse

from core_lib.data_layers.service.service import Service
from requests import HTTPError

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    TaskCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.implementation_service import ImplementationService
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import (
    log_mission_end,
    log_mission_start,
    log_review_comment_end,
    log_review_comment_start,
)
from kato_core_lib.helpers.review_comment_utils import (
    ReviewFixContext,
    comment_context_entry,
    is_kato_review_comment_reply,
    is_mention_comment,
    is_question_only_batch,
    review_comment_answer_body,
    review_comment_from_payload,
    review_comment_fixed_comment,
    review_comment_reply_body,
    review_comment_processing_keys,
    review_comment_resolution_key,
    review_fix_context_from_mapping,
    review_fix_result,
)
from kato_core_lib.helpers.task_lookup_utils import find_task_by_id
from kato_core_lib.helpers.text_utils import normalized_text, text_from_attr

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
        planning_session_runner=None,
        use_streaming_for_review_fixes: bool = False,
        workspace_manager=None,
    ) -> None:
        self._task_service = task_service
        self._implementation_service = implementation_service
        self._repository_service = repository_service
        self._state_registry = state_registry
        self._planning_session_runner = planning_session_runner
        self._use_streaming_for_review_fixes = bool(
            planning_session_runner is not None and use_streaming_for_review_fixes
        )
        # When set, review-fix runs against per-task workspace clones
        # instead of the operator's shared on-disk repository checkout.
        # Prevents parallel review-fix workers from clobbering each
        # other's git state (index.lock, half-finished rebases, branch
        # switches) when multiple comments fire on the same scan tick.
        self._workspace_manager = workspace_manager
        self.logger = logger or configure_logger(self.__class__.__name__)

    @property
    def state_registry(self) -> AgentStateRegistry:
        return self._state_registry

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        comment = review_comment_from_payload(payload)
        return self.process_review_comment(comment)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        results = self.process_review_comment_batch([comment])
        # Empty list = graceful terminal (no changes made, comment already
        # marked processed and replied to). Not an error.
        return results[0] if results else {}

    def process_review_comment_batch(
        self, comments: list[ReviewComment],
    ) -> list[dict[str, str]]:
        """Address every comment in ``comments`` in a single agent spawn.

        Caller (``process_assigned_tasks._dispatch_review_comments``)
        groups comments by ``(repository_id, pull_request_id)`` so
        every entry shares the workspace clone, the branch, and the
        Claude session. Cuts startup cost from O(N) sessions to O(1)
        and gives the agent the full set of related comments at once
        — same rename across three files becomes one edit, not three
        racing edits.

        All-or-nothing on the implementation: one agent spawn,
        success means every comment is addressed in a single
        commit-and-push. Per-comment for the publication side: each
        comment gets its own platform reply, resolution, and
        state-registry mark — so the operator can see in the PR
        which specific comments were addressed.
        """
        if not comments:
            return []
        review_context = self._review_fix_context(comments[0])
        # Defensive: every comment must share the (repo, pr) shape.
        # The dispatcher already groups; this guards against a
        # misuse that would otherwise corrupt the batch by trying
        # to push two PRs' worth of fixes onto one branch.
        for comment in comments[1:]:
            ctx = self._review_fix_context(comment)
            if (
                ctx.repository_id != review_context.repository_id
                or comment.pull_request_id != comments[0].pull_request_id
            ):
                raise ValueError(
                    'process_review_comment_batch requires every comment '
                    'to share the same (repository_id, pull_request_id); '
                    f'got {comment.comment_id} on '
                    f'{ctx.repository_id}/{comment.pull_request_id}'
                )
        log_mission_start(
            self.logger,
            review_context.task_id,
            'starting mission',
        )
        log_review_comment_start(
            self.logger,
            review_context.task_id,
            'starting pull request %s (%d comment(s) in batch)',
            comments[0].pull_request_id,
            len(comments),
        )
        repository = self._repository_service.get_repository(review_context.repository_id)
        repository = self._provision_workspace_clone(repository, review_context)
        # Pure-question batches go through the answer-only flow:
        # agent reads code to understand context, posts a plain-text
        # answer per comment, NO commit, NO push, NO resolve. The
        # heuristic is conservative so any wording that looks like a
        # fix request stays on the existing fix flow.
        question_only = is_question_only_batch(comments)
        try:
            self._prepare_review_fix_branch(repository, review_context)
            # Snapshot HEAD before the agent runs so we can verify
            # the fix actually moved the branch (or left dirty edits)
            # before claiming we addressed the comment. Without this
            # check, a no-op agent run on a branch that already has
            # prior commits ahead of base would still publish (push
            # is a no-op when remote is up to date), reply "kato
            # addressed this", and resolve the comment — even though
            # nothing changed for THIS comment. The classic symptom:
            # "Done — edits written, kato will publish" reply with
            # zero commits behind it.
            head_sha_fn = getattr(
                self._repository_service, 'current_head_sha', None,
            )
            head_before_agent = (
                head_sha_fn(repository) if callable(head_sha_fn) else ''
            )
            execution = self._run_review_comments_batch_fix(
                comments, review_context,
                mode=('answer' if question_only else 'fix'),
                repository=repository,
            )
            if question_only:
                self._publish_review_comment_answers(
                    comments, repository, review_context, execution,
                )
            else:
                # Hard pre-publish gate: if the agent produced no
                # commits AND the working tree is clean, the fix is a
                # lie. Post a "no changes were made" reply, do NOT
                # resolve, and treat it as a failure so the comment
                # stays open for human review.
                if not self._review_fix_produced_changes(
                    repository, head_before_agent,
                ):
                    self._publish_review_no_changes(
                        comments, repository, review_context,
                    )
                    # Mark each comment processed so the scan loop does
                    # not retry it — the "no changes" reply IS the
                    # terminal outcome; the thread stays open for a human.
                    for comment in comments:
                        self._complete_review_fix(comment, review_context)
                    self.logger.warning(
                        'review-fix agent produced no commits for %d '
                        'comment(s) on PR %s — replied with no-changes '
                        'notice; thread left open for human review',
                        len(comments),
                        comments[0].pull_request_id if comments else '?',
                    )
                    return []
                self._publish_review_comments_batch_fix(
                    comments, repository, review_context, execution,
                )
            for comment in comments:
                self._complete_review_fix(comment, review_context)
            log_review_comment_end(
                self.logger,
                review_context.task_id,
                'completed pull request %s (%d comment(s) in batch)',
                comments[0].pull_request_id,
                len(comments),
            )
            log_mission_end(
                self.logger,
                review_context.task_id,
                'done working on mission',
            )
            _record_review_fix_completed(comments, review_context)
            return [
                review_fix_result(comment, review_context) for comment in comments
            ]
        except Exception:
            # Restore the repository state once for the batch — it's
            # idempotent and the comments share the workspace clone.
            self._restore_review_comment_repository(comments[0], repository)
            self.logger.exception(
                'failed to process review comment batch (%d comment(s)) '
                'for pull request %s',
                len(comments),
                comments[0].pull_request_id,
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
        return re.findall(r"https?://[^\s<>'\")]+", str(text or ''))

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
        """Pick the comments kato should address on this scan tick.

        Position-based "already handled" check: for each thread, find
        the position of kato's most recent reply. A reviewer comment
        whose position is **after** that reply is a follow-up — kato
        re-engages. A reviewer comment whose position is **before**
        the reply has already been addressed; skip.

        This handles two scenarios that the old "thread has any kato
        reply → skip" logic missed:

        1. Reviewer adds a follow-up comment in the same thread
           ("still not fixed, the bug is at line 88"). New comment
           id, but same thread/resolution target — old logic dropped
           it; new logic picks it up because its index in ``comments``
           is past kato's reply.
        2. Reviewer re-opens a thread by adding a new comment after
           kato's reply. Same flow as #1.

        Kato's reply itself is never returned — that's just kato
        narrating to itself. The state-registry processed-mark stays
        as a within-cycle dedup so we don't double-process the same
        follow-up if a scan tick races with another worker.
        """
        # Map thread → index of kato's most recent reply in this
        # comment list. Threads with no kato reply have index = -1
        # so every reviewer comment is "after" by definition.
        last_kato_reply_index: dict = {}
        for index, comment in enumerate(comments):
            if is_kato_review_comment_reply(comment):
                last_kato_reply_index[review_comment_resolution_key(comment)] = index

        # Walk backwards for thread dedup (keep the newest comment per
        # thread), but reverse before returning so the output order
        # matches the documented contract — comments returned in
        # chronological order, same as the reviewer wrote them.
        # Without the final reverse the agent sees the newest comment
        # first, which is wrong for batches where a later comment
        # depends on context from an earlier one.
        new_comments: list[ReviewComment] = []
        seen_resolution_targets: set = set()
        for index in range(len(comments) - 1, -1, -1):
            comment = comments[index]
            if is_kato_review_comment_reply(comment):
                continue
            setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
            setattr(comment, ReviewCommentFields.ALL_COMMENTS, list(comment_context))
            resolution_key = review_comment_resolution_key(comment)
            kato_reply_index = last_kato_reply_index.get(resolution_key, -1)
            if index < kato_reply_index:
                # Reviewer comment older than kato's last reply on
                # this thread — already addressed, skip.
                continue
            if resolution_key in seen_resolution_targets:
                continue
            seen_resolution_targets.add(resolution_key)
            if self._is_review_comment_processed(repository_id, pull_request_id, comment):
                continue
            new_comments.append(comment)
        new_comments.reverse()
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

    def task_id_for_comment(self, comment: ReviewComment) -> str | None:
        """Return the task id this comment will be processed under, or None.

        Exposed so the scan loop can use it as a parallel-runner dedup key
        without having to call ``_review_fix_context`` (which mutates the
        comment). Used to ensure same-task review fixes serialize while
        cross-task fixes run concurrently.
        """
        repository_id = text_from_attr(comment, PullRequestFields.REPOSITORY_ID)
        context = self._state_registry.pull_request_context(
            comment.pull_request_id,
            repository_id,
        )
        if context is None:
            return None
        task_id = context.get('task_id') if isinstance(context, dict) else getattr(
            context, 'task_id', None,
        )
        normalized = str(task_id or '').strip()
        return normalized or None

    def _prepare_review_fix_branch(
        self,
        repository,
        review_context: ReviewFixContext,
    ) -> None:
        self._repository_service.prepare_task_branches(
            [repository],
            {review_context.repository_id: review_context.branch_name},
        )

    def _run_review_comments_batch_fix(
        self,
        comments: list[ReviewComment],
        review_context: ReviewFixContext,
        mode: str = 'fix',
        *,
        repository=None,
    ) -> dict[str, str | bool]:
        """One agent spawn covering every comment in ``comments``.

        Backends address them in a single coherent change-set; we
        get one ``execution`` dict back. ``len(comments) == 1``
        produces an identical agent prompt to the legacy singular
        path — no regression for setups that don't yet trigger
        batched flow.

        ``mode='answer'`` switches to the question-answering prompt:
        agent reads code, returns text answer in ``message``, no
        commit. The caller (publish path) uses the message as the
        reply body and skips ``publish_review_fix``.

        Back-compat: if the wired service or planning runner doesn't
        expose ``fix_review_comments`` (older test stubs / custom
        wrappers), we fan out to the singular method per comment.
        Loses batching efficiency but preserves correctness for any
        caller that hasn't migrated yet.
        """
        if self._use_streaming_for_review_fixes:
            self.logger.info(
                'streaming review-fix session for task %s (%d comment(s), mode=%s) '
                '(visible in the planning UI)',
                review_context.task_id,
                len(comments),
                mode,
            )
            execution = self._call_fix_review_comments_or_fanout(
                self._planning_session_runner,
                comments,
                review_context,
                streaming=True,
                mode=mode,
                repository=repository,
            )
        else:
            execution = self._call_fix_review_comments_or_fanout(
                self._implementation_service,
                comments,
                review_context,
                streaming=False,
                mode=mode,
                repository=repository,
            )
        if not execution.get(ImplementationFields.SUCCESS, False):
            comment_ids = ', '.join(c.comment_id for c in comments)
            raise RuntimeError(
                f'failed to address review comment batch ({comment_ids})'
            )
        return execution

    def _call_fix_review_comments_or_fanout(
        self,
        backend,
        comments: list[ReviewComment],
        review_context: ReviewFixContext,
        *,
        streaming: bool,
        mode: str = 'fix',
        repository=None,
    ) -> dict[str, str | bool]:
        if streaming:
            # Spawn cwd for the streaming agent. MUST be the workspace
            # clone's path (under ``KATO_WORKSPACES_ROOT``), NOT the
            # inventory entry's ``local_path`` (under
            # ``REPOSITORY_ROOT_PATH``). When ``repository`` is the
            # already-provisioned workspace clone (the caller's job),
            # use it directly. Falling back to the inventory lookup
            # was the source of the "kato cloned the repo but edited
            # my dev-una checkout" bug — the clone happened, but the
            # agent ran somewhere else.
            spawn_cwd = (
                normalized_text(getattr(repository, 'local_path', '') or '')
                if repository is not None
                else self._review_repository_local_path(review_context)
            )
            kwargs = dict(
                task_id=review_context.task_id,
                task_summary=review_context.task_summary,
                repository_local_path=spawn_cwd,
            )
            singular_args = lambda c: (c, review_context.branch_name)  # noqa: E731
        else:
            kwargs = dict(
                task_id=review_context.task_id,
                task_summary=review_context.task_summary,
            )
            singular_args = lambda c: (  # noqa: E731
                c, review_context.branch_name, review_context.agent_session_id,
            )
        if hasattr(backend, 'fix_review_comments'):
            plural_args = (comments, review_context.branch_name)
            if not streaming:
                # Implementation-service signature carries agent_session_id
                # as a positional, plural method matches.
                plural_args = (
                    comments, review_context.branch_name, review_context.agent_session_id,
                )
            try:
                return backend.fix_review_comments(
                    *plural_args, **kwargs, mode=mode,
                ) or {}
            except TypeError:
                # Older test stub without ``mode`` kwarg — fall back
                # to the legacy signature. Service still skips the
                # push for question batches, so the agent's answer
                # text simply travels through the fix-mode prompt.
                return backend.fix_review_comments(
                    *plural_args, **kwargs,
                ) or {}
        # Fanout: call singular per-comment, keep the last execution
        # dict (they should all carry the same success flag for one
        # batch). Best-effort — first failure raises so the caller
        # can react the same way it would with a real plural call.
        last_execution: dict[str, str | bool] = {}
        for comment in comments:
            last_execution = backend.fix_review_comment(
                *singular_args(comment), **kwargs,
            ) or {}
            if not last_execution.get(ImplementationFields.SUCCESS, False):
                return last_execution
        return last_execution

    def _provision_workspace_clone(self, repository, review_context):
        """Return a copy of ``repository`` re-pointed at the per-task clone.

        Each in-flight review-fix gets its own workspace clone so parallel
        workers can't corrupt each other's git state on the same on-disk
        repo (index.lock collisions, half-finished rebases, branch flip-
        flops between tasks). The original ``repository`` (the operator's
        shared checkout) is never mutated.

        Clones EVERY repo the task touches — not just the repo the
        comment was posted on. The original implementation cloned only
        the comment's repo, which broke multi-repo tasks: on a fresh
        machine, Claude opened on a workspace that only contained
        repo A, but the task spans repos A/B/C, and the fix usually
        needs cross-repo context. Mirrors the initial-task path
        (``TaskPreflightService._prepare_task_start`` →
        ``_resolve_task_repositories`` →
        ``_provision_workspace_clones``).

        Falls through to the original ``repository`` when no workspace
        manager is configured — preserves the legacy single-repo
        behaviour for setups that never opted into workspaces.
        """
        if self._workspace_manager is None:
            return repository
        try:
            from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )

            task = self._task_for_workspace_clone(review_context, repository)
            try:
                task_repositories = self._repository_service.resolve_task_repositories(task)
            except Exception:
                # Tag/description resolution couldn't pin the repo set
                # — degrade gracefully to "just the comment's repo" so
                # the fix still proceeds. Logged so the operator can
                # see why cross-repo context might be missing.
                self.logger.exception(
                    'failed to resolve task repositories for review-fix on '
                    'task %s; cloning only the comment repo (%s) — multi-repo '
                    'context will be missing from the agent workspace',
                    review_context.task_id,
                    review_context.repository_id,
                )
                task_repositories = [repository]
            else:
                # ``resolve_task_repositories`` may return repos that
                # don't include the comment's repo (e.g. the comment
                # is on a repo not tagged on the task). Always clone
                # the comment repo as well so the fix branch has a
                # workspace to land on.
                if not any(
                    getattr(r, 'id', '') == review_context.repository_id
                    for r in task_repositories
                ):
                    task_repositories = [*task_repositories, repository]
            provisioned = provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task,
                task_repositories,
            )
            # Return the comment's repo from the provisioned list so
            # the rest of the review-fix flow (branch checkout, push,
            # etc.) operates on its workspace clone, not the operator's
            # shared checkout.
            for clone in provisioned or []:
                if getattr(clone, 'id', '') == review_context.repository_id:
                    return clone
            return repository
        except Exception:
            self.logger.exception(
                'failed to provision per-task workspace clone for review-fix '
                'on task %s; falling back to shared repository checkout',
                review_context.task_id,
            )
            return repository

    def _task_for_workspace_clone(self, review_context, repository):
        """Return a Task-like object good enough for resolve_task_repositories.

        Prefers the live ticket-platform task (carries tags +
        description, which drive the multi-repo resolution) and falls
        back to a SimpleNamespace built from ``review_context`` if the
        platform lookup fails. The fallback only resolves correctly via
        the tag path or the single-repo short-circuit — multi-repo
        description matching needs the full description string.

        Walks BOTH the assigned queue and the review queue. The
        original implementation only looked at the review queue,
        which broke this exact symptom: a task still flagged "in
        progress" (not yet "in review") that fires a review comment
        was never matched here, the SimpleNamespace fallback kicked
        in with empty tags, and ``resolve_task_repositories``
        single-repo-short-circuited to the comment's repo. The agent
        then ran with only that repo cloned, even though the task
        was tagged for several.
        """
        _queue_labels = {
            'get_assigned_tasks': 'assigned',
            'get_review_tasks': 'review',
        }

        def _log_queue_error(queue_name: str) -> None:
            self.logger.exception(
                'failed to load %s tasks for workspace-clone resolution '
                '(task %s); will try other queues / stub task',
                _queue_labels.get(queue_name, queue_name), review_context.task_id,
            )

        task = find_task_by_id(
            self._task_service,
            review_context.task_id,
            queues=('get_assigned_tasks', 'get_review_tasks'),
            on_error=_log_queue_error,
        )
        if task is not None:
            return task
        from types import SimpleNamespace
        return SimpleNamespace(
            id=review_context.task_id,
            summary=review_context.task_summary,
            description='',
            tags=[],
        )

    def _review_repository_local_path(
        self,
        review_context: ReviewFixContext,
    ) -> str:
        try:
            repository = self._repository_service.get_repository(
                review_context.repository_id,
            )
        except Exception:
            return ''
        return normalized_text(getattr(repository, 'local_path', '') or '')

    def _publish_review_comments_batch_fix(
        self,
        comments: list[ReviewComment],
        repository,
        review_context: ReviewFixContext,
        execution: dict[str, str | bool],
    ) -> None:
        """Push once, then per-comment reply + resolve.

        Single push covers every comment in the batch — one commit
        on the task branch addresses them all. After the push lands,
        the platform-side bookkeeping (reply, resolve) is per-comment
        and best-effort: a 4xx on one reply doesn't roll back the
        actual code fix or affect the other comments.
        """
        self.logger.info(
            'publishing review fix for pull request %s (%d comment(s)) on branch %s',
            comments[0].pull_request_id,
            len(comments),
            review_context.branch_name,
        )
        self._repository_service.publish_review_fix(
            repository,
            review_context.branch_name,
            self._review_fix_commit_message(),
        )
        # Per-comment reply / resolve. Each call is best-effort —
        # one comment's failed reply doesn't stop the next comment's
        # reply from being attempted. The fix is already on the
        # remote either way.
        for comment in comments:
            try:
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
            except Exception:
                self.logger.exception(
                    'failed to post reply to review comment %s on pull request %s; '
                    'fix has been pushed but the reply will need manual posting',
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

    def _review_fix_produced_changes(
        self, repository, head_before_agent: str,
    ) -> bool:
        """True when the agent committed something OR left dirty edits.

        Two ways the agent legitimately moves the needle:

          * It committed directly on the task branch — HEAD moves.
          * It left edits in the working tree — kato's commit step
            (``_commit_branch_changes_if_needed``) will pick them
            up during publish.

        Either is enough to consider the fix real. Both absent means
        the agent ran but didn't change anything — we refuse to
        publish in that case so we don't post the misleading "kato
        addressed this" reply on top of someone else's prior commits.

        Defensively returns ``True`` when the wired repository
        service doesn't expose the two helpers — preserves the
        legacy behaviour for any test stub or custom wrapper that
        hasn't been migrated yet, so the new check never silently
        breaks an existing flow.
        """
        head_sha_fn = getattr(self._repository_service, 'current_head_sha', None)
        dirty_fn = getattr(self._repository_service, 'has_dirty_working_tree', None)
        if not callable(head_sha_fn) or not callable(dirty_fn):
            return True
        try:
            head_now = head_sha_fn(repository)
        except Exception:
            return True
        head_changed = bool(head_now and head_now != head_before_agent)
        if head_changed:
            return True
        try:
            return bool(dirty_fn(repository))
        except Exception:
            return True

    def _publish_review_no_changes(
        self,
        comments: list[ReviewComment],
        repository,
        review_context: ReviewFixContext,
    ) -> None:
        """Reply to each comment explaining the fix produced no changes.

        Posts a clearly-worded "no changes were made" body so the
        reviewer knows kato saw the comment and concluded nothing
        needed editing — without the misleading "kato addressed
        this and pushed a follow-up update" template. Does NOT
        resolve the thread: comments where Claude refused to act
        belong in front of a human, not silently closed.

        Best-effort per-comment: a 4xx on one reply doesn't stop
        the next comment's reply.
        """
        body = (
            'Kato ran an agent against this comment but produced no '
            'commits and left the working tree clean. The comment '
            'has not been resolved — please review the agent\'s '
            'reasoning in the planning UI and either re-prompt with '
            'more context, edit the file directly, or resolve the '
            'comment yourself if no change is needed.'
        )
        for comment in comments:
            try:
                self._repository_service.reply_to_review_comment(
                    repository, comment, body,
                )
                self.logger.warning(
                    'review-fix produced no changes for comment %s on '
                    'pull request %s; posted "no changes" reply, did '
                    'NOT resolve',
                    comment.comment_id,
                    comment.pull_request_id,
                )
            except Exception:
                self.logger.exception(
                    'review-fix produced no changes for comment %s on '
                    'pull request %s, AND posting the "no changes" '
                    'reply failed; the comment is left in its original '
                    'state',
                    comment.comment_id,
                    comment.pull_request_id,
                )

    def _publish_review_comment_answers(
        self,
        comments: list[ReviewComment],
        repository,
        review_context: ReviewFixContext,
        execution: dict[str, str | bool],
    ) -> None:
        """Reply to each question with the agent's answer text.

        Differences from ``_publish_review_comments_batch_fix``:
        - No ``publish_review_fix`` call. Nothing was edited.
        - Reply body is prefixed with a clear "no code changed" disclaimer
          so the reviewer cannot mistake an answer for a push-backed fix,
          even if the LLM's output text claims otherwise.
        - The thread is intentionally NOT resolved. No code changed, so
          the comment stays open for the reviewer to read the answer,
          verify it, and close the thread themselves. Auto-resolving an
          unanswered fix request (misclassified as a question) would
          make the problem invisible.
        """
        self.logger.info(
            'answering pull request %s (%d question(s)) on branch %s — no push, no resolve',
            comments[0].pull_request_id,
            len(comments),
            review_context.branch_name,
        )
        answer_body = review_comment_answer_body(execution)
        for comment in comments:
            try:
                self._repository_service.reply_to_review_comment(
                    repository,
                    comment,
                    answer_body,
                )
                self.logger.info(
                    'replied to review question %s on pull request %s — thread left open',
                    comment.comment_id,
                    comment.pull_request_id,
                )
            except Exception:
                self.logger.exception(
                    'failed to post answer to review question %s on pull request %s; '
                    'the question is unanswered, re-open the thread to retry',
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


def _record_review_fix_completed(comments, review_context) -> None:
    """Append a review_fix_completed audit record. Best-effort.

    The review-fix flow doesn't open a new PR — it pushes to the
    existing branch and replies to the comment(s). We record the
    pull-request id alongside the task / repo / branch so the
    operator can correlate ``./kato history`` entries with the PR
    review thread.
    """
    from kato_core_lib.helpers.audit_log_utils import (
        EVENT_REVIEW_FIX_COMPLETED,
        OUTCOME_SUCCESS,
        append_audit_event,
    )

    pr_ids = sorted({
        str(getattr(comment, 'pull_request_id', '') or '')
        for comment in comments
        if getattr(comment, 'pull_request_id', '')
    })
    append_audit_event(
        event=EVENT_REVIEW_FIX_COMPLETED,
        task_id=str(getattr(review_context, 'task_id', '') or ''),
        ticket_summary=str(getattr(review_context, 'task_summary', '') or ''),
        repositories=[str(getattr(review_context, 'repository_id', '') or '')]
        if getattr(review_context, 'repository_id', '') else [],
        branch=str(getattr(review_context, 'branch_name', '') or ''),
        pr_url=', '.join(pr_ids),
        outcome=OUTCOME_SUCCESS,
    )
