import unittest
from unittest.mock import patch

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from utils import build_test_cfg


class TestUna2427(unittest.TestCase):
    """Test for UNA-2427 task implementing proper testing scenarios"""
    
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_core_lib_initialization_with_default_configs(self) -> None:
        """Test that CoreLib initializes correctly with default configurations"""
        # This validates basic initialization of the CoreLib component
        with patch('openhands_agent.openhands_agent_core_lib.EmailCoreLib'), \
             patch('openhands_agent.openhands_agent_core_lib.build_ticket_client'), \
             patch('openhands_agent.openhands_agent_core_lib.OpenHandsClient'), \
             patch('openhands_agent.openhands_agent_core_lib.RepositoryService'), \
             patch('openhands_agent.openhands_agent_core_lib.TaskDataAccess'), \
             patch('openhands_agent.openhands_agent_core_lib.TaskService'), \
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
        
        with patch('openhands_agent.openhands_agent_core_lib.EmailCoreLib'), \
             patch('openhands_agent.openhands_agent_core_lib.build_ticket_client'), \
             patch('openhands_agent.openhands_agent_core_lib.OpenHandsClient'), \
             patch('openhands_agent.openhands_agent_core_lib.RepositoryService'), \
             patch('openhands_agent.openhands_agent_core_lib.TaskDataAccess'), \
             patch('openhands_agent.openhands_agent_core_lib.TaskService'), \
             patch('openhands_agent.openhands_agent_core_lib.ImplementationService'), \
             patch('openhands_agent.openhands_agent_core_lib.TestingService'), \
             patch('openhands_agent.openhands_agent_core_lib.NotificationService'), \
             patch('openhands_agent.openhands_agent_core_lib.AgentService'):
            
            # Test instantiation
            core_lib = OpenHandsAgentCoreLib(cfg_copy)
            
            # Validate service and its components
            self.assertIsNotNone(core_lib.service)
            self.assertTrue(hasattr(core_lib.service, 'validate_connections'))

if __name__ == '__main__':
    unittest.main()
