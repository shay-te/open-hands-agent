import unittest
from unittest.mock import Mock, patch

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from utils import build_test_cfg


class OpenHandsAgentCoreLibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = build_test_cfg()

    def test_builds_data_access_and_service_in_core_lib(self) -> None:
        mock_db_connection = Mock()
        implementation_client = Mock(name='implementation_openhands_client')
        testing_client = Mock(name='testing_openhands_client')

        with patch(
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg',
            return_value=mock_db_connection,
        ) as mock_get_or_reg, patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ) as mock_build_ticket_client, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient',
            side_effect=[implementation_client, testing_client],
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ) as mock_state_data_access_cls, patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ) as mock_task_da_cls, patch(
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

        mock_get_or_reg.assert_called_once_with(self.cfg.core_lib.data.sqlalchemy)
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
            },
        )
        mock_repository_service_cls.assert_called_once_with(
            self.cfg.openhands_agent,
            self.cfg.openhands_agent.retry.max_retries,
        )
        mock_state_data_access_cls.assert_called_once_with(
            self.cfg.openhands_agent.state.file_path,
        )
        mock_task_da_cls.assert_called_once_with(
            self.cfg.openhands_agent.youtrack,
            mock_build_ticket_client.return_value,
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
            task_data_access=mock_task_da_cls.return_value,
            implementation_service=mock_impl_service_cls.return_value,
            testing_service=mock_testing_service_cls.return_value,
            repository_service=mock_repository_service_cls.return_value,
            notification_service=mock_notification_service_cls.return_value,
            state_data_access=mock_state_data_access_cls.return_value,
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
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
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

    def test_exposes_service_instance(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
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
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
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
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ) as mock_build_ticket_client, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
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
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ) as mock_build_ticket_client, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
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
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.EmailCoreLib'
        ) as mock_email_core_lib_cls, patch(
            'openhands_agent.openhands_agent_core_lib.build_ticket_client'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.RepositoryService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
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

    def test_install_upgrades_database_to_head(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.GlobalHydra.instance'
        ) as mock_hydra_instance, patch(
            'openhands_agent.openhands_agent_core_lib.command.upgrade'
        ) as mock_upgrade:
            OpenHandsAgentCoreLib.install(self.cfg)

        mock_hydra_instance.return_value.clear.assert_called_once_with()
        mock_upgrade.assert_called_once()
        _, revision = mock_upgrade.call_args.args
        self.assertEqual(revision, 'head')

    def test_uninstall_downgrades_database_to_base(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.GlobalHydra.instance'
        ) as mock_hydra_instance, patch(
            'openhands_agent.openhands_agent_core_lib.command.downgrade'
        ) as mock_downgrade:
            OpenHandsAgentCoreLib.uninstall(self.cfg)

        mock_hydra_instance.return_value.clear.assert_called_once_with()
        mock_downgrade.assert_called_once()
        _, revision = mock_downgrade.call_args.args
        self.assertEqual(revision, 'base')

    def test_create_generates_named_migration(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.command.revision'
        ) as mock_revision:
            OpenHandsAgentCoreLib.create(self.cfg, 'add_repository_table')

        mock_revision.assert_called_once()
        self.assertEqual(
            mock_revision.call_args.kwargs,
            {'message': 'add_repository_table', 'autogenerate': True},
        )

    def test_downgrade_steps_back_one_revision(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.command.downgrade'
        ) as mock_downgrade:
            OpenHandsAgentCoreLib.downgrade(self.cfg)

        mock_downgrade.assert_called_once()
        _, revision = mock_downgrade.call_args.args
        self.assertEqual(revision, '-1')
