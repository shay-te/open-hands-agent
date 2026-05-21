import types
import unittest
from unittest.mock import ANY, Mock, patch


from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.implementation_service import (
    ImplementationService,
)
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.data.fields import (
    EmailFields,
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
    TaskCommentFields,
)
from kato_core_lib.data_layers.service.testing_service import TestingService
from tests.utils import build_review_comment_payload, build_task, build_test_cfg


class AgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_description = 'whats wrong with you please fix it'
        self.pr_description = (
            'Files changed:\n'
            '- client/app.ts\n'
            '  Updated the client flow for the task.\n'
            '- backend/api.py\n'
            '  Added the backend support for the task.'
        )
        self.cfg = build_test_cfg()
        self.client_repo = self.cfg.kato.repositories[0]
        self.backend_repo = self.cfg.kato.repositories[1]
        task_client = types.SimpleNamespace(
            provider_name='youtrack',
            get_assigned_tasks=Mock(return_value=[build_task(description=self.task_description)]),
            add_comment=Mock(),
            move_issue_to_state=Mock(),
        )
        self.task_client = task_client
        self.task_data_access = TaskService(
            self.cfg.kato.youtrack,
            TaskDataAccess(self.cfg.kato.youtrack, task_client),
        )
        self.task_state_service = TaskStateService(
            self.cfg.kato.youtrack,
            TaskDataAccess(self.cfg.kato.youtrack, task_client),
        )
        self.kato_client = types.SimpleNamespace(
            validate_connection=Mock(),
            validate_model_access=Mock(),
            implement_task=Mock(
                return_value={
                    ImplementationFields.SUCCESS: True,
                    ImplementationFields.SESSION_ID: 'conversation-1',
                    ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
                    ImplementationFields.MESSAGE: 'Implementation notes: updated the client and backend flows.',
                    'summary': self.pr_description,
                }
            ),
            test_task=Mock(
                return_value={
                    ImplementationFields.SUCCESS: True,
                    'summary': 'Testing agent validated the implementation',
                }
            ),
            fix_review_comment=Mock(return_value={ImplementationFields.SUCCESS: True}),
        )
        self.implementation_service = ImplementationService(self.kato_client)
        self.testing_service = TestingService(self.kato_client)
        self.repository_service = types.SimpleNamespace(
            repositories=[self.client_repo, self.backend_repo],
            _validate_inventory=Mock(),
            _validate_git_executable=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            resolve_task_repositories=Mock(return_value=[self.client_repo, self.backend_repo]),
            prepare_task_repositories=Mock(side_effect=lambda repositories: repositories),
            prepare_task_branches=Mock(side_effect=lambda repositories, repository_branches: repositories),
            destination_branch=Mock(return_value='master'),
            _ensure_branch_is_pushable=Mock(),
            _ensure_branch_has_task_changes=Mock(),
            restore_task_repositories=Mock(),
            get_repository=Mock(side_effect=lambda repository_id: {
                'client': self.client_repo,
                'backend': self.backend_repo,
            }[repository_id]),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            build_branch_name=Mock(
                side_effect=[
                    'feature/proj-1/client',
                    'feature/proj-1/backend',
                ]
            ),
            create_pull_request=Mock(
                side_effect=[
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        PullRequestFields.ID: '17',
                        PullRequestFields.TITLE: 'PROJ-1: fix it already',
                        PullRequestFields.URL: 'https://bitbucket/pr/17',
                        PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                        PullRequestFields.DESTINATION_BRANCH: 'master',
                    },
                    {
                        PullRequestFields.REPOSITORY_ID: 'backend',
                        PullRequestFields.ID: '18',
                        PullRequestFields.TITLE: 'PROJ-1: fix it already',
                        PullRequestFields.URL: 'https://github/pr/18',
                        PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                        PullRequestFields.DESTINATION_BRANCH: 'main',
                    },
                ]
            ),
        )
        self.email_core_lib = Mock()
        self.notification_service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.kato.failure_email,
            completion_email_cfg=self.cfg.kato.completion_email,
        )
        self.service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )

    def test_init_rejects_missing_testing_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'testing_service is required'):
            AgentService(
                self.task_data_access,
                self.task_state_service,
                self.implementation_service,
                None,
                self.repository_service,
                self.notification_service,
            )

    def test_init_rejects_missing_notification_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'notification_service is required'):
            AgentService(
                self.task_data_access,
                self.task_state_service,
                self.implementation_service,
                self.testing_service,
                self.repository_service,
                None,
            )

    # NOTE: 3 obsolete ``test_validate_connections_*`` tests were removed
    # here when ``validate_connections`` became lazy. Their assertions
    # are covered by ``test_startup_validator``:
    #
    #   * dependency-call shape →
    #     ``test_validate_checks_repository_and_all_dependencies``
    #   * aggregated failure message →
    #     ``test_validate_aggregates_dependency_failures``
    #     (also covers retryable-failure attempt counts)
    #
    # See tests/test_startup_validator.py for the current contract.

    def test_process_assigned_task_stops_when_model_access_validation_fails(self) -> None:
        self.service.logger = Mock()
        self.kato_client.validate_model_access = Mock(
            side_effect=RuntimeError('openrouter is unavailable')
        )
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.repository_service.resolve_task_repositories.assert_not_called()
        self.repository_service.prepare_task_repositories.assert_not_called()
        self.repository_service.prepare_task_branches.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.task_client.add_comment.assert_called()
        self.kato_client.implement_task.assert_not_called()
        self.kato_client.test_task.assert_not_called()

    # NOTE: 2 obsolete ``test_validate_connections_*`` tests were removed
    # here when inventory + repo-validation errors became lazy. Their
    # assertions are covered:
    #
    #   * inventory ValueError ("at least one repository must be
    #     configured") at lazy time →
    #     ``test_repository_service.test_validate_inventory_refuses_when_no_repositories_configured``
    #   * repository validation failure halts before downstream
    #     dependency validation →
    #     ``test_startup_validator.test_validate_raises_when_repository_validation_fails``

    def test_process_assigned_task_creates_prs_for_all_selected_repositories(self) -> None:
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]
        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(
            results,
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
                PullRequestFields.PULL_REQUESTS: [
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        PullRequestFields.ID: '17',
                        PullRequestFields.TITLE: 'PROJ-1: fix it already',
                        PullRequestFields.URL: 'https://bitbucket/pr/17',
                        PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                        PullRequestFields.DESTINATION_BRANCH: 'master',
                    },
                    {
                        PullRequestFields.REPOSITORY_ID: 'backend',
                        PullRequestFields.ID: '18',
                        PullRequestFields.TITLE: 'PROJ-1: fix it already',
                        PullRequestFields.URL: 'https://github/pr/18',
                        PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                        PullRequestFields.DESTINATION_BRANCH: 'main',
                    },
                ],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.kato_client.test_task.assert_called_once_with(
            task,
            prepared_task=ANY,
        )
        self.assertEqual(
            self.repository_service.prepare_task_branches.call_args_list,
            [
                unittest.mock.call(
                    [self.client_repo, self.backend_repo],
                    {
                        'client': 'feature/proj-1/client',
                        'backend': 'feature/proj-1/backend',
                    },
                ),
            ],
        )
        client_call = self.repository_service.create_pull_request.call_args_list[0]
        backend_call = self.repository_service.create_pull_request.call_args_list[1]
        client_description = client_call.kwargs['description']
        backend_description = backend_call.kwargs['description']
        for description in (client_description, backend_description):
            self.assertIn('Kato completed task PROJ-1: fix it already.', description)
            self.assertIn('Requested change:', description)
            self.assertIn('whats wrong with you please fix it', description)
            self.assertIn('Implementation summary:', description)
            self.assertIn('Files changed:', description)
            self.assertIn('Execution notes:', description)
            self.assertIn('Implementation notes: updated the client and backend flows.', description)
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        start_comment = self.task_client.add_comment.call_args_list[0].args[1]
        self.assertIn('started working on this task', start_comment)
        self.assertIn('client', start_comment)
        self.assertIn('backend', start_comment)
        summary_comment = self.task_client.add_comment.call_args_list[1].args[1]
        self.assertIn('Published review links:', summary_comment)
        self.assertIn('client: https://bitbucket/pr/17', summary_comment)
        self.assertIn('backend: https://github/pr/18', summary_comment)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'To Verify'),
            ],
        )
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        completion_email = self.email_core_lib.send.call_args_list[0].args[1]
        self.assertEqual(completion_email[EmailFields.TASK_ID], 'PROJ-1')
        self.assertIn('client: PROJ-1: fix it already', completion_email[EmailFields.PULL_REQUEST_SUMMARY])
        self.assertIn('backend: PROJ-1: fix it already', completion_email[EmailFields.PULL_REQUEST_SUMMARY])
        self.assertEqual(
            self.service._state_registry.pull_request_context_map,
            {
                '17': [
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        PullRequestFields.TITLE: 'PROJ-1: fix it already',
                        'branch_name': 'feature/proj-1/client',
                        ImplementationFields.SESSION_ID: 'conversation-1',
                        TaskFields.ID: 'PROJ-1',
                        TaskFields.SUMMARY: 'fix it already',
                    }
                ],
                '18': [
                    {
                        PullRequestFields.REPOSITORY_ID: 'backend',
                        PullRequestFields.TITLE: 'PROJ-1: fix it already',
                        'branch_name': 'feature/proj-1/backend',
                        ImplementationFields.SESSION_ID: 'conversation-1',
                        TaskFields.ID: 'PROJ-1',
                        TaskFields.SUMMARY: 'fix it already',
                    }
                ],
            },
        )
    def test_process_assigned_task_uses_orchestration_commit_message_for_publish(self) -> None:
        task = self.task_data_access.get_assigned_tasks()[0]
        self.kato_client.test_task.return_value = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.COMMIT_MESSAGE: 'Finalize PROJ-1 after testing',
            ImplementationFields.MESSAGE: 'Validation report: no dedicated tests were defined.',
            'summary': 'Testing agent validated the implementation',
        }

        self.service.process_assigned_task(task)

        client_call = self.repository_service.create_pull_request.call_args_list[0]
        client_description = client_call.kwargs['description']
        self.assertIn('Kato completed task PROJ-1: fix it already.', client_description)
        self.assertIn('Requested change:', client_description)
        self.assertIn('whats wrong with you please fix it', client_description)
        self.assertIn('Implementation summary:', client_description)
        self.assertIn('Files changed:', client_description)
        self.assertIn('Execution notes:', client_description)
        self.assertIn('Validation report: no dedicated tests were defined.', client_description)
        self.assertIn(
            'Validation report: no dedicated tests were defined.',
            self.task_client.add_comment.call_args_list[1].args[1],
        )

    def test_process_assigned_task_can_skip_testing_validation(self) -> None:
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            skip_testing=True,
        )
        self.kato_client.implement_task.return_value = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.SESSION_ID: 'conversation-1',
            ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
            ImplementationFields.MESSAGE: 'Implementation note from OpenHands',
            'summary': self.pr_description,
        }
        task = self.task_data_access.get_assigned_tasks()[0]

        results = service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.kato_client.test_task.assert_not_called()
        self.assertEqual(self.repository_service.prepare_task_branches.call_count, 1)
        summary_comment = self.task_client.add_comment.call_args_list[1].args[1]
        self.assertNotIn('Validation report:', summary_comment)
        self.assertNotIn('Implementation note from OpenHands', summary_comment)

    def test_process_assigned_task_reopens_when_task_branch_validation_fails_before_testing(self) -> None:
        task = self.task_data_access.get_assigned_tasks()[0]
        self.repository_service._ensure_branch_has_task_changes.side_effect = RuntimeError(
            'branch feature/proj-1/client has no task changes ahead of master'
        )

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        self.assertIn(
            'started working on this task',
            self.task_client.add_comment.call_args_list[0].args[1],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertIn(
            'branch feature/proj-1/client has no task changes ahead of master',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.kato_client.test_task.assert_not_called()
        self.repository_service.create_pull_request.assert_not_called()

    def test_get_assigned_tasks_returns_empty_list_when_no_tasks_exist(self) -> None:
        self.task_client.get_assigned_tasks.return_value = []

        results = self.service.get_assigned_tasks()

        self.assertEqual(results, [])

    def test_process_assigned_task_skips_when_prior_failure_comment_is_still_active(self) -> None:
        task = build_task(
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato agent stopped working on this task: gateway timeout'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'Please keep the fix minimal.',
                },
            ],
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(
            results,
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.SKIPPED,
                PullRequestFields.PULL_REQUESTS: [],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )
        self.repository_service.resolve_task_repositories.assert_not_called()
        self.kato_client.implement_task.assert_not_called()
        self.task_client.add_comment.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_task_skips_when_prior_completion_comment_is_still_active(self) -> None:
        task = build_task(
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato completed task PROJ-1: Fix the auth flow.'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'Looks good.',
                },
            ],
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(
            results,
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.SKIPPED,
                PullRequestFields.PULL_REQUESTS: [],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )
        self.repository_service.resolve_task_repositories.assert_not_called()
        self.kato_client.implement_task.assert_not_called()
        self.task_client.add_comment.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_task_retries_when_prior_pre_start_failure_comment_is_stale(self) -> None:
        task = build_task(
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato agent could not safely process this task: '
                        'destination branch master at /workspace/project has 3 local '
                        'commit(s) not on origin/master; refusing to start a new task'
                    ),
                }
            ],
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo]
        )
        self.assertEqual(self.repository_service.prepare_task_branches.call_count, 1)
        self.kato_client.implement_task.assert_called_once_with(
            task,
            '',
            prepared_task=ANY,
        )

    def test_process_assigned_task_skips_when_prior_pre_start_failure_still_blocks_preflight(self) -> None:
        task = build_task(
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato agent could not safely process this task: '
                        'destination branch master at /workspace/project has 3 local '
                        'commit(s) not on origin/master; refusing to start a new task'
                    ),
                }
            ],
        )
        self.repository_service.prepare_task_repositories.side_effect = RuntimeError(
            'destination branch master at /workspace/project has 3 local commit(s) '
            'not on origin/master; refusing to start a new task'
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(
            results,
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.SKIPPED,
                PullRequestFields.PULL_REQUESTS: [],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo]
        )
        self.kato_client.implement_task.assert_not_called()
        self.task_client.add_comment.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_task_retries_when_prior_repository_detection_skip_is_stale(self) -> None:
        task = build_task(
            summary='client backend task needs update',
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato agent skipped this task because it could not detect '
                        'which repository to use from the task content: no configured '
                        'repository matched task PROJ-1. Please mention the repository '
                        'name or alias in the task summary or description.'
                    ),
                }
            ],
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.kato_client.implement_task.assert_called_once_with(
            task,
            '',
            prepared_task=ANY,
        )

    def test_process_assigned_task_retries_after_later_retry_instruction(self) -> None:
        task = build_task(
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato agent could not safely process this task: timeout'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'kato: retry approved for this task.',
                },
            ],
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo]
        )
        self.kato_client.implement_task.assert_called_once_with(
            task,
            '',
            prepared_task=ANY,
        )

    def test_process_assigned_task_retries_after_later_retry_instruction_following_completion_comment(self) -> None:
        task = build_task(
            description='whats wrong with you please fix it',
            comments=[
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'Kato completed task PROJ-1: Fix the auth flow.'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'kato: retry approved for this task.',
                },
            ],
        )

        results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo]
        )
        self.kato_client.implement_task.assert_called_once_with(
            task,
            '',
            prepared_task=ANY,
        )

    def test_process_assigned_task_skips_execution_without_success_flag(self) -> None:
        self.kato_client.implement_task.return_value = {}
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.kato_client.test_task.assert_not_called()
        self.repository_service.create_pull_request.assert_not_called()
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        self.assertIn(
            'started working on this task',
            self.task_client.add_comment.call_args_list[0].args[1],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertIn(
            'implementation agent reported the task is not ready',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo],
            force=True,
        )
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_restores_repositories_after_branch_preparation_failure(self) -> None:
        self.repository_service.prepare_task_branches.side_effect = RuntimeError(
            'failed to prepare task branches'
        )
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo]
        )
        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo],
            force=True,
        )
        self.task_client.add_comment.assert_called_once()
        self.assertIn(
            'Kato agent could not safely process this task: failed to prepare task branches',
            self.task_client.add_comment.call_args.args[1],
        )

    def test_process_assigned_task_restores_repositories_when_git_push_validation_fails_before_implementation(self) -> None:
        self.repository_service._ensure_branch_is_pushable.side_effect = RuntimeError(
            'failed to push branch PROJ-1'
        )
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.repository_service.resolve_task_repositories.assert_called_once_with(task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo]
        )
        self.repository_service.prepare_task_branches.assert_called_once()
        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.client_repo, self.backend_repo],
            force=True,
        )
        self.task_client.add_comment.assert_called_once()
        self.assertIn(
            'Kato agent stopped working on this task: failed to push branch PROJ-1',
            self.task_client.add_comment.call_args.args[1],
        )
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [unittest.mock.call('PROJ-1', 'State', 'Todo')],
        )
        self.kato_client.implement_task.assert_not_called()
        self.kato_client.test_task.assert_not_called()

    def test_validate_task_branch_push_access_returns_false_without_failure_handler(self) -> None:
        self.service._task_preflight_service._task_branch_push_validator.validate = Mock(
            side_effect=RuntimeError('failed to push branch PROJ-1')
        )
        prepared_task = types.SimpleNamespace(
            repositories=[self.client_repo],
            repository_branches={'client': 'feature/proj-1/client'},
        )

        result = self.service._task_preflight_service.validate_task_branch_push_access(
            build_task(),
            prepared_task,
        )

        self.assertFalse(result)
        self.service._task_preflight_service._task_branch_push_validator.validate.assert_called_once_with(
            [self.client_repo],
            {'client': 'feature/proj-1/client'},
        )
        self.repository_service.restore_task_repositories.assert_not_called()

    def test_process_assigned_task_handles_ambiguous_or_missing_repository_scope(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = ValueError('no configured repository matched task PROJ-1')
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.task_client.add_comment.assert_called_once()
        comment_body = self.task_client.add_comment.call_args.args[1]
        self.assertIn('could not detect which repository to use', comment_body)
        # The fix instruction: tag with ``kato:repo:<id>`` and use
        # the picker to find the legal ids.
        self.assertIn('kato:repo:<repository-id>', comment_body)
        self.assertIn('./kato approve-repo', comment_body)
        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_task_reports_generic_pre_start_failures_without_reopening(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = RuntimeError('repository service down')
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.task_client.add_comment.assert_called_once_with(
            'PROJ-1',
            'Kato agent could not safely process this task: repository service down',
        )
        self.task_client.move_issue_to_state.assert_not_called()
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_reports_repository_preparation_failures_without_reopening(self) -> None:
        self.repository_service.prepare_task_repositories.side_effect = RuntimeError(
            'unable to determine destination branch for repository client'
        )
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.task_client.add_comment.assert_called_once_with(
            'PROJ-1',
            'Kato agent could not safely process this task: '
            'unable to determine destination branch for repository client',
        )
        self.task_client.move_issue_to_state.assert_not_called()
        self.kato_client.implement_task.assert_not_called()

    def test_process_assigned_task_with_planning_tag_registers_chat_and_skips_execution(self) -> None:
        from unittest.mock import MagicMock as _MagicMock
        from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
        session_manager = _MagicMock()
        # No prior session — wait-planning short-circuits when one is alive
        # (so the scan loop doesn't respawn or spam logs every cycle).
        session_manager.get_session.return_value = None
        # Wait-planning is now its own service injected into AgentService,
        # not inline methods. Construct with the same dependencies the
        # real wiring uses.
        wait_planning_service = WaitPlanningService(
            session_manager=session_manager,
            repository_service=self.repository_service,
            task_state_service=self.task_state_service,
        )
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            session_manager=session_manager,
            wait_planning_service=wait_planning_service,
        )
        task = build_task(tags=['kato:wait-planning'])

        results = service.process_assigned_task(task)

        # Tab is registered so the user can chat with the agent. The
        # session is spawned with a contextual prompt so claude doesn't
        # exit on empty stdin and the scan loop won't respawn it.
        session_manager.start_session.assert_called_once()
        kwargs = session_manager.start_session.call_args.kwargs
        self.assertEqual(kwargs['task_id'], 'PROJ-1')
        # Initial prompt must be non-empty (claude -p exits on empty stdin)
        # and must not request any work — just announce readiness.
        self.assertNotEqual(kwargs['initial_prompt'], '')
        prompt = kwargs['initial_prompt']
        self.assertIn('PROJ-1', prompt)
        # Must hard-stop tool use; otherwise Claude would start working
        # on the task instead of planning it.
        self.assertIn('planning-only', prompt)
        self.assertIn('DO NOT call any tools', prompt)
        # Skip result returned: orchestrator does not run / publish anything.
        self.assertIsNotNone(results)
        self.assertEqual(results.get(StatusFields.STATUS), StatusFields.SKIPPED)
        self.kato_client.implement_task.assert_not_called()

    def test_process_assigned_task_with_planning_tag_skips_silently_when_session_alive(self) -> None:
        # When the user is mid-conversation, every scan cycle calling
        # the wait-planning handler should be a no-op — no respawn,
        # no log line. Otherwise the kato terminal fills with duplicate
        # "registered planning chat" lines and we risk re-injecting the
        # initial prompt into a live conversation.
        from unittest.mock import MagicMock as _MagicMock
        from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
        session_manager = _MagicMock()
        live_session = _MagicMock()
        live_session.is_alive = True
        session_manager.get_session.return_value = live_session
        wait_planning_service = WaitPlanningService(
            session_manager=session_manager,
            repository_service=self.repository_service,
            task_state_service=self.task_state_service,
        )
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            session_manager=session_manager,
            wait_planning_service=wait_planning_service,
        )
        task = build_task(tags=['kato:wait-planning'])

        results = service.process_assigned_task(task)

        session_manager.start_session.assert_not_called()
        self.assertEqual(results.get(StatusFields.STATUS), StatusFields.SKIPPED)

    def test_wait_planning_marks_workspace_as_operator_driven_for_startup_resume(self) -> None:
        from unittest.mock import MagicMock as _MagicMock
        from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
        session_manager = _MagicMock()
        session_manager.get_session.return_value = None
        workspace_manager = _MagicMock()
        wait_planning_service = WaitPlanningService(
            session_manager=session_manager,
            repository_service=self.repository_service,
            task_state_service=self.task_state_service,
            workspace_manager=workspace_manager,
        )
        wait_planning_service._resolve_planning_context = Mock(
            return_value=types.SimpleNamespace(cwd='.', expected_branch='PROJ-1')
        )
        task = build_task(tags=['kato:wait-planning'])

        results = wait_planning_service.handle_task(task)

        self.assertEqual(results.get(StatusFields.STATUS), StatusFields.SKIPPED)
        session_manager.start_session.assert_called_once()
        workspace_manager.update_resume_on_startup.assert_called_once_with(
            'PROJ-1',
            False,
        )

    def test_process_assigned_task_without_planning_tag_runs_normally(self) -> None:
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )
        task = build_task(
            description=self.task_description,
            tags=['some-other-label'],
        )

        service.process_assigned_task(task)

        # No tag → run autonomously through the one-shot client (no
        # streaming runner wired in this test setup).
        self.kato_client.implement_task.assert_called_once()

    def test_process_assigned_task_skips_when_task_definition_is_too_thin(self) -> None:
        task = build_task(
            summary='test',
            description='No description provided.',
        )

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.task_client.add_comment.assert_called_once()
        comment_args = self.task_client.add_comment.call_args.args
        self.assertEqual(comment_args[0], 'PROJ-1')
        comment_body = comment_args[1]
        # Pin the load-bearing parts of the actionable comment, not
        # the whole string — the prose can iterate without breaking
        # the test.
        self.assertIn('task definition is too thin', comment_body)
        self.assertIn('what', comment_body)
        self.assertIn('why', comment_body)
        self.assertIn('kato:run', comment_body)
        self.task_client.move_issue_to_state.assert_not_called()
        self.kato_client.implement_task.assert_not_called()
        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_task_reports_testing_failures_before_pr_creation(self) -> None:
        self.kato_client.test_task.return_value = {
            ImplementationFields.SUCCESS: False,
            'summary': 'backend tests are still failing',
        }
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.TESTING_FAILED)
        self.repository_service.create_pull_request.assert_not_called()
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        self.assertIn(
            'started working on this task',
            self.task_client.add_comment.call_args_list[0].args[1],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertIn(
            'backend tests are still failing',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_handles_implementation_request_errors(self) -> None:
        self.kato_client.implement_task.side_effect = RuntimeError('openhands down')
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertIn(
            'started working on this task',
            self.task_client.add_comment.call_args_list[0].args[1],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertIn('openhands down', self.task_client.add_comment.call_args_list[1].args[1])
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_handles_testing_request_errors(self) -> None:
        self.kato_client.test_task.side_effect = RuntimeError('testing sandbox down')
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertIn(
            'started working on this task',
            self.task_client.add_comment.call_args_list[0].args[1],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertIn(
            'testing sandbox down',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_reports_partial_pr_failures_without_moving_review(self) -> None:
        self.kato_client.test_task.return_value = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.MESSAGE: 'Validation report: no dedicated tests were defined.',
            'summary': 'Testing agent validated the implementation',
        }
        # Backend's PR creation fails on every retry attempt (the
        # publisher uses 3 attempts by default); side_effect must
        # supply 3 errors so we land in the failure-handler branch
        # rather than burning through StopIteration.
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            RuntimeError('github down'),
            RuntimeError('github down'),
            RuntimeError('github down'),
        ]
        task = self.task_data_access.get_assigned_tasks()[0]
        # Skip retry-backoff sleeps so the test stays fast.
        self.service._task_publisher._sleep_fn = lambda _: None

        results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        # Result payload now carries the per-repo error so callers
        # (and downstream emails / logs) can render an actionable line.
        self.assertEqual(
            results[PullRequestFields.FAILED_REPOSITORIES],
            [{
                PullRequestFields.REPOSITORY_ID: 'backend',
                'error': 'github down',
            }],
        )
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[-1].args[1],
        )
        # Comment text includes the failure reason so the operator can
        # diagnose without spelunking through kato logs.
        self.assertIn(
            'failed to create pull requests for repositories: backend (github down)',
            self.task_client.add_comment.call_args_list[-1].args[1],
        )
        self.assertNotIn(
            'Validation report:',
            self.task_client.add_comment.call_args_list[-1].args[1],
        )
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_stops_when_move_to_in_progress_fails(self) -> None:
        self.task_client.move_issue_to_state.side_effect = RuntimeError('state update failed')
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.task_client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'State',
            'In Progress',
        )
        self.kato_client.implement_task.assert_not_called()
        self.task_client.add_comment.assert_called_once_with(
            'PROJ-1',
            'Kato agent could not safely process this task: state update failed',
        )

    def test_process_assigned_task_moves_to_review_when_completion_comment_fails(self) -> None:
        self.task_client.add_comment.side_effect = [
            None,
            RuntimeError('comment write failed'),
        ]
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'To Verify'),
            ],
        )
        self.assertEqual(self.task_client.add_comment.call_count, 2)
        self.assertIn(
            'started working on this task',
            self.task_client.add_comment.call_args_list[0].args[1],
        )
        self.assertEqual(
            self.service._state_registry.processed_task_map,
            {
                'PROJ-1': {
                    StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
                    PullRequestFields.PULL_REQUESTS: [
                        {
                            PullRequestFields.REPOSITORY_ID: 'client',
                            PullRequestFields.ID: '17',
                            PullRequestFields.TITLE: 'PROJ-1: fix it already',
                            PullRequestFields.URL: 'https://bitbucket/pr/17',
                            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                            PullRequestFields.DESTINATION_BRANCH: 'master',
                        },
                        {
                            PullRequestFields.REPOSITORY_ID: 'backend',
                            PullRequestFields.ID: '18',
                            PullRequestFields.TITLE: 'PROJ-1: fix it already',
                            PullRequestFields.URL: 'https://github/pr/18',
                            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                            PullRequestFields.DESTINATION_BRANCH: 'main',
                        },
                    ],
                }
            },
        )
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_reopens_when_move_to_review_fails(self) -> None:
        # Publisher now retries move-to-review with the default budget
        # (2 retries → 3 attempts) before giving up. Permanent failure
        # = all 3 attempts fail; only then does kato reopen the task.
        self.task_client.move_issue_to_state.side_effect = [
            None,                                # initial → In Progress
            RuntimeError('state update failed'),  # to-review attempt 1
            RuntimeError('state update failed'),  # to-review attempt 2
            RuntimeError('state update failed'),  # to-review attempt 3
            None,                                # reopen → Todo
        ]
        self.service.logger = Mock()
        # Skip the retry-backoff sleeps so the test stays fast.
        self.service._task_publisher._sleep_fn = lambda _: None
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'To Verify'),
                unittest.mock.call('PROJ-1', 'State', 'To Verify'),
                unittest.mock.call('PROJ-1', 'State', 'To Verify'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertEqual(self.task_client.add_comment.call_count, 3)
        self.assertIn(
            'Kato completed task PROJ-1: fix it already.',
            self.task_client.add_comment.call_args_list[1].args[1],
        )
        self.assertIn(
            'stopped working on this task',
            self.task_client.add_comment.call_args_list[2].args[1],
        )
        self.assertIn(
            'state update failed',
            self.task_client.add_comment.call_args_list[2].args[1],
        )
        self.assertEqual(self.service._state_registry.processed_task_map, {})
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_continues_when_move_to_open_fails(self) -> None:
        self.kato_client.implement_task.side_effect = RuntimeError('openhands down')
        self.task_client.move_issue_to_state.side_effect = [
            None,
            RuntimeError('reopen failed'),
        ]
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )
        self.assertEqual(self.task_client.add_comment.call_count, 2)

    def test_process_assigned_task_ignores_completion_notification_failures(self) -> None:
        self.notification_service.notify_task_ready_for_review = Mock(side_effect=RuntimeError('smtp failed'))
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.notification_service.notify_task_ready_for_review.assert_called_once()
        self.email_core_lib.send.assert_not_called()
        self.assertEqual(
            self.task_client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'To Verify'),
            ],
        )

    def test_handle_pull_request_comment_updates_known_branch_and_repository(self) -> None:
        self.service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
                ImplementationFields.SESSION_ID: 'conversation-1',
                TaskFields.ID: 'PROJ-1',
                TaskFields.SUMMARY: 'fix it already',
            }
        ]

        result = self.service.handle_pull_request_comment(build_review_comment_payload())

        self.assertEqual(
            result,
            {
                StatusFields.STATUS: StatusFields.UPDATED,
                'pull_request_id': '17',
                'branch_name': 'feature/proj-1/client',
                PullRequestFields.REPOSITORY_ID: 'client',
            },
        )
        comment_arg = self.kato_client.fix_review_comment.call_args.args[0]
        self.assertEqual(getattr(comment_arg, PullRequestFields.REPOSITORY_ID), 'client')
        self.assertEqual(
            self.kato_client.fix_review_comment.call_args.args[2],
            'conversation-1',
        )
        self.assertEqual(
            self.kato_client.fix_review_comment.call_args.kwargs,
            {
                'task_id': 'PROJ-1',
                'task_summary': 'fix it already',
            },
        )
        self.repository_service.prepare_task_branches.assert_called_once_with(
            [self.client_repo],
            {'client': 'feature/proj-1/client'},
        )

    def test_process_review_comment_marks_comment_processed(self) -> None:
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )
        service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            }
        ]

        service.process_review_comment(
            ReviewComment(
                pull_request_id='17',
                comment_id='99',
                author='reviewer',
                body='Please rename this variable.',
            )
        )

        self.repository_service.prepare_task_branches.assert_called_once_with(
            [self.client_repo],
            {'client': 'feature/proj-1/client'},
        )
        self.repository_service.publish_review_fix.assert_called_once_with(
            self.client_repo,
            'feature/proj-1/client',
            'Address review comments',
        )
        self.repository_service.resolve_review_comment.assert_called_once()
        self.assertTrue(service._state_registry.is_review_comment_processed('client', '17', '99'))

    def test_process_review_comment_does_not_mark_processed_when_publish_fails(self) -> None:
        self.repository_service.publish_review_fix.side_effect = RuntimeError('push failed')
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )
        service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            }
        ]

        with self.assertRaisesRegex(RuntimeError, 'push failed'):
            service.process_review_comment(
                ReviewComment(
                    pull_request_id='17',
                    comment_id='99',
                    author='reviewer',
                    body='Please rename this variable.',
                )
        )

        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.client_repo],
            force=True,
        )
        self.repository_service.resolve_review_comment.assert_not_called()
        self.assertFalse(service._state_registry.is_review_comment_processed('client', '17', '99'))

    def test_process_review_comment_does_not_mark_processed_when_resolution_fails(self) -> None:
        self.repository_service.resolve_review_comment.side_effect = RuntimeError('provider down')
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )
        service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            }
        ]

        with self.assertRaisesRegex(RuntimeError, 'provider down'):
            service.process_review_comment(
                ReviewComment(
                    pull_request_id='17',
                    comment_id='99',
                    author='reviewer',
                    body='Please rename this variable.',
                )
        )

        self.repository_service.restore_task_repositories.assert_called_once_with(
            [self.client_repo],
            force=True,
        )
        self.repository_service.publish_review_fix.assert_called_once()
        self.assertFalse(service._state_registry.is_review_comment_processed('client', '17', '99'))

    def test_handle_pull_request_comment_rejects_unknown_pull_request(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unknown pull request id'):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_handle_pull_request_comment_rejects_ambiguous_pull_request(self) -> None:
        self.service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        ]

        with self.assertRaisesRegex(ValueError, 'ambiguous pull request id across repositories'):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_handle_pull_request_comment_uses_repository_id_to_resolve_ambiguity(self) -> None:
        self.service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        ]
        payload = build_review_comment_payload()
        payload[PullRequestFields.REPOSITORY_ID] = 'backend'

        result = self.service.handle_pull_request_comment(payload)

        self.assertEqual(result[PullRequestFields.REPOSITORY_ID], 'backend')

    def test_handle_pull_request_comment_raises_when_fix_fails(self) -> None:
        self.kato_client.fix_review_comment.return_value = {ImplementationFields.SUCCESS: False}
        self.service._state_registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            }
        ]

        with self.assertRaisesRegex(
            RuntimeError, 'failed to address review comment batch \\(99\\)',
        ):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_get_new_pull_request_comments_returns_unprocessed_comments_with_context(self) -> None:
        self.repository_service.resolve_task_repositories.return_value = [self.client_repo]
        self.repository_service.build_branch_name = Mock(return_value='feature/proj-1/client')
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='17',
                comment_id='98',
                author='reviewer',
                body='Please add a test.',
            ),
            ReviewComment(
                pull_request_id='17',
                comment_id='99',
                author='reviewer',
                body='Please rename this variable.',
            ),
        ]
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )
        service._state_registry.mark_review_comment_processed('client', '17', '98')
        self.task_client.get_assigned_tasks.return_value = [build_task(task_id='PROJ-1')]

        comments = service.get_new_pull_request_comments()

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].comment_id, '99')
        self.assertEqual(getattr(comments[0], PullRequestFields.REPOSITORY_ID), 'client')
        self.assertEqual(
            getattr(comments[0], ReviewCommentFields.ALL_COMMENTS),
            [
                {
                    ReviewCommentFields.COMMENT_ID: '98',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please add a test.',
                },
                {
                    ReviewCommentFields.COMMENT_ID: '99',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please rename this variable.',
                },
            ],
        )
        # Called twice per cycle: once for done-task cleanup, once for comment discovery.
        self.assertEqual(self.task_client.get_assigned_tasks.call_count, 2)
        self.task_client.get_assigned_tasks.assert_called_with(
            project='PROJ',
            assignee='me',
            states=['To Verify'],
        )

    def test_get_new_pull_request_comments_deduplicates_same_resolution_target(self) -> None:
        self.repository_service.resolve_task_repositories.return_value = [self.client_repo]
        self.repository_service.build_branch_name = Mock(return_value='feature/proj-1/client')
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        first = ReviewComment(
            pull_request_id='17',
            comment_id='98',
            author='reviewer',
            body='Please add a test.',
        )
        setattr(first, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(first, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        second = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please rename this variable.',
        )
        setattr(second, ReviewCommentFields.RESOLUTION_TARGET_ID, 'thread-1')
        setattr(second, ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'thread')
        self.repository_service.list_pull_request_comments.return_value = [first, second]
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )
        self.task_client.get_assigned_tasks.return_value = [build_task(task_id='PROJ-1')]

        comments = service.get_new_pull_request_comments()

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].comment_id, '99')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.client_repo,
            '17',
        )

    def test_get_new_pull_request_comments_only_polls_pull_requests_for_review_tasks(self) -> None:
        self.task_client.get_assigned_tasks.return_value = [build_task(task_id='PROJ-1')]
        self.repository_service.list_pull_request_comments.return_value = []
        self.repository_service.resolve_task_repositories.return_value = [self.client_repo]
        self.repository_service.build_branch_name = Mock(return_value='feature/proj-1/client')
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        service = AgentService(
            self.task_data_access,
            self.task_state_service,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )

        comments = service.get_new_pull_request_comments()

        self.assertEqual(comments, [])
        self.repository_service.get_repository.assert_called_once_with('client')
        self.repository_service.list_pull_request_comments.assert_called_once_with(
            self.client_repo,
            '17',
        )

    def test_get_new_pull_request_comments_uses_in_memory_processed_tasks_without_state_storage(self) -> None:
        self.repository_service.resolve_task_repositories.return_value = [self.client_repo]
        self.repository_service.build_branch_name = Mock(return_value='feature/proj-1/client')
        self.repository_service.find_pull_requests.return_value = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            }
        ]
        self.task_client.get_assigned_tasks.return_value = [build_task(task_id='PROJ-1')]
        self.repository_service.list_pull_request_comments.return_value = [
            ReviewComment(
                pull_request_id='17',
                comment_id='98',
                author='reviewer',
                body='Please add a test.',
            )
        ]

        comments = self.service.get_new_pull_request_comments()

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].comment_id, '98')
