import types
import unittest
from unittest.mock import Mock

from requests import HTTPError

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.helpers.review_comment_utils import review_comment_fixed_comment
from tests.utils import build_review_comment, build_task


class ReviewCommentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = types.SimpleNamespace(
            get_review_tasks=Mock(return_value=[]),
            add_comment=Mock(),
        )
        self.implementation_service = types.SimpleNamespace(
            fix_review_comment=Mock(
                return_value={
                    ImplementationFields.SUCCESS: True,
                }
            ),
        )
        self.repository = types.SimpleNamespace(
            id='client',
            owner='workspace',
            repo_slug='repo',
            provider_base_url='https://api.bitbucket.org/2.0',
        )
        self.repository_service = types.SimpleNamespace(
            get_repository=Mock(return_value=self.repository),
            resolve_task_repositories=Mock(return_value=[self.repository]),
            prepare_task_branches=Mock(),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            restore_task_repositories=Mock(),
            build_branch_name=Mock(return_value='PROJ-1'),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
        )
        self.state_registry = AgentStateRegistry()
        self.service = ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            self.state_registry,
        )

    def test_process_review_comment_raises_for_unknown_pull_request(self) -> None:
        comment = build_review_comment(pull_request_id='17')

        with self.assertRaisesRegex(ValueError, 'unknown pull request id: 17'):
            self.service.process_review_comment(comment)

    def test_review_fix_streaming_runner_spawns_at_workspace_clone_not_inventory(self) -> None:
        # Regression: the review-fix streaming runner used to look
        # up the spawn ``repository_local_path`` from the inventory
        # via ``get_repository(repository_id)`` — which returns the
        # operator's REPOSITORY_ROOT_PATH checkout, NOT the per-task
        # workspace clone. So Claude cloned the repo into
        # ``KATO_WORKSPACES_ROOT`` (good) and then edited the
        # operator's dev-una checkout (very bad). This test pins the
        # spawn cwd to the workspace clone's ``local_path`` that
        # ``_provision_workspace_clone`` produced.
        from kato_core_lib.data_layers.service.review_comment_service import (
            ReviewFixContext,
        )
        from pathlib import Path

        # Inventory entry — operator's local checkout.
        inventory_repo = types.SimpleNamespace(
            id='admin-client',
            owner='workspace',
            repo_slug='repo',
            local_path='C:/Codes/dev-una/admin-client',  # <-- DO NOT spawn here
            provider_base_url='https://api.bitbucket.org/2.0',
        )
        # Workspace clone — what kato should actually spawn against.
        workspace_clone_path = 'C:/Codes/dev-kato/PROJ-9/admin-client'
        self.repository_service.get_repository = Mock(return_value=inventory_repo)
        self.repository_service.resolve_task_repositories = Mock(
            return_value=[inventory_repo],
        )
        self.repository_service.ensure_clone = Mock()
        # Stub a workspace_manager that produces the workspace
        # clone path; ``provision_task_workspace_clones`` rewrites
        # ``local_path`` on a copy of inventory_repo to this path.
        workspace_manager = Mock()
        workspace_manager.repository_path = Mock(
            side_effect=lambda task_id, repo_id: Path(workspace_clone_path),
        )
        # Streaming runner that records the spawn cwd it was given.
        recorded = {}
        def _capture(*args, **kwargs):
            recorded.update(kwargs)
            return {ImplementationFields.SUCCESS: True}
        runner = types.SimpleNamespace(fix_review_comment=Mock(side_effect=_capture))

        service = ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            self.state_registry,
            planning_session_runner=runner,
            use_streaming_for_review_fixes=True,
            workspace_manager=workspace_manager,
        )
        # Bypass the change-detection gate (a separate test) so we
        # can focus on the spawn cwd.
        self.repository_service.current_head_sha = Mock(
            side_effect=['head-before', 'head-after'],
        )
        self.repository_service.has_dirty_working_tree = Mock(return_value=False)

        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='please rename this variable',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'admin-client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-9 thing',
            },
            'feature/proj-9/admin-client',
            session_id='conv-1',
            task_id='PROJ-9',
            task_summary='thing',
        )
        # ``build_branch_name`` needs to be callable for both inventory
        # repos and workspace-clone copies — return the branch name
        # unconditionally for the test.
        self.repository_service.build_branch_name = Mock(
            return_value='feature/proj-9/admin-client',
        )

        service.process_review_comment(comment)

        # The spawn cwd MUST be the workspace clone path, not the
        # inventory's REPOSITORY_ROOT_PATH local_path. The exact
        # spelling matters: a forward-slashes "C:/Codes/dev-kato/..."
        # is what ``Path(workspace_clone_path)`` stringifies to.
        self.assertIn('repository_local_path', recorded)
        self.assertEqual(
            recorded['repository_local_path'],
            workspace_clone_path,
        )
        self.assertNotEqual(
            recorded['repository_local_path'],
            'C:/Codes/dev-una/admin-client',
        )

    def test_process_review_comment_refuses_when_agent_made_no_changes(self) -> None:
        # Regression: previously, when Claude ran but committed
        # nothing AND left a clean tree, kato still posted "Kato
        # addressed this review comment and pushed a follow-up
        # update" and resolved the thread — because the publish
        # check only verified the BRANCH had commits ahead of base,
        # not that THIS review-fix added anything. With the
        # head-sha snapshot + dirty-tree probe, kato now refuses to
        # publish, posts a clear "no changes were made" reply, and
        # leaves the thread open for human review.
        self.service.logger = Mock()
        # Same SHA before + after means "agent didn't commit
        # anything"; clean tree means "no dirty edits either".
        self.repository_service.current_head_sha = Mock(return_value='same-sha')
        self.repository_service.has_dirty_working_tree = Mock(return_value=False)
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please add a feature flag check.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
            },
            'feature/proj-1/client',
            session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        # No-changes is now a graceful terminal path — no exception.
        self.service.process_review_comment(comment)

        # The "kato addressed this and pushed" path must NOT run.
        self.repository_service.publish_review_fix.assert_not_called()
        # The thread must NOT be resolved — that was the lie.
        self.repository_service.resolve_review_comment.assert_not_called()
        # A reply WAS posted, but with the explicit "no changes"
        # wording so the reviewer knows kato saw the comment and
        # concluded nothing needed editing.
        self.repository_service.reply_to_review_comment.assert_called_once()
        reply_body = self.repository_service.reply_to_review_comment.call_args.args[2]
        self.assertIn('produced no commits', reply_body)
        self.assertIn('not been resolved', reply_body)
        # Comment must be marked processed so it is not retried.
        self.assertTrue(
            self.state_registry.is_review_comment_processed(
                'client', comment.pull_request_id, comment.comment_id,
            ),
            'comment should be marked processed so the scan loop stops retrying',
        )

    def test_process_review_comment_publishes_when_head_moves(self) -> None:
        # The mirror of the regression: when the agent DID commit
        # something (HEAD moves between the pre-agent snapshot and
        # the post-agent check), the publish + reply + resolve path
        # runs as before. Locks the safe-default behaviour so a
        # future tweak to the change-detection heuristic can't
        # accidentally turn off real fixes.
        self.service.logger = Mock()
        self.repository_service.current_head_sha = Mock(
            side_effect=['sha-before', 'sha-after'],
        )
        self.repository_service.has_dirty_working_tree = Mock(return_value=False)
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
            },
            'feature/proj-1/client',
            session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        self.service.process_review_comment(comment)

        self.repository_service.publish_review_fix.assert_called_once()
        self.repository_service.resolve_review_comment.assert_called_once()

    def test_process_review_comment_processes_fix_and_marks_comment_processed(self) -> None:
        call_order: list[str] = []
        self.service.logger = Mock()
        self.repository_service.reply_to_review_comment.side_effect = (
            lambda *args, **kwargs: call_order.append('reply')
        )
        self.repository_service.resolve_review_comment.side_effect = (
            lambda *args, **kwargs: call_order.append('resolve')
        )
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
            },
            'feature/proj-1/client',
            session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        result = self.service.process_review_comment(comment)

        self.assertEqual(
            result,
            {
                'status': 'updated',
                'pull_request_id': '17',
                'branch_name': 'feature/proj-1/client',
                PullRequestFields.REPOSITORY_ID: 'client',
            },
        )
        self.repository_service.prepare_task_branches.assert_called_once_with(
            [self.repository],
            {'client': 'feature/proj-1/client'},
        )
        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository,
            'feature/proj-1/client',
            'Address review comments',
        )
        self.repository_service.reply_to_review_comment.assert_called_once()
        self.repository_service.resolve_review_comment.assert_called_once_with(
            self.repository,
            comment,
        )
        self.assertEqual(call_order, ['reply', 'resolve'])
        reply_body = self.repository_service.reply_to_review_comment.call_args.args[2]
        self.assertIn('Kato addressed this review comment', reply_body)
        self.task_service.add_comment.assert_called_once_with(
            'PROJ-1',
            review_comment_fixed_comment(comment),
        )
        from kato_core_lib.helpers.mission_logging_utils import _CYAN, _GREEN, _RESET
        self.assertEqual(
            self.service.logger.info.call_args_list[0].args,
            ('%s>> Mission %s: %s%s', _GREEN, 'PROJ-1', 'starting mission', _RESET),
        )
        self.assertEqual(
            self.service.logger.info.call_args_list[1].args,
            (
                '%s>> Mission %s: %s%s',
                _CYAN,
                'PROJ-1',
                'starting pull request 17 (1 comment(s) in batch)',
                _RESET,
            ),
        )
        end_log = next(
            (c for c in self.service.logger.info.call_args_list if c.args and 'done working on mission' in str(c.args)),
            None,
        )
        self.assertIsNotNone(end_log, '"done working on mission" log missing')
        comment_end_log = next(
            (
                c
                for c in self.service.logger.info.call_args_list
                if c.args and 'completed pull request 17 (1 comment(s) in batch)' in str(c.args)
            ),
            None,
        )
        self.assertIsNotNone(comment_end_log, '"completed pull request" log missing')
        self.assertEqual(
            comment_end_log.args,
            (
                '%s<< Mission %s: %s%s',
                _CYAN,
                'PROJ-1',
                'completed pull request 17 (1 comment(s) in batch)',
                _RESET,
            ),
        )
        self.assertEqual(
            end_log.args,
            ('%s<< Mission %s: %s%s', _GREEN, 'PROJ-1', 'done working on mission', _RESET),
        )
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', '99')
        )
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', 'comment:99')
        )

    def test_provision_workspace_clone_clones_every_task_repo_not_just_comment_repo(self) -> None:
        # Regression: when a multi-repo task gets a review comment on
        # a fresh machine, kato used to clone only the repo the
        # comment was posted on. The agent then opened on a workspace
        # missing the other repos the task touches and couldn't make
        # consistent cross-repo fixes. The provisioner now resolves
        # the FULL task repo set and clones every one of them, while
        # still returning the comment's repo (re-pointed at its
        # workspace clone) so the rest of the review-fix flow lands
        # on the right branch.
        from kato_core_lib.data_layers.service.review_comment_service import (
            ReviewFixContext,
        )

        # Three-repo task; comment is on repo 'admin-client'.
        repo_client = types.SimpleNamespace(id='admin-client', local_path='/inv/admin-client')
        repo_backend = types.SimpleNamespace(id='admin-backend', local_path='/inv/admin-backend')
        repo_core = types.SimpleNamespace(id='core-lib', local_path='/inv/core-lib')
        all_task_repos = [repo_client, repo_backend, repo_core]
        self.repository_service.resolve_task_repositories = Mock(
            return_value=all_task_repos,
        )
        # Stub task lookup so the provisioner picks the real Task —
        # which carries tags/description that resolve_task_repositories
        # uses on the production path.
        task = build_task(
            task_id='PROJ-9',
            summary='Cross-repo refactor',
            description='Touches admin-client, admin-backend, core-lib',
        )
        self.task_service.get_review_tasks = Mock(return_value=[task])

        # Fake workspace manager: records what it was asked to create.
        from pathlib import Path
        workspace_manager = Mock()
        workspace_manager.repository_path = Mock(
            side_effect=lambda task_id, repo_id: Path(f'/wks/{task_id}/{repo_id}'),
        )
        # Stub ensure_clone on repository_service since
        # provision_task_workspace_clones routes the actual clone
        # through there (and we don't want to hit real git in tests).
        self.repository_service.ensure_clone = Mock()

        service = ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            self.state_registry,
            workspace_manager=workspace_manager,
        )

        review_context = ReviewFixContext(
            task_id='PROJ-9',
            task_summary='Cross-repo refactor',
            repository_id='admin-client',
            branch_name='feature/PROJ-9',
            session_id='conv-1',
            pull_request_title='PROJ-9 Cross-repo refactor',
        )

        result = service._provision_workspace_clone(repo_client, review_context)

        # All three repos passed to workspace_manager.create — the
        # operator-visible "what folders does this task own?" list.
        workspace_manager.create.assert_called_once()
        kwargs = workspace_manager.create.call_args.kwargs
        self.assertEqual(kwargs['task_id'], 'PROJ-9')
        self.assertEqual(
            sorted(kwargs['repository_ids']),
            ['admin-backend', 'admin-client', 'core-lib'],
        )
        # Returned repo is the comment's repo, re-pointed at the
        # workspace clone (so the fix branch / push lands there).
        self.assertEqual(result.id, 'admin-client')
        self.assertEqual(result.local_path, '/wks/PROJ-9/admin-client')

    def test_provision_workspace_clone_finds_task_in_assigned_queue_too(self) -> None:
        # Regression: ``_task_for_workspace_clone`` only looked at
        # ``get_review_tasks``. If a comment fired while the task
        # was still ``in progress`` (not yet ``in review``) the
        # task was never found, the SimpleNamespace fallback ran
        # with empty tags, ``resolve_task_repositories``
        # single-repo-short-circuited to the comment's repo, and
        # only that one repo got cloned. Mirror of the earlier
        # multi-repo test, but with the task in the assigned queue.
        from kato_core_lib.data_layers.service.review_comment_service import (
            ReviewFixContext,
        )
        from pathlib import Path

        repo_client = types.SimpleNamespace(id='admin-client', local_path='/inv/admin-client')
        repo_backend = types.SimpleNamespace(id='admin-backend', local_path='/inv/admin-backend')
        repo_core = types.SimpleNamespace(id='core-lib', local_path='/inv/core-lib')
        all_task_repos = [repo_client, repo_backend, repo_core]
        self.repository_service.resolve_task_repositories = Mock(
            return_value=all_task_repos,
        )
        task = build_task(
            task_id='PROJ-12',
            summary='Cross-repo work in progress',
            description='Touches admin-client, admin-backend, core-lib',
        )
        # Task is in the ASSIGNED queue, NOT review. The old code
        # missed this case. Empty review queue + populated assigned
        # queue is the production shape: the operator commented on
        # a draft PR while still actively working the task.
        self.task_service.get_assigned_tasks = Mock(return_value=[task])
        self.task_service.get_review_tasks = Mock(return_value=[])

        workspace_manager = Mock()
        workspace_manager.repository_path = Mock(
            side_effect=lambda task_id, repo_id: Path(f'/wks/{task_id}/{repo_id}'),
        )
        self.repository_service.ensure_clone = Mock()

        service = ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            self.state_registry,
            workspace_manager=workspace_manager,
        )

        review_context = ReviewFixContext(
            task_id='PROJ-12',
            task_summary='Cross-repo work in progress',
            repository_id='admin-client',
            branch_name='feature/PROJ-12',
            session_id='conv-1',
            pull_request_title='PROJ-12 thing',
        )

        result = service._provision_workspace_clone(repo_client, review_context)

        # All three repos cloned even though the task was in the
        # assigned queue, not the review queue.
        workspace_manager.create.assert_called_once()
        kwargs = workspace_manager.create.call_args.kwargs
        self.assertEqual(
            sorted(kwargs['repository_ids']),
            ['admin-backend', 'admin-client', 'core-lib'],
        )
        self.assertEqual(result.id, 'admin-client')
        self.assertEqual(result.local_path, '/wks/PROJ-12/admin-client')

    def test_process_review_comment_treats_resolution_conflict_as_non_fatal(self) -> None:
        self.service.logger = Mock()
        self.repository_service.resolve_review_comment.side_effect = HTTPError(
            '409 Client Error: Conflict',
            response=types.SimpleNamespace(status_code=409),
        )
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
            },
            'feature/proj-1/client',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        result = self.service.process_review_comment(comment)

        self.assertEqual(result['status'], 'updated')
        self.repository_service.publish_review_fix.assert_called_once()
        self.repository_service.reply_to_review_comment.assert_called_once()
        self.repository_service.resolve_review_comment.assert_called_once_with(
            self.repository,
            comment,
        )
        self.repository_service.restore_task_repositories.assert_not_called()
        self.service.logger.warning.assert_called_once()
        self.assertIn(
            'skipped resolving review comment %s on pull request %s',
            [call.args[0] for call in self.service.logger.info.call_args_list],
        )
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', '99')
        )

    def test_process_review_comment_treats_already_resolved_runtime_error_as_non_fatal(self) -> None:
        self.service.logger = Mock()
        self.repository_service.resolve_review_comment.side_effect = RuntimeError(
            'review thread is already resolved'
        )
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
            },
            'feature/proj-1/client',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        result = self.service.process_review_comment(comment)

        self.assertEqual(result['status'], 'updated')
        self.repository_service.restore_task_repositories.assert_not_called()
        self.service.logger.warning.assert_called_once()
        self.assertTrue(
            self.state_registry.is_review_comment_processed('client', '17', '99')
        )

    def test_process_review_comment_restores_repository_when_publish_fails(self) -> None:
        self.repository_service.publish_review_fix.side_effect = RuntimeError('push failed')
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
            },
            'feature/proj-1/client',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        with self.assertRaisesRegex(RuntimeError, 'push failed'):
            self.service.process_review_comment(comment)

        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.repository],
            force=True,
        )
        self.repository_service.reply_to_review_comment.assert_not_called()
        self.repository_service.resolve_review_comment.assert_not_called()
        self.assertFalse(self.state_registry.is_review_comment_processed('client', '17', '99'))

    def test_get_new_pull_request_comments_discovers_pull_request_context_for_review_task(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        processed = ReviewComment(
            pull_request_id='17',
            comment_id='98',
            author='reviewer',
            body='Already handled.',
        )
        duplicate_a = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        duplicate_b = ReviewComment(
            pull_request_id='17',
            comment_id='100',
            author='reviewer',
            body='Please rename this variable too.',
        )
        setattr(duplicate_a, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(duplicate_a, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        setattr(duplicate_b, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(duplicate_b, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [
            processed,
            duplicate_a,
            duplicate_b,
        ]
        self.state_registry.mark_review_comment_processed('client', '17', '98')

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['100'])
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.repository_service.find_pull_requests.assert_called_once_with(
            self.repository,
            source_branch='PROJ-1',
            title_prefix='PROJ-1 ',
        )
        self.repository_service.get_repository.assert_called_once_with('client')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '17',
        )
        self.assertEqual(
            self.state_registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                'branch_name': 'PROJ-1',
                'task_id': 'PROJ-1',
                'task_summary': 'fix it already',
            },
        )

    def test_get_new_pull_request_comments_skips_thread_with_prior_kato_reply(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        reviewer_comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        kato_reply = ReviewComment(
            pull_request_id='17',
            comment_id='100',
            author='kato',
            body='Kato addressed this review comment and pushed a follow-up update.',
        )
        for comment in (reviewer_comment, kato_reply):
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [
            reviewer_comment,
            kato_reply,
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(comments, [])

    def test_get_new_pull_request_comments_skips_processed_resolution_target(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        follow_up_comment = ReviewComment(
            pull_request_id='17',
            comment_id='100',
            author='reviewer',
            body='Please rename this variable too.',
        )
        setattr(follow_up_comment, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(follow_up_comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [
            follow_up_comment,
        ]
        self.state_registry.mark_review_comment_processed('client', '17', 'thread:thread-1')

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(comments, [])

    def test_get_new_pull_request_comments_adds_repository_id_before_remembering_api_discovered_pull_request(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(task_id='PROJ-1', tags=['repo:client'])
        ]
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(comments, [])
        self.assertEqual(
            self.state_registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                'branch_name': 'PROJ-1',
                'task_id': 'PROJ-1',
                'task_summary': 'fix it already',
            },
        )

    def test_get_new_pull_request_comments_uses_task_pull_request_url_before_api_lookup(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(
                task_id='PROJ-1',
                description=(
                    'Requested change.\n\n'
                    'Pull request created: '
                    'https://bitbucket.org/workspace/repo/pull-requests/17'
                ),
                tags=['repo:client'],
            )
        ]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='17',
                comment_id='99',
                author='reviewer',
                body='Please rename this variable.',
            )
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['99'])
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.repository_service.find_pull_requests.assert_not_called()
        self.repository_service.get_repository.assert_called_once_with('client')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '17',
        )
        self.assertEqual(
            self.state_registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'PROJ-1',
                'task_id': 'PROJ-1',
                'task_summary': 'fix it already',
            },
        )

    def test_get_new_pull_request_comments_strips_markdown_autolink_wrapper_from_pull_request_url(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(
                task_id='PROJ-1',
                description=(
                    'Requested change.\n\n'
                    'Pull request created: '
                    '<https://bitbucket.org/workspace/repo/pull-requests/17>'
                ),
                tags=['repo:client'],
            )
        ]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='17',
                comment_id='99',
                author='reviewer',
                body='Please rename this variable.',
            )
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['99'])
        self.repository_service.find_pull_requests.assert_not_called()
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '17',
        )

    def test_get_new_pull_request_comments_uses_task_comment_pull_request_url_before_api_lookup(self) -> None:
        self.task_service.get_review_tasks.return_value = [
            build_task(
                task_id='PROJ-1',
                description='Requested change.',
                comments=[
                    {
                        'author': 'OpenHands',
                        'body': (
                            'Pull request created: '
                            'https://bitbucket.org/workspace/repo/pull-requests/18'
                        ),
                    }
                ],
                tags=['repo:client'],
            )
        ]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='18',
                comment_id='100',
                author='reviewer',
                body='Please rename this variable.',
            )
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual([comment.comment_id for comment in comments], ['100'])
        self.repository_service.find_pull_requests.assert_not_called()
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.repository,
            '18',
        )
