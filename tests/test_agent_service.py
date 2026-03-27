import types
import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data.review_comment import ReviewComment
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
    ReviewCommentFields,
    StatusFields,
)
from openhands_agent.data_layers.service.testing_service import TestingService
from utils import build_review_comment_payload, build_task, build_test_cfg


class AgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self.client_repo = self.cfg.openhands_agent.repositories[0]
        self.backend_repo = self.cfg.openhands_agent.repositories[1]
        task_client = types.SimpleNamespace(
            provider_name='youtrack',
            get_assigned_tasks=Mock(return_value=[build_task(description='Update client and backend APIs')]),
            add_comment=Mock(),
            move_issue_to_state=Mock(),
        )
        self.task_client = task_client
        self.task_data_access = TaskDataAccess(self.cfg.openhands_agent.youtrack, task_client)
        self.openhands_client = types.SimpleNamespace(
            validate_connection=Mock(),
            implement_task=Mock(
                return_value={
                    ImplementationFields.SUCCESS: True,
                    ImplementationFields.SESSION_ID: 'conversation-1',
                    'summary': 'Implemented task across repos',
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
        self.implementation_service = ImplementationService(self.openhands_client)
        self.testing_service = TestingService(self.openhands_client)
        self.repository_service = types.SimpleNamespace(
            validate_connections=Mock(),
            resolve_task_repositories=Mock(return_value=[self.client_repo, self.backend_repo]),
            get_repository=Mock(side_effect=lambda repository_id: {
                'client': self.client_repo,
                'backend': self.backend_repo,
            }[repository_id]),
            list_pull_request_comments=Mock(return_value=[]),
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
            self.testing_service,
            self.repository_service,
            self.notification_service,
        )

    def test_init_rejects_missing_testing_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'testing_service is required'):
            AgentService(
                self.task_data_access,
                self.implementation_service,
                None,
                self.repository_service,
                self.notification_service,
            )

    def test_init_rejects_missing_notification_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'notification_service is required'):
            AgentService(
                self.task_data_access,
                self.implementation_service,
                self.testing_service,
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
        self.assertEqual(self.openhands_client.validate_connection.call_count, 2)
        self.repository_service.validate_connections.assert_called_once_with()

    def test_validate_connections_checks_state_when_configured(self) -> None:
        state_data_access = types.SimpleNamespace(validate=Mock())
        service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            state_data_access=state_data_access,
        )
        self.task_client.validate_connection = Mock()
        self.openhands_client.validate_connection = Mock()

        service.validate_connections()

        state_data_access.validate.assert_called_once_with()

    def test_validate_connections_raises_with_service_stack_traces(self) -> None:
        self.task_client.validate_connection = Mock(side_effect=RuntimeError('youtrack down'))
        self.openhands_client.validate_connection = Mock(side_effect=RuntimeError('openhands down'))
        self.service.logger = Mock()

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            self.service.validate_connections()

        self.assertEqual(self.service.logger.exception.call_count, 3)
        self.assertIn('- unable to validate youtrack: youtrack down', str(exc_context.exception))
        self.assertIn('- unable to validate openhands: openhands down', str(exc_context.exception))
        self.assertIn('- unable to validate openhands_testing: openhands down', str(exc_context.exception))
        self.assertIn('Details:', str(exc_context.exception))
        self.assertIn('[youtrack]', str(exc_context.exception))
        self.assertIn('[openhands]', str(exc_context.exception))
        self.assertIn('[openhands_testing]', str(exc_context.exception))

    def test_validate_connections_summarizes_retryable_failures_with_attempt_count(self) -> None:
        self.task_client.validate_connection = Mock()
        self.openhands_client.max_retries = 5
        self.openhands_client.validate_connection = Mock(side_effect=ConnectionError('connection refused'))
        self.service.logger = Mock()

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            self.service.validate_connections()

        self.assertIn(
            '- unable to connect to openhands (tried 5 times)',
            str(exc_context.exception),
        )
        self.assertIn(
            '- unable to connect to openhands_testing (tried 5 times)',
            str(exc_context.exception),
        )

    def test_process_assigned_task_creates_prs_for_all_selected_repositories(self) -> None:
        task = self.task_data_access.get_assigned_tasks()[0]
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
            },
        )
        self.repository_service.resolve_task_repositories.assert_called_once()
        self.openhands_client.test_task.assert_called_once()
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
        self.assertIn('Published review links:', comment_text)
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
                        ImplementationFields.SESSION_ID: 'conversation-1',
                    }
                ],
                '18': [
                    {
                        PullRequestFields.REPOSITORY_ID: 'backend',
                        'branch_name': 'feature/proj-1/backend',
                        ImplementationFields.SESSION_ID: 'conversation-1',
                    }
                ],
            },
        )

    def test_get_assigned_tasks_returns_empty_list_when_no_tasks_exist(self) -> None:
        self.task_client.get_assigned_tasks.return_value = []

        results = self.service.get_assigned_tasks()

        self.assertEqual(results, [])

    def test_process_assigned_task_skips_already_processed_tasks(self) -> None:
        state_data_access = types.SimpleNamespace(
            is_task_processed=Mock(return_value=True),
            get_processed_task=Mock(
                return_value={
                    PullRequestFields.PULL_REQUESTS: [
                        {
                            PullRequestFields.REPOSITORY_ID: 'client',
                            PullRequestFields.ID: '17',
                        }
                    ]
                }
            ),
        )
        service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            state_data_access=state_data_access,
        )
        task = self.task_data_access.get_assigned_tasks()[0]

        results = service.process_assigned_task(task)

        self.assertEqual(
            results,
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.SKIPPED,
                PullRequestFields.PULL_REQUESTS: [
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        PullRequestFields.ID: '17',
                    }
                ],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )
        self.repository_service.resolve_task_repositories.assert_not_called()
        self.openhands_client.implement_task.assert_not_called()

    def test_process_assigned_task_skips_execution_without_success_flag(self) -> None:
        self.openhands_client.implement_task.return_value = {}
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.openhands_client.test_task.assert_not_called()
        self.repository_service.create_pull_request.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.task_client.add_comment.assert_called_once()
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        self.service.logger.warning.assert_called_once_with(
            'implementation failed for task %s: %s',
            'PROJ-1',
            'implementation agent reported the task is not ready',
        )

    def test_process_assigned_task_handles_ambiguous_or_missing_repository_scope(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = ValueError('no configured repository matched task PROJ-1')
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertIsNone(results)
        self.task_client.add_comment.assert_called_once()
        self.assertIn('could not safely process this task', self.task_client.add_comment.call_args.args[1])
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_reports_testing_failures_before_pr_creation(self) -> None:
        self.openhands_client.test_task.return_value = {
            ImplementationFields.SUCCESS: False,
            'summary': 'backend tests are still failing',
        }
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.TESTING_FAILED)
        self.repository_service.create_pull_request.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        self.service.logger.warning.assert_called_once_with(
            'testing failed for task %s: %s',
            'PROJ-1',
            'backend tests are still failing',
        )

    def test_process_assigned_task_reports_partial_pr_failures_without_moving_review(self) -> None:
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
        task = self.task_data_access.get_assigned_tasks()[0]

        results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        self.assertEqual(results[PullRequestFields.FAILED_REPOSITORIES], ['backend'])
        self.task_client.move_issue_to_state.assert_not_called()
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_process_assigned_task_continues_when_move_to_review_fails(self) -> None:
        self.task_client.move_issue_to_state.side_effect = RuntimeError('state update failed')
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        self.service.logger.exception.assert_called_once_with(
            'failed to move task %s to review',
            'PROJ-1',
        )

    def test_process_assigned_task_ignores_completion_notification_failures(self) -> None:
        self.notification_service.notify_task_ready_for_review = Mock(side_effect=RuntimeError('smtp failed'))
        self.service.logger = Mock()
        task = self.task_data_access.get_assigned_tasks()[0]

        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_task(task)

        self.assertEqual(results[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.service.logger.exception.assert_called_once_with(
            'failed to send completion notification for task %s',
            'PROJ-1',
        )

    def test_handle_pull_request_comment_updates_known_branch_and_repository(self) -> None:
        self.service._pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
                ImplementationFields.SESSION_ID: 'conversation-1',
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
        self.assertEqual(
            self.openhands_client.fix_review_comment.call_args.args[2],
            'conversation-1',
        )

    def test_process_review_comment_marks_comment_processed(self) -> None:
        state_data_access = types.SimpleNamespace(
            mark_review_comment_processed=Mock(),
        )
        service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            state_data_access=state_data_access,
        )
        service._pull_request_context_map['17'] = [
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

        state_data_access.mark_review_comment_processed.assert_called_once_with(
            'client',
            '17',
            '99',
        )

    def test_handle_pull_request_comment_rejects_unknown_pull_request(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unknown pull request id'):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_handle_pull_request_comment_loads_persisted_context_after_restart(self) -> None:
        state_data_access = types.SimpleNamespace(
            get_pull_request_contexts=Mock(
                return_value=[
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        'branch_name': 'feature/proj-1/client',
                        ImplementationFields.SESSION_ID: 'conversation-1',
                    }
                ]
            ),
            mark_review_comment_processed=Mock(),
        )
        service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            state_data_access=state_data_access,
        )

        result = service.handle_pull_request_comment(build_review_comment_payload())

        self.assertEqual(result[PullRequestFields.REPOSITORY_ID], 'client')
        state_data_access.get_pull_request_contexts.assert_called_once_with('17')

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

    def test_handle_pull_request_comment_uses_repository_id_to_resolve_ambiguity(self) -> None:
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
        payload = build_review_comment_payload()
        payload[PullRequestFields.REPOSITORY_ID] = 'backend'

        result = self.service.handle_pull_request_comment(payload)

        self.assertEqual(result[PullRequestFields.REPOSITORY_ID], 'backend')

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

    def test_get_new_pull_request_comments_returns_unprocessed_comments_with_context(self) -> None:
        state_data_access = types.SimpleNamespace(
            list_pull_request_contexts=Mock(
                return_value=[
                    {
                        PullRequestFields.ID: '17',
                        PullRequestFields.REPOSITORY_ID: 'client',
                        'branch_name': 'feature/proj-1/client',
                    }
                ]
            ),
            is_review_comment_processed=Mock(side_effect=[False, True]),
        )
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
            self.implementation_service,
            self.testing_service,
            self.repository_service,
            self.notification_service,
            state_data_access=state_data_access,
        )

        comments = service.get_new_pull_request_comments()

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].comment_id, '98')
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
