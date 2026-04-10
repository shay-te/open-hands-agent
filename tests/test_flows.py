"""
Flow tests for the three main Kato workflows as defined in README.md.

Each test class maps to one ### section:
  - ### Startup Flow
  - ### Task Fix Flow
  - ### Review Comment Fix Flow

The private helpers hold the assertions. Test methods call them for every
supported provider combination. When a README flow step changes, update
the corresponding helper so the tests stay in sync.
"""
from __future__ import annotations

import types
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call

from kato.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
    TaskCommentFields,
)
from kato.data_layers.data_access.task_data_access import TaskDataAccess
from kato.data_layers.service.agent_service import AgentService
from kato.data_layers.service.agent_state_registry import AgentStateRegistry
from kato.data_layers.service.implementation_service import ImplementationService
from kato.data_layers.service.notification_service import NotificationService
from kato.data_layers.service.review_comment_service import ReviewCommentService
from kato.data_layers.service.task_service import TaskService
from kato.data_layers.service.task_state_service import TaskStateService
from kato.data_layers.service.testing_service import TestingService
from kato.validation.startup_dependency_validator import StartupDependencyValidator
from utils import build_review_comment, build_task, build_test_cfg


# ---------------------------------------------------------------------------
# Provider configuration tables
# ---------------------------------------------------------------------------

# All supported issue platforms and their config keys / state field names.
# Update here when a new platform is added or field names change.
ISSUE_PLATFORMS = {
    'youtrack': {
        'config_key': 'youtrack',
        'progress_state_field': 'State',
        'progress_state': 'In Progress',
        'review_state_field': 'State',
        'review_state': 'To Verify',
    },
    'jira': {
        'config_key': 'jira',
        'progress_state_field': 'status',
        'progress_state': 'In Progress',
        'review_state_field': 'status',
        'review_state': 'In Review',
    },
    'github': {
        'config_key': 'github_issues',
        'progress_state_field': 'labels',
        'progress_state': 'In Progress',
        'review_state_field': 'labels',
        'review_state': 'In Review',
    },
    'gitlab': {
        'config_key': 'gitlab_issues',
        'progress_state_field': 'labels',
        'progress_state': 'In Progress',
        'review_state_field': 'labels',
        'review_state': 'In Review',
    },
    'bitbucket': {
        'config_key': 'bitbucket_issues',
        'progress_state_field': 'state',
        'progress_state': 'open',
        'review_state_field': 'state',
        'review_state': 'resolved',
    },
}

# All supported git repository providers.
# Update here when a new provider is added.
REPO_PROVIDERS = {
    'bitbucket': 'https://bitbucket.example',
    'github': 'https://github.example/api/v3',
    'gitlab': 'https://gitlab.example/api/v4',
}


# ---------------------------------------------------------------------------
# ### Startup Flow
# ---------------------------------------------------------------------------

