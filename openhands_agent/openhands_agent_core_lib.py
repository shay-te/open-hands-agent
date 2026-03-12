import logging

from alembic import command
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig

from core_lib.core_lib import CoreLib
from email_core_lib.email_core_lib import EmailCoreLib

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data_access.agent_state_data_access import (
    AgentStateDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.testing_service import TestingService
from openhands_agent.create_db import build_alembic_config
from openhands_agent.logging_utils import configure_logger

logger = logging.getLogger(__name__)


class OpenHandsAgentCoreLib(CoreLib):
    @staticmethod
    def install(cfg: DictConfig):
        GlobalHydra.instance().clear()
        logger.info('Installing OpenHandsAgentCoreLib')
        command.upgrade(build_alembic_config(cfg), 'head')
        logger.info('OpenHandsAgentCoreLib installed successfully')

    @staticmethod
    def uninstall(cfg: DictConfig):
        GlobalHydra.instance().clear()
        logger.info('Uninstalling OpenHandsAgentCoreLib')
        command.downgrade(build_alembic_config(cfg), 'base')
        logger.info('OpenHandsAgentCoreLib uninstalled successfully')

    @staticmethod
    def create(cfg: DictConfig, name: str):
        command.revision(build_alembic_config(cfg), message=name, autogenerate=True)

    @staticmethod
    def downgrade(cfg: DictConfig):
        command.downgrade(build_alembic_config(cfg), '-1')

    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self.logger = configure_logger(cfg.core_lib.app.name)
        open_cfg = cfg.openhands_agent
        retry_cfg = open_cfg.retry

        CoreLib.connection_factory_registry.get_or_reg(self.config.core_lib.data.sqlalchemy)
        _email_core_lib = EmailCoreLib(cfg) if hasattr(cfg.core_lib, 'email_core_lib') else None
        _youtrack_client = YouTrackClient(open_cfg.youtrack.base_url, open_cfg.youtrack.token, retry_cfg.max_retries)
        _implementation_openhands_client = OpenHandsClient(
            open_cfg.openhands.base_url,
            open_cfg.openhands.api_key,
            retry_cfg.max_retries,
        )
        _testing_openhands_client = OpenHandsClient(
            open_cfg.openhands.base_url,
            open_cfg.openhands.api_key,
            retry_cfg.max_retries,
        )
        repositories_cfg = getattr(open_cfg, 'repositories', None) or [open_cfg.repository]
        _task_data_access = TaskDataAccess(open_cfg.youtrack, _youtrack_client)
        _implementation_service = ImplementationService(_implementation_openhands_client)
        _testing_service = TestingService(_testing_openhands_client)
        _repository_service = RepositoryService(repositories_cfg, retry_cfg.max_retries)
        _state_data_access = AgentStateDataAccess(open_cfg.state.file_path)
        notification_service = NotificationService(app_name=self.config.core_lib.app.name, email_core_lib=_email_core_lib, failure_email_cfg=getattr(open_cfg, 'failure_email', None), completion_email_cfg=getattr(open_cfg, 'completion_email', None))
        self.service = AgentService(
            task_data_access=_task_data_access,
            implementation_service=_implementation_service,
            testing_service=_testing_service,
            repository_service=_repository_service,
            notification_service=notification_service,
            state_data_access=_state_data_access,
        )
        self.service.validate_connections()
