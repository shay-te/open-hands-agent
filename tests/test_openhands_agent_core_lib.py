import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from utils import build_test_cfg


class OpenHandsAgentCoreLibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = build_test_cfg()

    def test_builds_data_access_and_service_in_core_lib(self) -> None:
        mock_db_connection = Mock()

        with patch(
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg',
            return_value=mock_db_connection,
        ) as mock_get_or_reg, patch(
            'openhands_agent.openhands_agent_core_lib.YouTrackClient'
        ) as mock_youtrack_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.BitbucketClient'
        ) as mock_bitbucket_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ) as mock_task_da_cls, patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ) as mock_impl_service_cls, patch(
            'openhands_agent.openhands_agent_core_lib.PullRequestDataAccess'
        ) as mock_pr_da_cls, patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            app = OpenHandsAgentCoreLib(self.cfg)

        mock_get_or_reg.assert_called_once_with(self.cfg.core_lib.data.sqlalchemy)
        mock_youtrack_client_cls.assert_called_once_with(self.cfg.openhands_agent.youtrack.base_url, self.cfg.openhands_agent.youtrack.token)
        mock_openhands_client_cls.assert_called_once_with(self.cfg.openhands_agent.openhands.base_url, self.cfg.openhands_agent.openhands.api_key)
        mock_bitbucket_client_cls.assert_called_once_with(self.cfg.openhands_agent.bitbucket.base_url, self.cfg.openhands_agent.bitbucket.token)
        mock_task_da_cls.assert_called_once_with(self.cfg.openhands_agent.youtrack, mock_youtrack_client_cls.return_value)
        mock_impl_service_cls.assert_called_once_with(mock_openhands_client_cls.return_value)
        mock_pr_da_cls.assert_called_once_with(self.cfg.openhands_agent.bitbucket, mock_bitbucket_client_cls.return_value)
        mock_service_cls.assert_called_once_with(
            task_data_access=mock_task_da_cls.return_value,
            implementation_service=mock_impl_service_cls.return_value,
            pull_request_data_access=mock_pr_da_cls.return_value,
        )
        self.assertIs(app.service, mock_service_cls.return_value)
        self.assertIs(app._db_connection, mock_db_connection)
        self.assertIs(app._task_data_access, mock_task_da_cls.return_value)
        self.assertIs(app._implementation_service, mock_impl_service_cls.return_value)
        self.assertIs(app._pull_request_data_access, mock_pr_da_cls.return_value)

    def test_exposes_service_instance(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.YouTrackClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.BitbucketClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.PullRequestDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.process_assigned_tasks.return_value = [{"id": "17"}]
            app = OpenHandsAgentCoreLib(self.cfg)

        self.assertIs(app.service, mock_service_cls.return_value)

    def test_service_handles_comment_operations(self) -> None:
        with patch(
            'openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.YouTrackClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.BitbucketClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationService'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.PullRequestDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.handle_pull_request_comment.return_value = {"status": "updated"}
            app = OpenHandsAgentCoreLib(self.cfg)

        self.assertIs(app.service, mock_service_cls.return_value)