class StartupFlowTests(unittest.TestCase):
    """
    README ### Startup Flow step 4:
    Startup dependency validation checks repository connections, the active
    issue-platform connection, the main OpenHands connection, and the testing
    OpenHands connection unless OPENHANDS_SKIP_TESTING=true.
    """

    def _run_and_assert_startup_flow(
        self, issue_platform: str, *, skip_testing: bool = False
    ) -> None:
        """
        Verify that validate_connections() runs the four startup checks in the
        correct order for the given issue platform.

        Step 4a: repository connections validated first.
        Step 4b: active issue-platform connection validated second.
        Step 4c: main OpenHands connection validated third.
        Step 4d: testing OpenHands connection validated last (skipped when
                 OPENHANDS_SKIP_TESTING=true).
        """
        call_order: list[str] = []

        repos_validator = Mock()
        repos_validator.validate.side_effect = lambda: call_order.append(
            'validate_repositories'
        )

        task_service = SimpleNamespace(
            provider_name=issue_platform,
            max_retries=3,
            validate_connection=Mock(
                side_effect=lambda: call_order.append(f'validate_{issue_platform}')
            ),
        )
        impl_service = SimpleNamespace(
            max_retries=3,
            validate_connection=Mock(
                side_effect=lambda: call_order.append('validate_openhands')
            ),
        )
        testing_service = SimpleNamespace(
            max_retries=3,
            validate_connection=Mock(
                side_effect=lambda: call_order.append('validate_openhands_testing')
            ),
        )

        validator = StartupDependencyValidator(
            repos_validator, task_service, impl_service, testing_service, skip_testing
        )
        validator.validate(Mock())

        # Step 4a: repository connections validated first
        self.assertEqual(
            call_order[0],
            'validate_repositories',
            'repositories must be the first validation step',
        )

        # Step 4b: issue platform validated after repositories
        self.assertIn(f'validate_{issue_platform}', call_order)
        self.assertLess(
            call_order.index('validate_repositories'),
            call_order.index(f'validate_{issue_platform}'),
        )

        # Step 4c: main OpenHands validated after issue platform
        self.assertIn('validate_openhands', call_order)
        self.assertLess(
            call_order.index(f'validate_{issue_platform}'),
            call_order.index('validate_openhands'),
        )

        if skip_testing:
            # Step 4d omitted: OPENHANDS_SKIP_TESTING=true skips testing validation
            self.assertNotIn(
                'validate_openhands_testing',
                call_order,
                'testing OpenHands must not be validated when skip_testing=True',
            )
        else:
            # Step 4d: testing OpenHands validated last
            self.assertIn('validate_openhands_testing', call_order)
            self.assertLess(
                call_order.index('validate_openhands'),
                call_order.index('validate_openhands_testing'),
            )

    # --- one test per issue platform ---

    def test_startup_flow_youtrack(self) -> None:
        self._run_and_assert_startup_flow('youtrack')

    def test_startup_flow_jira(self) -> None:
        self._run_and_assert_startup_flow('jira')

    def test_startup_flow_github(self) -> None:
        self._run_and_assert_startup_flow('github')

    def test_startup_flow_gitlab(self) -> None:
        self._run_and_assert_startup_flow('gitlab')

    def test_startup_flow_bitbucket(self) -> None:
        self._run_and_assert_startup_flow('bitbucket')

    # --- with OPENHANDS_SKIP_TESTING=true ---

    def test_startup_flow_skips_testing_youtrack(self) -> None:
        self._run_and_assert_startup_flow('youtrack', skip_testing=True)

    def test_startup_flow_skips_testing_jira(self) -> None:
        self._run_and_assert_startup_flow('jira', skip_testing=True)

    def test_startup_flow_skips_testing_github(self) -> None:
        self._run_and_assert_startup_flow('github', skip_testing=True)

    def test_startup_flow_skips_testing_gitlab(self) -> None:
        self._run_and_assert_startup_flow('gitlab', skip_testing=True)

    def test_startup_flow_skips_testing_bitbucket(self) -> None:
        self._run_and_assert_startup_flow('bitbucket', skip_testing=True)


# ---------------------------------------------------------------------------
# ### Task Fix Flow
# ---------------------------------------------------------------------------

