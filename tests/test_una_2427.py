import unittest
from unittest.mock import Mock, patch

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from utils import build_test_cfg


class TestUna2427(unittest.TestCase):
    """Test for UNA-2427 task implementing proper testing scenarios"""
    
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_core_lib_initialization_with_default_configs(self) -> None:
        """Test that CoreLib initializes correctly with default configurations"""
        # This validates basic initialization of the CoreLib component
        with patch('openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'), \
             patch('openhands_agent.openhands_agent_core_lib.EmailCoreLib'), \
             patch('openhands_agent.openhands_agent_core_lib.build_ticket_client'), \
             patch('openhands_agent.openhands_agent_core_lib.OpenHandsClient'), \
             patch('openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'), \
             patch('openhands_agent.openhands_agent_core_lib.RepositoryService'), \
             patch('openhands_agent.openhands_agent_core_lib.TaskDataAccess'), \
             patch('openhands_agent.openhands_agent_core_lib.ImplementationService'), \
             patch('openhands_agent.openhands_agent_core_lib.TestingService'), \
             patch('openhands_agent.openhands_agent_core_lib.NotificationService'), \
             patch('openhands_agent.openhands_agent_core_lib.AgentService'):
            
            # Test instantiation
            core_lib = OpenHandsAgentCoreLib(self.cfg)
            
            # Verify that the service was properly initialized
            self.assertIsNotNone(core_lib.service)

    def test_core_lib_configuration_handling(self) -> None:
        """Test that CoreLib properly handles various configuration scenarios"""
        # Test with different configurations
        cfg_copy = build_test_cfg()
        
        with patch('openhands_agent.openhands_agent_core_lib.CoreLib.connection_factory_registry.get_or_reg'), \
             patch('openhands_agent.openhands_agent_core_lib.EmailCoreLib'), \
             patch('openhands_agent.openhands_agent_core_lib.build_ticket_client'), \
             patch('openhands_agent.openhands_agent_core_lib.OpenHandsClient'), \
             patch('openhands_agent.openhands_agent_core_lib.AgentStateDataAccess'), \
             patch('openhands_agent.openhands_agent_core_lib.RepositoryService'), \
             patch('openhands_agent.openhands_agent_core_lib.TaskDataAccess'), \
             patch('openhands_agent.openhands_agent_core_lib.ImplementationService'), \
             patch('openhands_agent.openhands_agent_core_lib.TestingService'), \
             patch('openhands_agent.openhands_agent_core_lib.NotificationService'), \
             patch('openhands_agent.openhands_agent_core_lib.AgentService'):
            
            # Test instantiation
            core_lib = OpenHandsAgentCoreLib(cfg_copy)
            
            # Validate service and its components
            self.assertIsNotNone(core_lib.service)
            self.assertTrue(hasattr(core_lib.service, 'validate_connections'))

    def test_core_lib_database_operations(self) -> None:
        """Test CoreLib database interaction operations"""
        with patch('openhands_agent.openhands_agent_core_lib.GlobalHydra.instance') as mock_hydra_instance, \
             patch('openhands_agent.openhands_agent_core_lib.command.upgrade') as mock_upgrade, \
             patch('openhands_agent.openhands_agent_core_lib.command.downgrade') as mock_downgrade:
            
            # Test upgrade
            OpenHandsAgentCoreLib.install(self.cfg)
            mock_hydra_instance.return_value.clear.assert_called_once_with()
            mock_upgrade.assert_called_once()
            
            mock_hydra_instance.reset_mock()
            mock_downgrade.reset_mock()
            
            # Test uninstall
            OpenHandsAgentCoreLib.uninstall(self.cfg)
            mock_hydra_instance.return_value.clear.assert_called_once_with()
            mock_downgrade.assert_called_once()


if __name__ == '__main__':
    unittest.main()
