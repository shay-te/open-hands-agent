import unittest
from unittest.mock import ANY, Mock, patch

from kato_core_lib.kato_core_lib import KatoCoreLib
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from task_core_lib.task_core_lib.platform import Platform
from tests.utils import build_task, build_test_cfg


class KatoCoreLibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = build_test_cfg()

    def _build_workflow_app(self, *, testing_container_enabled: bool):
        cfg = build_test_cfg()
        cfg.kato.openhands.testing_container_enabled = testing_container_enabled
        cfg.kato.openhands.testing_base_url = 'https://openhands-testing.example'
        cfg.kato.openhands.testing_llm_model = 'openai/gpt-4o-mini'
        cfg.kato.openhands.testing_llm_base_url = 'https://api.openai.com/v1'

        task = build_task(description='Update the client flow and cover it with tests')
        ticket_client = Mock()
        ticket_client.provider_name = 'youtrack'
        ticket_client.max_retries = cfg.kato.retry.max_retries
        ticket_client.get_assigned_tasks.return_value = [task]
        ticket_client.issue = ticket_client

        implementation_client = Mock(name='implementation_kato_client')
        implementation_client.max_retries = cfg.kato.retry.max_retries
        implementation_client.implement_task.return_value = {
            'success': True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            'commit_message': 'Implement PROJ-1',
            'summary': 'Files changed:\n- client/app.ts\n  Updated the client flow.',
        }

        testing_client = Mock(name='testing_kato_client')
        testing_client.max_retries = cfg.kato.retry.max_retries
        testing_client.test_task.return_value = {
            'success': True,
            'summary': 'Testing agent validated the implementation',
        }

        repository = cfg.kato.repositories[0]
        repository_service = Mock()
        # validate_connections is now lazy: if ``_repositories`` is None
        # the connection validator skips per-repo iteration. Mock() would
        # otherwise auto-create a non-iterable Mock for this attribute.
        repository_service._repositories = None
        repository_service.repositories = [repository]
        repository_service.resolve_task_repositories.return_value = [repository]
        repository_service.prepare_task_repositories.side_effect = lambda repositories: repositories
        repository_service.prepare_task_branches.side_effect = (
            lambda repositories, repository_branches: repositories
        )
        repository_service.build_branch_name.return_value = 'feature/proj-1/client'
        repository_service.create_pull_request.return_value = {
            PullRequestFields.REPOSITORY_ID: repository.id,
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: fix it already',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
            PullRequestFields.DESTINATION_BRANCH: 'main',
        }

        # The Restricted Execution Protocol gate would refuse the
        # synthetic test repos because they're not on any approval
        # sidecar. These tests cover workflow routing, not REP, so
        # patch the service to a stub that pretends every repo is
        # already approved.
        approval_service_stub = Mock()
        approval_service_stub.unapproved_repository_ids.return_value = []
        approval_service_stub.restricted_mode_repository_ids.return_value = []

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib',
        return_value=ticket_client,
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient',
            side_effect=[implementation_client, testing_client],
        ) as mock_kato_client_cls, patch(
            'kato_core_lib.kato_core_lib.RepositoryService',
            return_value=repository_service,
        ), patch.object(
            KatoCoreLib,
            '_build_repository_approval_service',
            return_value=approval_service_stub,
        ):
            app = KatoCoreLib(cfg)

        return (
            app,
            cfg,
            task,
            ticket_client,
            implementation_client,
            testing_client,
            repository_service,
            mock_email_core_lib_cls.return_value,
            mock_kato_client_cls,
        )

    def test_builds_data_access_and_service_in_core_lib(self) -> None:
        implementation_client = Mock(name='implementation_kato_client')
        testing_client = Mock(name='testing_kato_client')

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ) as mock_TaskCoreLib, patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient',
            side_effect=[implementation_client, testing_client],
        ) as mock_kato_client_cls, patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ) as mock_task_da_cls, patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ) as mock_task_service_cls, patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ) as mock_task_state_service_cls, patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ) as mock_impl_service_cls, patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ) as mock_testing_service_cls, patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ) as mock_repository_service_cls, patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ) as mock_notification_service_cls, patch(
            'kato_core_lib.kato_core_lib.TaskPreflightService'
        ) as mock_task_preflight_service_cls, patch(
            'kato_core_lib.kato_core_lib.TaskFailureHandler'
        ) as mock_task_failure_handler_cls, patch(
            'kato_core_lib.kato_core_lib.TaskPublisher'
        ) as mock_task_publisher_cls, patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ) as mock_service_cls:
            app = KatoCoreLib(self.cfg)

        mock_email_core_lib_cls.assert_called_once_with(self.cfg)
        mock_TaskCoreLib.assert_called_once_with(
            Platform.YOUTRACK,
            self.cfg.kato.youtrack,
            self.cfg.kato.retry.max_retries,
        )
        self.assertEqual(mock_kato_client_cls.call_count, 2)
        self.assertEqual(
            mock_kato_client_cls.call_args_list[0].args,
            (
                self.cfg.kato.openhands.base_url,
                self.cfg.kato.openhands.api_key,
                self.cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[0].kwargs,
            {
                'llm_settings': {
                    'llm_model': self.cfg.kato.openhands.llm_model,
                    'llm_base_url': self.cfg.kato.openhands.llm_base_url,
                },
                'poll_interval_seconds': 2.0,
                'max_poll_attempts': 900,
                'model_smoke_test_enabled': True,
            },
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].args,
            (
                self.cfg.kato.openhands.base_url,
                self.cfg.kato.openhands.api_key,
                self.cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].kwargs,
            {
                'llm_settings': {
                    'llm_model': self.cfg.kato.openhands.llm_model,
                    'llm_base_url': self.cfg.kato.openhands.llm_base_url,
                },
                'poll_interval_seconds': 2.0,
                'max_poll_attempts': 900,
                'model_smoke_test_enabled': False,
            },
        )
        mock_repository_service_cls.assert_called_once_with(
            self.cfg.kato,
            self.cfg.kato.retry.max_retries,
        )
        mock_task_preflight_service_cls.assert_called_once_with(
            task_model_access_validator=ANY,
            task_service=mock_task_service_cls.return_value,
            repository_service=mock_repository_service_cls.return_value,
            task_branch_push_validator=ANY,
            task_branch_publishability_validator=ANY,
            workspace_provisioner=ANY,
            security_scanner_service=ANY,
            repository_approval_service=ANY,
            runtime_posture_supplier=ANY,
        )
        mock_task_failure_handler_cls.assert_called_once_with(
            task_service=mock_task_service_cls.return_value,
            task_state_service=mock_task_state_service_cls.return_value,
            repository_service=mock_repository_service_cls.return_value,
            notification_service=mock_notification_service_cls.return_value,
        )
        mock_task_publisher_cls.assert_called_once_with(
            task_service=mock_task_service_cls.return_value,
            task_state_service=mock_task_state_service_cls.return_value,
            repository_service=mock_repository_service_cls.return_value,
            notification_service=mock_notification_service_cls.return_value,
            state_registry=ANY,
            failure_handler=mock_task_failure_handler_cls.return_value,
            publish_max_retries=ANY,
        )
        mock_task_da_cls.assert_called_once_with(
            self.cfg.kato.youtrack,
            mock_TaskCoreLib.return_value.issue,
        )
        mock_task_service_cls.assert_called_once_with(
            self.cfg.kato.youtrack,
            mock_task_da_cls.return_value,
        )
        mock_task_state_service_cls.assert_called_once_with(
            self.cfg.kato.youtrack,
            mock_task_da_cls.return_value,
        )
        mock_impl_service_cls.assert_called_once_with(implementation_client)
        mock_testing_service_cls.assert_called_once_with(testing_client)
        mock_notification_service_cls.assert_called_once_with(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=mock_email_core_lib_cls.return_value,
            failure_email_cfg=self.cfg.kato.failure_email,
            completion_email_cfg=self.cfg.kato.completion_email,
        )
        mock_service_cls.assert_called_once_with(
            task_service=mock_task_service_cls.return_value,
            task_state_service=mock_task_state_service_cls.return_value,
            implementation_service=mock_impl_service_cls.return_value,
            testing_service=mock_testing_service_cls.return_value,
            repository_service=mock_repository_service_cls.return_value,
            notification_service=mock_notification_service_cls.return_value,
            state_registry=ANY,
            review_comment_service=ANY,
            task_failure_handler=mock_task_failure_handler_cls.return_value,
            task_publisher=mock_task_publisher_cls.return_value,
            repository_connections_validator=ANY,
            startup_validator=ANY,
            task_preflight_service=mock_task_preflight_service_cls.return_value,
            skip_testing=False,
            planning_session_runner=None,
            session_manager=None,
            workspace_manager=ANY,
            parallel_task_runner=ANY,
            wait_planning_service=ANY,
            triage_service=ANY,
            review_workspace_ttl_seconds=ANY,
            lessons_service=ANY,
        )
        mock_service_cls.return_value.validate_connections.assert_called_once_with()
        self.assertIs(app.service, mock_service_cls.return_value)

    def test_rejects_runtime_source_fingerprint_mismatch(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.source_fingerprint = 'expected-fingerprint'

        with patch(
            'kato_core_lib.kato_core_lib.runtime_source_fingerprint',
            return_value='current-fingerprint',
        ), patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskPreflightService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskFailureHandler'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskPublisher'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ):
            with self.assertRaises(RuntimeError) as exc:
                KatoCoreLib(cfg)

        self.assertIn('source fingerprint mismatch', str(exc.exception))
        self.assertIn('rebuild the Kato image before running', str(exc.exception))

    def test_uses_testing_container_base_url_and_llm_settings_when_enabled(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.openhands.testing_container_enabled = True
        cfg.kato.openhands.testing_base_url = 'https://openhands-testing.example'
        cfg.kato.openhands.testing_llm_model = 'openai/gpt-4o-mini'
        cfg.kato.openhands.testing_llm_base_url = 'https://api.openai.com/v1'

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ) as mock_kato_client_cls, patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ):
            KatoCoreLib(cfg)

        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].args,
            (
                'https://openhands-testing.example',
                cfg.kato.openhands.api_key,
                cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].kwargs['llm_settings'],
            {
                'llm_model': 'openai/gpt-4o-mini',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )

    def test_process_assigned_task_routes_testing_to_dedicated_openhands_when_enabled(self) -> None:
        (
            app,
            cfg,
            _task,
            ticket_client,
            implementation_client,
            testing_client,
            repository_service,
            email_core_lib,
            mock_kato_client_cls,
        ) = self._build_workflow_app(testing_container_enabled=True)

        assigned_task = _task
        result = app.service.process_assigned_task(assigned_task)

        self.assertEqual(
            mock_kato_client_cls.call_args_list[0].args,
            (
                cfg.kato.openhands.base_url,
                cfg.kato.openhands.api_key,
                cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].args,
            (
                'https://openhands-testing.example',
                cfg.kato.openhands.api_key,
                cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].kwargs['llm_settings'],
            {
                'llm_model': 'openai/gpt-4o-mini',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )
        implementation_client.implement_task.assert_called_once()
        implementation_client.test_task.assert_not_called()
        testing_client.test_task.assert_called_once()
        testing_client.implement_task.assert_not_called()
        ticket_client.add_comment.assert_called()
        repository_service.create_pull_request.assert_called_once()
        email_core_lib.send.assert_called()
        self.assertEqual(
            result[StatusFields.STATUS],
            StatusFields.READY_FOR_REVIEW,
        )

    def test_process_assigned_task_routes_testing_to_main_openhands_when_testing_container_disabled(
        self,
    ) -> None:
        (
            app,
            cfg,
            _task,
            _ticket_client,
            implementation_client,
            testing_client,
            repository_service,
            _email_core_lib,
            mock_kato_client_cls,
        ) = self._build_workflow_app(testing_container_enabled=False)

        assigned_task = _task
        result = app.service.process_assigned_task(assigned_task)

        self.assertEqual(
            mock_kato_client_cls.call_args_list[0].args,
            (
                cfg.kato.openhands.base_url,
                cfg.kato.openhands.api_key,
                cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].args,
            (
                cfg.kato.openhands.base_url,
                cfg.kato.openhands.api_key,
                cfg.kato.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_kato_client_cls.call_args_list[1].kwargs['llm_settings'],
            {
                'llm_model': cfg.kato.openhands.llm_model,
                'llm_base_url': cfg.kato.openhands.llm_base_url,
            },
        )
        implementation_client.implement_task.assert_called_once()
        implementation_client.test_task.assert_not_called()
        testing_client.test_task.assert_called_once()
        testing_client.implement_task.assert_not_called()
        repository_service.create_pull_request.assert_called_once()
        self.assertEqual(
            result[StatusFields.STATUS],
            StatusFields.READY_FOR_REVIEW,
        )

    def test_exposes_service_instance(self) -> None:
        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.get_assigned_tasks.return_value = ["task-1"]
            mock_service_cls.return_value.process_assigned_task.return_value = {"id": "17"}
            app = KatoCoreLib(self.cfg)

        mock_service_cls.return_value.validate_connections.assert_called_once_with()
        self.assertIs(app.service, mock_service_cls.return_value)

    def test_service_handles_comment_operations(self) -> None:
        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.handle_pull_request_comment.return_value = {"status": "updated"}
            app = KatoCoreLib(self.cfg)

        mock_service_cls.return_value.validate_connections.assert_called_once_with()
        self.assertIs(app.service, mock_service_cls.return_value)

    def test_builds_jira_ticket_client_when_configured(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.issue_platform = 'jira'

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ) as mock_TaskCoreLib, patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ):
            KatoCoreLib(cfg)

        mock_TaskCoreLib.assert_called_once_with(
            Platform.JIRA,
            cfg.kato.jira,
            cfg.kato.retry.max_retries,
        )

    def test_uses_claude_cli_backend_when_configured(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.agent_backend = 'claude'
        cfg.kato.claude = {
            'binary': 'claude',
            'model': 'claude-opus-4-7',
            'max_turns': '',
            'allowed_tools': '',
            'disallowed_tools': '',
            'bypass_permissions': True,
            'timeout_seconds': 1800,
            'model_smoke_test_enabled': False,
        }

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ) as mock_kato_client_cls, patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient'
        ) as mock_claude_client_cls, patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ):
            KatoCoreLib(cfg)

        mock_kato_client_cls.assert_not_called()
        # Two clients (implementation + testing) created via Claude CLI factory.
        self.assertEqual(mock_claude_client_cls.call_count, 2)
        first_kwargs = mock_claude_client_cls.call_args_list[0].kwargs
        self.assertEqual(first_kwargs['binary'], 'claude')
        self.assertEqual(first_kwargs['model'], 'claude-opus-4-7')
        self.assertIs(first_kwargs['bypass_permissions'], True)

    def test_docker_mode_env_threads_through_to_claude_client(self) -> None:
        """``KATO_CLAUDE_DOCKER=true`` must reach every Claude spawn point."""
        cfg = build_test_cfg()
        cfg.kato.agent_backend = 'claude'
        cfg.kato.claude = {
            'binary': 'claude',
            'model': 'claude-opus-4-7',
            'max_turns': '',
            'allowed_tools': '',
            'disallowed_tools': '',
            'bypass_permissions': False,
            'timeout_seconds': 1800,
            'model_smoke_test_enabled': False,
        }

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient'
        ) as mock_claude_client_cls, patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ), patch(
            'kato_core_lib.kato_core_lib.PlanningSessionRunner'
        ) as mock_runner_cls, patch(
            'kato_core_lib.kato_core_lib.is_docker_mode_enabled', return_value=True,
        ):
            KatoCoreLib(cfg)

        # Both clients (implementation + testing) get docker_mode_on=True.
        for call in mock_claude_client_cls.call_args_list:
            self.assertIs(call.kwargs['docker_mode_on'], True)
        # PlanningSessionRunner.from_config also sees docker_mode_on=True.
        from_config_kwargs = mock_runner_cls.from_config.call_args.kwargs
        self.assertIs(from_config_kwargs['docker_mode_on'], True)

    def test_docker_mode_default_off_threads_through_to_claude_client(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.agent_backend = 'claude'
        cfg.kato.claude = {
            'binary': 'claude',
            'model': 'claude-opus-4-7',
            'max_turns': '',
            'allowed_tools': '',
            'disallowed_tools': '',
            'bypass_permissions': False,
            'timeout_seconds': 1800,
            'model_smoke_test_enabled': False,
        }

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient'
        ) as mock_claude_client_cls, patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskStateService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ), patch(
            'kato_core_lib.kato_core_lib.is_docker_mode_enabled', return_value=False,
        ):
            KatoCoreLib(cfg)

        for call in mock_claude_client_cls.call_args_list:
            self.assertIs(call.kwargs['docker_mode_on'], False)

    def test_env_var_to_spawn_argv_end_to_end(self) -> None:
        """End-to-end: ``KATO_CLAUDE_DOCKER=true`` env → ClaudeCliClient
        spawn-argv contains the docker wrap.

        Bridges the link-by-link chain tests (env-var detection,
        kato_core_lib threading, ClaudeCliClient sandbox-wrap) with one
        assertion that proves the chain actually produces a wrapped
        spawn end-to-end. Catches the regression class where each layer
        forwards correctly in isolation but the chain breaks at one
        layer that was modified without its forwarding test being kept.
        """
        import json
        import os
        import subprocess

        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        from kato_core_lib.kato_core_lib import KatoCoreLib
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            is_docker_mode_enabled,
        )

        cfg = build_test_cfg()
        cfg.kato.agent_backend = 'claude'
        cfg.kato.claude = {
            'binary': 'claude',
            'model': 'claude-opus-4-7',
            'max_turns': '',
            'allowed_tools': '',
            'disallowed_tools': '',
            'bypass_permissions': False,
            'timeout_seconds': 1800,
            'model_smoke_test_enabled': False,
        }

        completed = subprocess.CompletedProcess(
            args=['docker'],
            returncode=0,
            stdout=json.dumps({'is_error': False, 'result': 'ok', 'agent_session_id': 's'}),
            stderr='',
        )

        with patch.dict(os.environ, {'KATO_CLAUDE_DOCKER': 'true'}, clear=False):
            # Read 1: validator helper sees the env var.
            self.assertTrue(is_docker_mode_enabled())
            # Build the ClaudeCliClient via the production builder
            # (the same path KatoCoreLib uses now — through the
            # ``agent_core_lib`` factory) — this is the chain under
            # test.
            client = KatoCoreLib._build_agent_client(
                cfg.kato,
                cfg.kato.retry.max_retries,
                docker_mode_on=is_docker_mode_enabled(),
            )
            self.assertIsInstance(client, ClaudeCliClient)
            self.assertTrue(client._docker_mode_on)

            # Trigger a per-task spawn and verify the docker wrap fires.
            with patch(
                'claude_core_lib.claude_core_lib.cli_client.subprocess.run',
                return_value=completed,
            ) as mock_run, patch(
                'sandbox_core_lib.sandbox_core_lib.manager.ensure_image',
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate',
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.enforce_no_workspace_secrets',
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.record_spawn',
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.wrap_command',
                return_value=['docker', 'run', '--rm', 'kato-sandbox', 'claude'],
            ) as mock_wrap, patch(
                'sandbox_core_lib.sandbox_core_lib.manager.make_container_name',
                return_value='kato-sandbox-PROJ-1-end2end',
            ):
                client.test_task(build_task())

        # End-to-end assertion: env var → builder → client → spawn-argv
        # is a docker wrap, not a raw claude command.
        mock_wrap.assert_called_once()
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv[:2], ['docker', 'run'])

    def test_rejects_unsupported_agent_backend(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.agent_backend = 'gemini'

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ):
            with self.assertRaisesRegex(ValueError, 'unsupported KATO_AGENT_BACKEND'):
                KatoCoreLib(cfg)

    def test_always_instantiates_email_core_lib_directly(self) -> None:
        cfg = build_test_cfg()
        delattr(cfg.core_lib, 'email_core_lib')

        with patch(
            'kato_core_lib.kato_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'kato_core_lib.kato_core_lib.TaskCoreLib'
        ), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient'
        ), patch(
            'kato_core_lib.kato_core_lib.RepositoryService'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskDataAccess'
        ), patch(
            'kato_core_lib.kato_core_lib.TaskService'
        ), patch(
            'kato_core_lib.kato_core_lib.ImplementationService'
        ), patch(
            'kato_core_lib.kato_core_lib.TestingService'
        ), patch(
            'kato_core_lib.kato_core_lib.NotificationService'
        ), patch(
            'kato_core_lib.kato_core_lib.AgentService'
        ):
            KatoCoreLib(cfg)

        mock_email_core_lib_cls.assert_called_once_with(cfg)