class TaskFixFlowTests(unittest.TestCase):
    """
    README ### Task Fix Flow: verify all 17 steps run in the correct order
    for every combination of issue platform and repository provider.
    """

    def _build_task_fix_services(self, issue_platform: str, repo_provider: str):
        """
        Wire up a complete AgentService stack with mocked external calls.
        Returns (agent_service, ticket_client, repository_service,
                 email_core_lib, call_order, ip_cfg).
        """
        call_order: list[str] = []
        cfg = build_test_cfg()
        cfg.kato.issue_platform = issue_platform
        ip = ISSUE_PLATFORMS[issue_platform]

        repository = types.SimpleNamespace(
            id='repo',
            display_name='Test Repo',
            local_path='.',
            destination_branch='main',
            provider_base_url=REPO_PROVIDERS[repo_provider],
        )

        # --- closures that record call order ---

        def track_add_comment(issue_id: str, comment: str) -> None:
            if 'started working on this task' in comment:
                call_order.append('started_comment')
            else:
                call_order.append('summary_comment')

        def track_move_state(issue_id: str, field_name: str, state_name: str) -> None:
            call_order.append(f'move:{field_name}:{state_name}')

        def resolve_repos(task):
            call_order.append('resolve_repositories')
            return [repository]

        def prepare_repos(repos):
            call_order.append('prepare_repositories')
            return repos

        def prepare_branches(repos, branches):
            call_order.append('prepare_branches')
            return repos

        def ensure_pushable(*args, **kwargs):
            call_order.append('validate_push_access')

        def ensure_publishable(*args, **kwargs):
            call_order.append('validate_publishability')

        def do_implement(*args, **kwargs):
            call_order.append('implement_task')
            return {
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conv-1',
                ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
                'summary': 'Files changed:\n- client/app.ts\n  Updated the flow.',
            }

        def do_test(*args, **kwargs):
            call_order.append('test_task')
            return {ImplementationFields.SUCCESS: True, 'summary': 'Tests passed.'}

        def do_create_pr(*args, **kwargs):
            call_order.append('create_pull_request')
            return {
                PullRequestFields.REPOSITORY_ID: repository.id,
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://example.com/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'PROJ-1',
                PullRequestFields.DESTINATION_BRANCH: 'main',
            }

        # --- build mocks ---

        ticket_client = types.SimpleNamespace(
            provider_name=issue_platform,
            max_retries=cfg.kato.retry.max_retries,
            get_assigned_tasks=Mock(return_value=[build_task(
                description='Fix the login flow in the client.',
            )]),
            add_comment=Mock(side_effect=track_add_comment),
            move_issue_to_state=Mock(side_effect=track_move_state),
            validate_connection=Mock(),
        )

        kato_client = types.SimpleNamespace(
            max_retries=cfg.kato.retry.max_retries,
            validate_connection=Mock(),
            validate_model_access=Mock(
                side_effect=lambda *a, **kw: call_order.append('validate_model_access')
            ),
            implement_task=Mock(side_effect=do_implement),
            test_task=Mock(side_effect=do_test),
        )

        repository_service = types.SimpleNamespace(
            _validate_inventory=Mock(),
            _validate_git_executable=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            resolve_task_repositories=Mock(side_effect=resolve_repos),
            prepare_task_repositories=Mock(side_effect=prepare_repos),
            prepare_task_branches=Mock(side_effect=prepare_branches),
            _ensure_branch_is_pushable=Mock(side_effect=ensure_pushable),
            _ensure_branch_has_task_changes=Mock(side_effect=ensure_publishable),
            destination_branch=Mock(return_value='main'),
            restore_task_repositories=Mock(),
            get_repository=Mock(return_value=repository),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            build_branch_name=Mock(return_value='PROJ-1'),
            create_pull_request=Mock(side_effect=do_create_pr),
        )

        ticket_cfg = getattr(cfg.kato, ip['config_key'])
        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        task_service = TaskService(ticket_cfg, task_data_access)
        task_state_service = TaskStateService(ticket_cfg, task_data_access)

        email_core_lib = Mock()
        email_core_lib.send.side_effect = lambda *a, **kw: call_order.append(
            'notification_sent'
        )
        notification_service = NotificationService(
            app_name='kato',
            email_core_lib=email_core_lib,
            failure_email_cfg=cfg.kato.failure_email,
            completion_email_cfg=cfg.kato.completion_email,
        )

        agent_service = AgentService(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=ImplementationService(kato_client),
            testing_service=TestingService(kato_client),
            repository_service=repository_service,
            notification_service=notification_service,
        )

        return (
            agent_service,
            ticket_client,
            repository_service,
            email_core_lib,
            call_order,
            ip,
            repository,
            task_service,
        )

    def _run_and_assert_task_fix_flow(
        self, issue_platform: str, repo_provider: str
    ) -> None:
        """
        Process one task and verify every README Task Fix Flow step fires in order.

        Step 1:  skip already-processed tasks (not exercised on happy path)
        Step 2:  validate model access before any repository work
        Step 3:  skip if blocking comment still active (not exercised on happy path)
        Step 4:  read full task context
        Step 5:  infer repositories from task summary / description
        Step 6:  validate repositories are ready on the destination branch
        Step 7:  build branch names and prepare branches locally
        Step 8:  (inside prepare_branches) fetch origin and rebase existing branch
        Step 9:  validate that task branches can be pushed
        Step 10: move issue to in-progress and add started comment
        Step 11: open implementation conversation in OpenHands
        Step 12: validate that branches contain publishable changes before testing
        Step 13: open testing conversation (skip_testing not set here)
        Step 14: commit / push / create pull requests
        Step 15: add pull-request summary comment back to the task
        Step 16: move to review state, mark task processed, send completion notification
        Step 17: store pull-request context for future review-comment handling
        """
        (
            agent_service,
            ticket_client,
            repository_service,
            email_core_lib,
            call_order,
            ip,
            repository,
            task_service,
        ) = self._build_task_fix_services(issue_platform, repo_provider)

        task = task_service.get_assigned_tasks()[0]
        result = agent_service.process_assigned_task(task)

        # Flow completed successfully
        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)

        # --- ordering assertions ---

        # Step 2 before step 5: model access validated before any repository work
        self.assertLess(
            call_order.index('validate_model_access'),
            call_order.index('resolve_repositories'),
            'model access must be validated before resolving repositories',
        )

        # Step 5 before step 6: repositories resolved then prepared
        self.assertLess(
            call_order.index('resolve_repositories'),
            call_order.index('prepare_repositories'),
            'repositories must be resolved before being prepared',
        )

        # Step 6 before step 7: repositories prepared before branches
        self.assertLess(
            call_order.index('prepare_repositories'),
            call_order.index('prepare_branches'),
            'repositories must be prepared before branches are set up',
        )

        # Step 7/8 before step 9: branches prepared before push-access check
        self.assertLess(
            call_order.index('prepare_branches'),
            call_order.index('validate_push_access'),
            'branches must be prepared before push access is validated',
        )

        # Step 9 before step 10: push access confirmed before moving to in-progress
        progress_key = f'move:{ip["progress_state_field"]}:{ip["progress_state"]}'
        self.assertLess(
            call_order.index('validate_push_access'),
            call_order.index(progress_key),
            'push access must be validated before moving to in-progress',
        )

        # Step 10 before step 11: moved to in-progress before implementation starts
        self.assertLess(
            call_order.index(progress_key),
            call_order.index('implement_task'),
            'task must be in-progress before implementation starts',
        )

        # Step 10 (started comment) before step 11
        self.assertLess(
            call_order.index('started_comment'),
            call_order.index('implement_task'),
            'started comment must precede implementation',
        )

        # Step 12 before step 13: publishability validated before testing
        self.assertLess(
            call_order.index('implement_task'),
            call_order.index('validate_publishability'),
            'publishability check must come after implementation',
        )
        self.assertLess(
            call_order.index('validate_publishability'),
            call_order.index('test_task'),
            'branches must be confirmed to have changes before testing',
        )

        # Step 13 before step 14: testing before PR creation
        self.assertLess(
            call_order.index('test_task'),
            call_order.index('create_pull_request'),
            'testing must pass before pull requests are created',
        )

        # Step 14 before step 15: PR created before summary comment
        self.assertLess(
            call_order.index('create_pull_request'),
            call_order.index('summary_comment'),
            'pull requests must be created before the summary comment is posted',
        )

        # Step 15 before step 16: summary comment before review-state transition
        review_key = f'move:{ip["review_state_field"]}:{ip["review_state"]}'
        self.assertLess(
            call_order.index('summary_comment'),
            call_order.index(review_key),
            'summary comment must be posted before moving to review state',
        )

        # Step 16 notification sent after review state
        self.assertLess(
            call_order.index(review_key),
            call_order.index('notification_sent'),
            'completion notification must be sent after review-state transition',
        )

        # --- provider-specific assertions ---

        # Step 10: correct progress-state field and value for this issue platform
        self.assertEqual(
            ticket_client.move_issue_to_state.call_args_list[0],
            call(task.id, ip['progress_state_field'], ip['progress_state']),
            f'wrong in-progress transition for {issue_platform}',
        )

        # Step 16: correct review-state field and value for this issue platform
        self.assertEqual(
            ticket_client.move_issue_to_state.call_args_list[1],
            call(task.id, ip['review_state_field'], ip['review_state']),
            f'wrong review-state transition for {issue_platform}',
        )

        # Step 14: create_pull_request called with the repository for this provider
        pr_repo_arg = repository_service.create_pull_request.call_args.args[0]
        self.assertEqual(
            pr_repo_arg.provider_base_url,
            REPO_PROVIDERS[repo_provider],
            f'wrong repository provider URL for {repo_provider}',
        )

        # Step 16: task marked processed in registry
        self.assertTrue(
            agent_service._state_registry.is_task_processed(task.id),
            'task must be marked processed after successful publish',
        )

        # Step 17: PR context stored in registry for future review-comment handling
        self.assertIn(
            '17',
            agent_service._state_registry.pull_request_context_map,
            'pull-request context must be stored in the state registry',
        )

    # --- one test per (issue_platform, repo_provider) combination ---

    def test_task_fix_flow_youtrack_bitbucket(self) -> None:
        self._run_and_assert_task_fix_flow('youtrack', 'bitbucket')

    def test_task_fix_flow_youtrack_github(self) -> None:
        self._run_and_assert_task_fix_flow('youtrack', 'github')

    def test_task_fix_flow_youtrack_gitlab(self) -> None:
        self._run_and_assert_task_fix_flow('youtrack', 'gitlab')

    def test_task_fix_flow_jira_bitbucket(self) -> None:
        self._run_and_assert_task_fix_flow('jira', 'bitbucket')

    def test_task_fix_flow_jira_github(self) -> None:
        self._run_and_assert_task_fix_flow('jira', 'github')

    def test_task_fix_flow_jira_gitlab(self) -> None:
        self._run_and_assert_task_fix_flow('jira', 'gitlab')

    def test_task_fix_flow_github_bitbucket(self) -> None:
        self._run_and_assert_task_fix_flow('github', 'bitbucket')

    def test_task_fix_flow_github_github(self) -> None:
        self._run_and_assert_task_fix_flow('github', 'github')

    def test_task_fix_flow_github_gitlab(self) -> None:
        self._run_and_assert_task_fix_flow('github', 'gitlab')

    def test_task_fix_flow_gitlab_bitbucket(self) -> None:
        self._run_and_assert_task_fix_flow('gitlab', 'bitbucket')

    def test_task_fix_flow_gitlab_github(self) -> None:
        self._run_and_assert_task_fix_flow('gitlab', 'github')

    def test_task_fix_flow_gitlab_gitlab(self) -> None:
        self._run_and_assert_task_fix_flow('gitlab', 'gitlab')

    def test_task_fix_flow_bitbucket_bitbucket(self) -> None:
        self._run_and_assert_task_fix_flow('bitbucket', 'bitbucket')

    def test_task_fix_flow_bitbucket_github(self) -> None:
        self._run_and_assert_task_fix_flow('bitbucket', 'github')

    def test_task_fix_flow_bitbucket_gitlab(self) -> None:
        self._run_and_assert_task_fix_flow('bitbucket', 'gitlab')


