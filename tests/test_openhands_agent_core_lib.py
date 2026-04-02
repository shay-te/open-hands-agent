import unittest
from unittest.mock import Mock, patch

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from openhands_agent.fields import PullRequestFields, StatusFields
from utils import build_task, build_test_cfg


class OpenHandsAgentCoreLibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = build_test_cfg()

    def _build_workflow_app(self, *, testing_container_enabled: bool):
        cfg = build_test_cfg()
        cfg.openhands_agent.openhands.testing_container_enabled = testing_container_enabled
        cfg.openhands_agent.openhands.testing_base_url = 'https://openhands-testing.example'
        cfg.openhands_agent.openhands.testing_llm_model = 'openai/gpt-4o-mini'
        cfg.openhands_agent.openhands.testing_llm_base_url = 'https://api.openai.com/v1'

        task = build_task(description='Update the client flow and cover it with tests')
        ticket_client = Mock()
        ticket_client.provider_name = 'youtrack'
        ticket_client.max_retries = cfg.openhands_agent.retry.max_retries
        ticket_client.get_assigned_tasks.return_value = [task]

        implementation_client = Mock(name='implementation_openhands_client')
        implementation_client.max_retries = cfg.openhands_agent.retry.max_retries
        implementation_client.implement_task.return_value = {
            'success': True,
            'session_id': 'conversation-1',
            'commit_message': 'Implement PROJ-1',
            'summary': 'Files changed:\n- client/app.ts\n  Updated the client flow.',
        }

        testing_client = Mock(name='testing_openhands_client')
        testing_client.max_retries = cfg.openhands_agent.retry.max_retries
        testing_client.test_task.return_value = {
            'success': True,
            'summary': 'Testing agent validated the implementation',
        }

        repository = cfg.openhands_agent.repositories[0]
        repository_service = Mock()
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
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
            PullRequestFields.DESTINATION_BRANCH: 'main',
        }

        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client',
        return_value=ticket_client,
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient',
            side_effect=[implementation_client, testing_client],
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService',
            return_value=repository_service,
        ):
            app = OpenHandsAgentCoreLib(cfg)

        return (
            app,
            cfg,
            task,
            ticket_client,
            implementation_client,
            testing_client,
            repository_service,
            mock_email_core_lib_cls.return_value,
            mock_openhands_client_cls,
        )

    def test_builds_data_access_and_service_in_core_lib(self) -> None:
        implementation_client = Mock(name='implementation_openhands_client')
        testing_client = Mock(name='testing_openhands_client')

        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ) as mock_build_ticket_client, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient',
            side_effect=[implementation_client, testing_client],
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ) as mock_task_da_cls, patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ) as mock_task_service_cls, patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ) as mock_impl_service_cls, patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ) as mock_testing_service_cls, patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ) as mock_repository_service_cls, patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ) as mock_notification_service_cls, patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            app = OpenHandsAgentCoreLib(self.cfg)

        mock_email_core_lib_cls.assert_called_once_with(self.cfg)
        mock_build_ticket_client.assert_called_once_with(
            'youtrack',
            self.cfg.openhands_agent.youtrack,
            self.cfg.openhands_agent.retry.max_retries,
        )
        self.assertEqual(mock_openhands_client_cls.call_count, 2)
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[0].args,
            (
                self.cfg.openhands_agent.openhands.base_url,
                self.cfg.openhands_agent.openhands.api_key,
                self.cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[0].kwargs,
            {
                'llm_settings': {
                    'llm_model': self.cfg.openhands_agent.openhands.llm_model,
                    'llm_base_url': self.cfg.openhands_agent.openhands.llm_base_url,
                },
                'poll_interval_seconds': 2.0,
                'max_poll_attempts': 900,
                'model_smoke_test_enabled': True,
            },
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].args,
            (
                self.cfg.openhands_agent.openhands.base_url,
                self.cfg.openhands_agent.openhands.api_key,
                self.cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].kwargs,
            {
                'llm_settings': {
                    'llm_model': self.cfg.openhands_agent.openhands.llm_model,
                    'llm_base_url': self.cfg.openhands_agent.openhands.llm_base_url,
                },
                'poll_interval_seconds': 2.0,
                'max_poll_attempts': 900,
                'model_smoke_test_enabled': False,
            },
        )
        mock_repository_service_cls.assert_called_once_with(
            self.cfg.openhands_agent,
            self.cfg.openhands_agent.retry.max_retries,
        )
        mock_task_da_cls.assert_called_once_with(
            self.cfg.openhands_agent.youtrack,
            mock_build_ticket_client.return_value,
        )
        mock_task_service_cls.assert_called_once_with(
            self.cfg.openhands_agent.youtrack,
            mock_task_da_cls.return_value,
        )
        mock_impl_service_cls.assert_called_once_with(implementation_client)
        mock_testing_service_cls.assert_called_once_with(testing_client)
        mock_notification_service_cls.assert_called_once_with(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=mock_email_core_lib_cls.return_value,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )
        mock_service_cls.assert_called_once_with(
            task_service=mock_task_service_cls.return_value,
            implementation_service=mock_impl_service_cls.return_value,
            testing_service=mock_testing_service_cls.return_value,
            repository_service=mock_repository_service_cls.return_value,
            notification_service=mock_notification_service_cls.return_value,
            skip_testing=False,
        )
        mock_service_cls.return_value.validate_connections.assert_called_once_with()
        self.assertIs(app.service, mock_service_cls.return_value)

    def test_uses_testing_container_base_url_and_llm_settings_when_enabled(self) -> None:
        cfg = build_test_cfg()
        cfg.openhands_agent.openhands.testing_container_enabled = True
        cfg.openhands_agent.openhands.testing_base_url = 'https://openhands-testing.example'
        cfg.openhands_agent.openhands.testing_llm_model = 'openai/gpt-4o-mini'
        cfg.openhands_agent.openhands.testing_llm_base_url = 'https://api.openai.com/v1'

        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ):
            OpenHandsAgentCoreLib(cfg)

        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].args,
            (
                'https://openhands-testing.example',
                cfg.openhands_agent.openhands.api_key,
                cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].kwargs['llm_settings'],
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
            mock_openhands_client_cls,
        ) = self._build_workflow_app(testing_container_enabled=True)

        assigned_task = app.service.get_assigned_tasks()[0]
        result = app.service.process_assigned_task(assigned_task)

        self.assertEqual(
            mock_openhands_client_cls.call_args_list[0].args,
            (
                cfg.openhands_agent.openhands.base_url,
                cfg.openhands_agent.openhands.api_key,
                cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].args,
            (
                'https://openhands-testing.example',
                cfg.openhands_agent.openhands.api_key,
                cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].kwargs['llm_settings'],
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
            mock_openhands_client_cls,
        ) = self._build_workflow_app(testing_container_enabled=False)

        assigned_task = app.service.get_assigned_tasks()[0]
        result = app.service.process_assigned_task(assigned_task)

        self.assertEqual(
            mock_openhands_client_cls.call_args_list[0].args,
            (
                cfg.openhands_agent.openhands.base_url,
                cfg.openhands_agent.openhands.api_key,
                cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].args,
            (
                cfg.openhands_agent.openhands.base_url,
                cfg.openhands_agent.openhands.api_key,
                cfg.openhands_agent.retry.max_retries,
            ),
        )
        self.assertEqual(
            mock_openhands_client_cls.call_args_list[1].kwargs['llm_settings'],
            {
                'llm_model': cfg.openhands_agent.openhands.llm_model,
                'llm_base_url': cfg.openhands_agent.openhands.llm_base_url,
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
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.get_assigned_tasks.return_value = ["task-1"]
            mock_service_cls.return_value.process_assigned_task.return_value = {"id": "17"}
            app = OpenHandsAgentCoreLib(self.cfg)

        mock_service_cls.return_value.validate_connections.assert_called_once_with()
        self.assertIs(app.service, mock_service_cls.return_value)

    def test_service_handles_comment_operations(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.handle_pull_request_comment.return_value = {"status": "updated"}
            app = OpenHandsAgentCoreLib(self.cfg)

        mock_service_cls.return_value.validate_connections.assert_called_once_with()
        self.assertIs(app.service, mock_service_cls.return_value)

    def test_builds_jira_ticket_client_when_configured(self) -> None:
        cfg = build_test_cfg()
        cfg.openhands_agent.ticket_system = 'jira'
        cfg.openhands_agent.issue_platform = ''

        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ) as mock_build_ticket_client, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ):
            OpenHandsAgentCoreLib(cfg)

        mock_build_ticket_client.assert_called_once_with(
            'jira',
            cfg.openhands_agent.jira,
            cfg.openhands_agent.retry.max_retries,
        )

    def test_issue_platform_takes_precedence_over_legacy_ticket_system(self) -> None:
        cfg = build_test_cfg()
        cfg.openhands_agent.ticket_system = 'youtrack'
        cfg.openhands_agent.issue_platform = 'github'

        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ) as mock_build_ticket_client, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ):
            OpenHandsAgentCoreLib(cfg)

        mock_build_ticket_client.assert_called_once_with(
            'github',
            cfg.openhands_agent.github_issues,
            cfg.openhands_agent.retry.max_retries,
        )

    def test_always_instantiates_email_core_lib_directly(self) -> None:
        cfg = build_test_cfg()
        delattr(cfg.core_lib, 'email_core_lib')

        with patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TestingService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.NotificationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ):
            OpenHandsAgentCoreLib(cfg)

        mock_email_core_lib_cls.assert_called_once_with(cfg)

    def test_install_logs_without_local_persistence(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.GlobalHydra.instance'
        ) as mock_hydra_instance, patch(
            'openhands_agent.openhands_agent_core_lib.logger.info'
        ) as mock_info:
            OpenHandsAgentCoreLib.install(self.cfg)

        mock_hydra_instance.return_value.clear.assert_called_once_with()
        mock_info.assert_any_call(
            'Installing OpenHandsAgentCoreLib without a local persistence layer'
        )
        mock_info.assert_any_call('OpenHandsAgentCoreLib installed successfully')

    def test_uninstall_logs_without_local_persistence(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.GlobalHydra.instance'
        ) as mock_hydra_instance, patch(
            'openhands_agent.openhands_agent_core_lib.logger.info'
        ) as mock_info:
            OpenHandsAgentCoreLib.uninstall(self.cfg)

        mock_hydra_instance.return_value.clear.assert_called_once_with()
        mock_info.assert_any_call(
            'Uninstalling OpenHandsAgentCoreLib without a local persistence layer'
        )
        mock_info.assert_any_call('OpenHandsAgentCoreLib uninstalled successfully')

    def test_create_is_a_noop_without_local_persistence(self) -> None:
        with patch('openhands_agent.openhands_agent_core_lib.logger.info') as mock_info:
            OpenHandsAgentCoreLib.create(self.cfg, 'add_repository_table')

        mock_info.assert_called_once_with(
            'Skipping core-lib create hook for %s because persistence support is disabled',
            'add_repository_table',
        )

    def test_downgrade_is_a_noop_without_local_persistence(self) -> None:
        with patch('openhands_agent.openhands_agent_core_lib.logger.info') as mock_info:
            OpenHandsAgentCoreLib.downgrade(self.cfg)

        mock_info.assert_called_once_with(
            'Skipping core-lib downgrade because persistence support is disabled'
        )
