import types
import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.fields import (
    EmailFields,
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from utils import build_review_comment_payload, build_task, build_test_cfg


class AgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self.client_repo = self.cfg.openhands_agent.repositories[0]
        self.backend_repo = self.cfg.openhands_agent.repositories[1]
        task_client = types.SimpleNamespace(
            get_assigned_tasks=Mock(return_value=[build_task(description='Update client and backend APIs')]),
            add_comment=Mock(),
            move_issue_to_state=Mock(),
        )
        self.task_client = task_client
        self.task_data_access = TaskDataAccess(self.cfg.openhands_agent.youtrack, task_client)
        self.openhands_client = types.SimpleNamespace(
            implement_task=Mock(
                return_value={
                    ImplementationFields.SUCCESS: True,
                    'summary': 'Implemented task across repos',
                }
            ),
            fix_review_comment=Mock(return_value={ImplementationFields.SUCCESS: True}),
        )
        self.implementation_service = ImplementationService(self.openhands_client)
        self.repository_service = types.SimpleNamespace(
            validate_connections=Mock(),
            resolve_task_repositories=Mock(return_value=[self.client_repo, self.backend_repo]),
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
                        PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                        PullRequestFields.URL: 'https://bitbucket/pr/17',
                        PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                        PullRequestFields.DESTINATION_BRANCH: 'master',
                    },
                    {
                        PullRequestFields.REPOSITORY_ID: 'backend',
                        PullRequestFields.ID: '18',
                        PullRequestFields.TITLE: 'PROJ-1: Fix bug',
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
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )
        self.service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.repository_service,
            self.notification_service,
        )

    def test_init_rejects_missing_notification_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'notification_service is required'):
            AgentService(
                self.task_data_access,
                self.implementation_service,
                self.repository_service,
                None,
            )

    def test_validate_connections_checks_all_dependencies(self) -> None:
        self.task_client.validate_connection = Mock()
        self.openhands_client.validate_connection = Mock()

        self.service.validate_connections()

        self.task_client.validate_connection.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        self.openhands_client.validate_connection.assert_called_once_with()
        self.repository_service.validate_connections.assert_called_once_with()

    def test_validate_connections_raises_with_service_stack_traces(self) -> None:
        self.task_client.validate_connection = Mock(side_effect=RuntimeError('youtrack down'))
        self.openhands_client.validate_connection = Mock(side_effect=RuntimeError('openhands down'))
        self.service.logger = Mock()

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            self.service.validate_connections()

        self.assertEqual(self.service.logger.exception.call_count, 2)
        self.assertIn('[youtrack]', str(exc_context.exception))
        self.assertIn('[openhands]', str(exc_context.exception))

    def test_process_assigned_tasks_creates_prs_for_all_selected_repositories(self) -> None:
        results = self.service.process_assigned_tasks()

        self.assertEqual(
            results,
            [
                {
                    'id': 'PROJ-1',
                    StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
                    PullRequestFields.PULL_REQUESTS: [
                        {
                            PullRequestFields.REPOSITORY_ID: 'client',
                            PullRequestFields.ID: '17',
                            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                            PullRequestFields.URL: 'https://bitbucket/pr/17',
                            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                            PullRequestFields.DESTINATION_BRANCH: 'master',
                        },
                        {
                            PullRequestFields.REPOSITORY_ID: 'backend',
                            PullRequestFields.ID: '18',
                            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                            PullRequestFields.URL: 'https://github/pr/18',
                            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                            PullRequestFields.DESTINATION_BRANCH: 'main',
                        },
                    ],
                    PullRequestFields.FAILED_REPOSITORIES: [],
                }
            ],
        )
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.repository_service.create_pull_request.assert_any_call(
            self.client_repo,
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/client',
            description='Implemented task across repos',
        )
        self.repository_service.create_pull_request.assert_any_call(
            self.backend_repo,
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/backend',
            description='Implemented task across repos',
        )
        self.task_client.add_comment.assert_called_once()
        comment_text = self.task_client.add_comment.call_args.args[1]
        self.assertIn('Created pull requests:', comment_text)
        self.assertIn('client: https://bitbucket/pr/17', comment_text)
        self.assertIn('backend: https://github/pr/18', comment_text)
        self.task_client.move_issue_to_state.assert_called_once_with('PROJ-1', 'State', 'In Review')
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        completion_email = self.email_core_lib.send.call_args_list[0].args[1]
        self.assertEqual(completion_email[EmailFields.TASK_ID], 'PROJ-1')
        self.assertIn('client: PROJ-1: Fix bug', completion_email[EmailFields.PULL_REQUEST_SUMMARY])
        self.assertIn('backend: PROJ-1: Fix bug', completion_email[EmailFields.PULL_REQUEST_SUMMARY])
        self.assertEqual(
            self.service._pull_request_context_map,
            {
                '17': [
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        'branch_name': 'feature/proj-1/client',
                    }
                ],
                '18': [
                    {
                        PullRequestFields.REPOSITORY_ID: 'backend',
                        'branch_name': 'feature/proj-1/backend',
                    }
                ],
            },
        )

    def test_process_assigned_tasks_skips_when_no_tasks_exist(self) -> None:
        self.task_client.get_assigned_tasks.return_value = []

        results = self.service.process_assigned_tasks()

        self.assertEqual(results, [])
        self.openhands_client.implement_task.assert_not_called()

    def test_process_assigned_tasks_skips_execution_without_success_flag(self) -> None:
        self.openhands_client.implement_task.return_value = {}
        self.service.logger = Mock()

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_tasks()

        self.assertEqual(results, [])
        self.repository_service.create_pull_request.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.email_core_lib.send.assert_not_called()
        self.service.logger.warning.assert_called_once_with(
            'implementation failed for task %s',
            'PROJ-1',
        )

    def test_process_assigned_tasks_handles_ambiguous_or_missing_repository_scope(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = ValueError('no configured repository matched task PROJ-1')

        results = self.service.process_assigned_tasks()

        self.assertEqual(results, [])
        self.task_client.add_comment.assert_called_once()
        self.assertIn('could not safely process this task', self.task_client.add_comment.call_args.args[1])
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_tasks_reports_partial_pr_failures_without_moving_review(self) -> None:
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            RuntimeError('github down'),
        ]

        results = self.service.process_assigned_tasks()

        self.assertEqual(results[0][StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        self.assertEqual(results[0][PullRequestFields.FAILED_REPOSITORIES], ['backend'])
        self.task_client.move_issue_to_state.assert_not_called()
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_tasks_raises_when_move_to_review_fails(self) -> None:
        self.task_client.move_issue_to_state.side_effect = RuntimeError('state update failed')

        with self.assertRaisesRegex(RuntimeError, 'state update failed'):
            self.service.process_assigned_tasks()

    def test_process_assigned_tasks_ignores_completion_notification_failures(self) -> None:
        self.notification_service.notify_task_ready_for_review = Mock(side_effect=RuntimeError('smtp failed'))
        self.service.logger = Mock()

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_tasks()

        self.assertEqual(results[0][StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.service.logger.exception.assert_called_once_with(
            'failed to send completion notification for task %s',
            'PROJ-1',
        )

    def test_handle_pull_request_comment_updates_known_branch_and_repository(self) -> None:
        self.service._pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
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
        comment_arg = self.openhands_client.fix_review_comment.call_args.args[0]
        self.assertEqual(getattr(comment_arg, PullRequestFields.REPOSITORY_ID), 'client')

    def test_handle_pull_request_comment_rejects_unknown_pull_request(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unknown pull request id'):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_handle_pull_request_comment_rejects_ambiguous_pull_request(self) -> None:
        self.service._pull_request_context_map['17'] = [
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

    def test_handle_pull_request_comment_raises_when_fix_fails(self) -> None:
        self.openhands_client.fix_review_comment.return_value = {ImplementationFields.SUCCESS: False}
        self.service._pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            }
        ]

        with self.assertRaisesRegex(RuntimeError, 'failed to address comment 99'):
            self.service.handle_pull_request_comment(build_review_comment_payload())