# ---------------------------------------------------------------------------
# ### Review Comment Fix Flow
# ---------------------------------------------------------------------------

class ReviewCommentFixFlowTests(unittest.TestCase):
    """
    README ### Review Comment Fix Flow: verify all 14 steps run in the correct
    order for every combination of issue platform and repository provider.
    """

    def _build_review_comment_services(
        self, issue_platform: str, repo_provider: str
    ):
        """
        Wire up a ReviewCommentService stack with mocked external calls.
        Returns (review_service, ticket_client, repository_service,
                 review_comment, call_order, ip_cfg, repository, state_registry).
        """
        call_order: list[str] = []
        cfg = build_test_cfg()
        cfg.kato.issue_platform = issue_platform
        ip = ISSUE_PLATFORMS[issue_platform]

        repository = types.SimpleNamespace(
            id='repo',
            display_name='Test Repo',
            local_path='.',
            destination_branch='main',
            provider_base_url=REPO_PROVIDERS[repo_provider],
            owner='workspace',
            repo_slug='repo',
        )

        review_comment = build_review_comment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )

        # --- closures ---

        def resolve_repos(task):
            call_order.append('resolve_task_repositories')
            return [repository]

        def find_prs(*args, **kwargs):
            call_order.append('find_pull_requests')
            return [
                {
                    PullRequestFields.REPOSITORY_ID: repository.id,
                    PullRequestFields.ID: '17',
                    PullRequestFields.TITLE: 'PROJ-1 Fix bug',
                    PullRequestFields.URL: 'https://example.com/pr/17',
                }
            ]

        def list_comments(*args, **kwargs):
            call_order.append('list_pull_request_comments')
            return [review_comment]

        def prepare_branches(*args, **kwargs):
            call_order.append('prepare_branches')

        def do_fix(*args, **kwargs):
            call_order.append('fix_review_comment')
            return {
                ImplementationFields.SUCCESS: True,
                ImplementationFields.COMMIT_MESSAGE: 'Address review comment',
            }

        def do_publish(*args, **kwargs):
            call_order.append('publish_review_fix')

        def do_reply(*args, **kwargs):
            call_order.append('reply_to_review_comment')

        def do_resolve(*args, **kwargs):
            call_order.append('resolve_review_comment')

        # --- build mocks ---

        ticket_client = types.SimpleNamespace(
            provider_name=issue_platform,
            max_retries=cfg.kato.retry.max_retries,
            get_assigned_tasks=Mock(return_value=[build_task(task_id='PROJ-1')]),
            add_comment=Mock(side_effect=lambda *a: call_order.append('ticket_comment')),
            move_issue_to_state=Mock(),
            validate_connection=Mock(),
        )

        kato_client = types.SimpleNamespace(
            max_retries=cfg.kato.retry.max_retries,
            validate_connection=Mock(),
            validate_model_access=Mock(),
            fix_review_comment=Mock(side_effect=do_fix),
        )

        repository_service = types.SimpleNamespace(
            _validate_inventory=Mock(),
            _validate_git_executable=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            resolve_task_repositories=Mock(side_effect=resolve_repos),
            build_branch_name=Mock(return_value='PROJ-1'),
            find_pull_requests=Mock(side_effect=find_prs),
            list_pull_request_comments=Mock(side_effect=list_comments),
            get_repository=Mock(return_value=repository),
            prepare_task_branches=Mock(side_effect=prepare_branches),
            publish_review_fix=Mock(side_effect=do_publish),
            reply_to_review_comment=Mock(side_effect=do_reply),
            resolve_review_comment=Mock(side_effect=do_resolve),
            restore_task_repositories=Mock(),
            # not used in review comment flow but present for completeness
            prepare_task_repositories=Mock(side_effect=lambda repos: repos),
            destination_branch=Mock(return_value='main'),
            _ensure_branch_is_pushable=Mock(),
            _ensure_branch_has_task_changes=Mock(),
            create_pull_request=Mock(),
        )

        ticket_cfg = getattr(cfg.kato, ip['config_key'])
        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        task_service = TaskService(ticket_cfg, task_data_access)

        state_registry = AgentStateRegistry()
        review_service = ReviewCommentService(
            task_service=task_service,
            implementation_service=ImplementationService(kato_client),
            repository_service=repository_service,
            state_registry=state_registry,
        )
        review_service.logger = Mock()

        return (
            review_service,
            ticket_client,
            repository_service,
            review_comment,
            call_order,
            ip,
            repository,
            state_registry,
            ticket_cfg,
        )

    def _run_and_assert_review_comment_fix_flow(
        self, issue_platform: str, repo_provider: str
    ) -> None:
        """
        Discover and process one review comment and verify every README Review
        Comment Fix Flow step fires in the correct order.

        Step 1:  only look at PRs belonging to review-state tasks
        Step 2:  load or reconstruct saved pull-request context
        Step 3:  fetch pull-request comments from the repository provider
        Step 4:  build the full comment-thread context for OpenHands
        Step 5:  skip already-processed comments (not exercised on happy path)
        Step 6:  log "Working on pull request comments: <name>" before comment id
        Step 7:  prepare the working branch (fetch origin + rebase)
        Step 8:  open the review-fix conversation in OpenHands
        Step 9:  publish the fix back to the same branch
        Step 10: reply to the original review comment
        Step 11: resolve the review comment (when supported)
        Step 12: handle already-resolved gracefully (not exercised on happy path)
        Step 13: mark both the comment id and resolution target as processed
        Step 14: restore branches on failure (not exercised on happy path)
        """
        (
            review_service,
            ticket_client,
            repository_service,
            review_comment,
            call_order,
            ip,
            repository,
            state_registry,
            ticket_cfg,
        ) = self._build_review_comment_services(issue_platform, repo_provider)

        # Step 1: get_new_pull_request_comments only looks at review-state tasks
        new_comments = review_service.get_new_pull_request_comments()

        self.assertEqual(len(new_comments), 1)
        self.assertEqual(new_comments[0].comment_id, '99')

        # Step 1: get_assigned_tasks was called with the review state for this platform
        ticket_client.get_assigned_tasks.assert_called_with(
            project=ticket_cfg.project,
            assignee=ticket_cfg.assignee,
            states=[ip['review_state']],
        )

        # Step 2: resolved repositories to find PR context
        self.assertIn('resolve_task_repositories', call_order)

        # Step 2: discovered PR by calling find_pull_requests on the provider API
        self.assertIn('find_pull_requests', call_order)
        self.assertLess(
            call_order.index('resolve_task_repositories'),
            call_order.index('find_pull_requests'),
            'repositories must be resolved before PRs are looked up',
        )

        # Step 3: pulled comment list from the repository provider
        self.assertIn('list_pull_request_comments', call_order)
        self.assertLess(
            call_order.index('find_pull_requests'),
            call_order.index('list_pull_request_comments'),
            'PRs must be found before comments are listed',
        )

        # --- process the discovered comment ---

        result = review_service.process_review_comment(new_comments[0])
        self.assertEqual(result['status'], 'updated')

        # Step 6: "Working on pull request comments: <name>" logged before comment id
        log_calls = review_service.logger.info.call_args_list
        working_on_idx = next(
            (
                i
                for i, c in enumerate(log_calls)
                if c.args and c.args[0] == 'Working on pull request comments: %s'
            ),
            None,
        )
        comment_id_idx = next(
            (
                i
                for i, c in enumerate(log_calls)
                if c.args and c.args[0] == 'processing review comment %s for pull request %s'
            ),
            None,
        )
        self.assertIsNotNone(working_on_idx, '"Working on pull request comments" log missing')
        self.assertIsNotNone(comment_id_idx, '"processing review comment" log missing')
        self.assertLess(
            working_on_idx,
            comment_id_idx,
            '"Working on pull request comments" must be logged before the comment id',
        )

        # Step 7 before step 8: branch prepared before fix conversation
        self.assertLess(
            call_order.index('prepare_branches'),
            call_order.index('fix_review_comment'),
            'branch must be prepared before the review-fix conversation starts',
        )

        # Step 8 before step 9: fix conversation before publishing
        self.assertLess(
            call_order.index('fix_review_comment'),
            call_order.index('publish_review_fix'),
            'review fix must be obtained before it is published',
        )

        # Step 9 before step 10: fix published before replying to comment
        self.assertLess(
            call_order.index('publish_review_fix'),
            call_order.index('reply_to_review_comment'),
            'fix must be published before replying to the reviewer',
        )

        # Step 10 before step 11: replied before resolving the thread
        self.assertLess(
            call_order.index('reply_to_review_comment'),
            call_order.index('resolve_review_comment'),
            'reply must be sent before the comment thread is resolved',
        )

        # Step 10: reply was posted to the correct PR comment
        repository_service.reply_to_review_comment.assert_called_once()
        reply_args = repository_service.reply_to_review_comment.call_args.args
        self.assertEqual(reply_args[0], repository)
        self.assertEqual(reply_args[1], new_comments[0])

        # Step 11: thread resolved on the correct repository
        repository_service.resolve_review_comment.assert_called_once_with(
            repository, new_comments[0]
        )

        # Step 10 (task comment): task updated with review-fix comment
        ticket_client.add_comment.assert_called_once()

        # Step 13: comment marked as processed in the state registry
        self.assertTrue(
            state_registry.is_review_comment_processed(repository.id, '17', '99'),
            'comment must be marked processed after successful fix',
        )

        # Step 9: published to the correct repository for this provider
        publish_repo_arg = repository_service.publish_review_fix.call_args.args[0]
        self.assertEqual(
            publish_repo_arg.provider_base_url,
            REPO_PROVIDERS[repo_provider],
            f'wrong repository provider for {repo_provider}',
        )

    # --- one test per (issue_platform, repo_provider) combination ---

    def test_review_comment_fix_flow_youtrack_bitbucket(self) -> None:
        self._run_and_assert_review_comment_fix_flow('youtrack', 'bitbucket')

    def test_review_comment_fix_flow_youtrack_github(self) -> None:
        self._run_and_assert_review_comment_fix_flow('youtrack', 'github')

    def test_review_comment_fix_flow_youtrack_gitlab(self) -> None:
        self._run_and_assert_review_comment_fix_flow('youtrack', 'gitlab')

    def test_review_comment_fix_flow_jira_bitbucket(self) -> None:
        self._run_and_assert_review_comment_fix_flow('jira', 'bitbucket')

    def test_review_comment_fix_flow_jira_github(self) -> None:
        self._run_and_assert_review_comment_fix_flow('jira', 'github')

    def test_review_comment_fix_flow_jira_gitlab(self) -> None:
        self._run_and_assert_review_comment_fix_flow('jira', 'gitlab')

    def test_review_comment_fix_flow_github_bitbucket(self) -> None:
        self._run_and_assert_review_comment_fix_flow('github', 'bitbucket')

    def test_review_comment_fix_flow_github_github(self) -> None:
        self._run_and_assert_review_comment_fix_flow('github', 'github')

    def test_review_comment_fix_flow_github_gitlab(self) -> None:
        self._run_and_assert_review_comment_fix_flow('github', 'gitlab')

    def test_review_comment_fix_flow_gitlab_bitbucket(self) -> None:
        self._run_and_assert_review_comment_fix_flow('gitlab', 'bitbucket')

    def test_review_comment_fix_flow_gitlab_github(self) -> None:
        self._run_and_assert_review_comment_fix_flow('gitlab', 'github')

    def test_review_comment_fix_flow_gitlab_gitlab(self) -> None:
        self._run_and_assert_review_comment_fix_flow('gitlab', 'gitlab')

    def test_review_comment_fix_flow_bitbucket_bitbucket(self) -> None:
        self._run_and_assert_review_comment_fix_flow('bitbucket', 'bitbucket')

    def test_review_comment_fix_flow_bitbucket_github(self) -> None:
        self._run_and_assert_review_comment_fix_flow('bitbucket', 'github')

    def test_review_comment_fix_flow_bitbucket_gitlab(self) -> None:
        self._run_and_assert_review_comment_fix_flow('bitbucket', 'gitlab')

    def test_review_comment_fix_reuses_implementation_session_id(self) -> None:
        """
        The session ID from the original implementation conversation is passed to
        fix_review_comment so the review fix runs in the same container, saving costs.
        """
        (
            review_service,
            ticket_client,
            repository_service,
            review_comment,
            call_order,
            ip,
            repository,
            state_registry,
            ticket_cfg,
        ) = self._build_review_comment_services('youtrack', 'github')

        # Discover comments (also seeds the PR context with the task session)
        new_comments = review_service.get_new_pull_request_comments()
        self.assertEqual(len(new_comments), 1)

        # Patch the PR context to carry the implementation session ID
        pr_context = state_registry.pull_request_context('17', repository.id)
        self.assertIsNotNone(pr_context)
        pr_context[ImplementationFields.SESSION_ID] = 'impl-session-xyz'

        # Process the comment
        review_service.process_review_comment(new_comments[0])

        # The session ID from the context must have been forwarded to fix_review_comment
        fix_call_kwargs = repository_service.publish_review_fix.call_args  # confirms flow ran
        self.assertIsNotNone(fix_call_kwargs)

        # Verify via the mock on the underlying kato_client through ImplementationService.
        # session_id is passed as the 3rd positional arg to fix_review_comment.
        impl_mock = review_service._implementation_service._client
        impl_mock.fix_review_comment.assert_called_once()
        call_args, _ = impl_mock.fix_review_comment.call_args
        # call_args: (comment, branch_name, session_id)
        self.assertEqual(
            call_args[2],
            'impl-session-xyz',
            'review-fix conversation must reuse the implementation session ID',
        )


# ---------------------------------------------------------------------------
# ### Shutdown Flow
# ---------------------------------------------------------------------------

class ShutdownFlowTests(unittest.TestCase):
    """
    README shutdown behaviour: on process exit, all active OpenHands conversations
    are deleted so agent-server containers are stopped and removed.
    """

    def test_done_task_conversation_deleted_when_no_longer_in_review(self) -> None:
        """
        When a task is no longer in the review-state list (merged/done), its
        conversation container must be deleted before the next comment poll.
        """
        delete_calls: list[str] = []

        cfg = build_test_cfg()
        ip = ISSUE_PLATFORMS['youtrack']
        ticket_cfg = cfg.kato.youtrack

        ticket_client = types.SimpleNamespace(
            provider_name='youtrack',
            max_retries=3,
            # Returns empty list — task PROJ-1 is no longer in review state
            get_assigned_tasks=Mock(return_value=[]),
            add_comment=Mock(),
            move_issue_to_state=Mock(),
            validate_connection=Mock(),
        )

        kato_client = types.SimpleNamespace(
            max_retries=3,
            validate_connection=Mock(),
            validate_model_access=Mock(),
            fix_review_comment=Mock(),
            delete_conversation=Mock(side_effect=lambda sid: delete_calls.append(sid)),
            stop_all_conversations=Mock(),
        )

        repository_service = types.SimpleNamespace(
            _validate_inventory=Mock(), _validate_git_executable=Mock(),
            _prepare_repository_access=Mock(), _validate_repository_git_access=Mock(),
            resolve_task_repositories=Mock(return_value=[]),
            build_branch_name=Mock(return_value='PROJ-1'),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
            get_repository=Mock(),
            prepare_task_branches=Mock(), publish_review_fix=Mock(),
            reply_to_review_comment=Mock(), resolve_review_comment=Mock(),
            restore_task_repositories=Mock(),
            prepare_task_repositories=Mock(side_effect=lambda r: r),
            destination_branch=Mock(return_value='main'),
            _ensure_branch_is_pushable=Mock(), _ensure_branch_has_task_changes=Mock(),
            create_pull_request=Mock(),
        )

        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        task_service = TaskService(ticket_cfg, task_data_access)
        task_state_service = TaskStateService(ticket_cfg, task_data_access)
        notification_service = NotificationService(
            app_name='kato',
            email_core_lib=Mock(),
            failure_email_cfg=cfg.kato.failure_email,
            completion_email_cfg=cfg.kato.completion_email,
        )

        agent_service = AgentService(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=ImplementationService(kato_client),
            testing_service=TestingService(kato_client),
            repository_service=repository_service,
            notification_service=notification_service,
        )

        # Seed the registry with a tracked session for PROJ-1 as if a task was processed
        agent_service._state_registry.remember_pull_request_context(
            {
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'repo',
            },
            branch_name='PROJ-1',
            session_id='conv-abc',
            task_id='PROJ-1',
        )

        # Polling for comments triggers cleanup: PROJ-1 is not in review → delete conv-abc
        agent_service.get_new_pull_request_comments()

        self.assertIn('conv-abc', delete_calls, 'conversation for done task must be deleted')

    def test_shutdown_stops_all_conversations_on_impl_and_testing_services(self) -> None:
        """AgentService.shutdown() calls stop_all_conversations on both services."""
        impl_stop = Mock()
        testing_stop = Mock()

        impl_service = types.SimpleNamespace(
            max_retries=3,
            validate_connection=Mock(),
            validate_model_access=Mock(),
            implement_task=Mock(return_value={ImplementationFields.SUCCESS: True}),
            fix_review_comment=Mock(),
            stop_all_conversations=impl_stop,
        )
        testing_service = types.SimpleNamespace(
            max_retries=3,
            validate_connection=Mock(),
            validate_model_access=Mock(),
            test_task=Mock(return_value={ImplementationFields.SUCCESS: True}),
            stop_all_conversations=testing_stop,
        )

        cfg = build_test_cfg()
        ip = ISSUE_PLATFORMS['youtrack']
        ticket_cfg = cfg.kato.youtrack

        ticket_client = types.SimpleNamespace(
            provider_name='youtrack',
            max_retries=3,
            get_assigned_tasks=Mock(return_value=[]),
            add_comment=Mock(),
            move_issue_to_state=Mock(),
            validate_connection=Mock(),
        )

        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        task_service = TaskService(ticket_cfg, task_data_access)
        task_state_service = TaskStateService(ticket_cfg, task_data_access)

        repository_service = types.SimpleNamespace(
            _validate_inventory=Mock(),
            _validate_git_executable=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            resolve_task_repositories=Mock(return_value=[]),
            prepare_task_repositories=Mock(side_effect=lambda r: r),
            prepare_task_branches=Mock(),
            destination_branch=Mock(return_value='main'),
            restore_task_repositories=Mock(),
            get_repository=Mock(),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            build_branch_name=Mock(return_value='PROJ-1'),
            create_pull_request=Mock(),
            _ensure_branch_is_pushable=Mock(),
            _ensure_branch_has_task_changes=Mock(),
        )

        notification_service = NotificationService(
            app_name='kato',
            email_core_lib=Mock(),
            failure_email_cfg=cfg.kato.failure_email,
            completion_email_cfg=cfg.kato.completion_email,
        )

        agent_service = AgentService(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=impl_service,
            testing_service=testing_service,
            repository_service=repository_service,
            notification_service=notification_service,
        )

        agent_service.shutdown()

        impl_stop.assert_called_once()
        testing_stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
