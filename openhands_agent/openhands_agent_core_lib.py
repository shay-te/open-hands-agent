from alembic import command
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig

from core_lib.core_lib import CoreLib
from email_core_lib.email_core_lib import EmailCoreLib

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.ticket_client_factory import build_ticket_client
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
from openhands_agent.alembic_config import build_alembic_config
from openhands_agent.logging_utils import configure_logger

logger = configure_logger('OpenHandsAgentCoreLib')


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
        issue_platform = str(
            open_cfg.issue_platform
            or open_cfg.ticket_system
            or 'youtrack'
        ).strip().lower()
        ticket_cfg = {
            'youtrack': open_cfg.youtrack,
            'jira': open_cfg.jira,
            'github': open_cfg.github_issues,
            'github_issues': open_cfg.github_issues,
            'gitlab': open_cfg.gitlab_issues,
            'gitlab_issues': open_cfg.gitlab_issues,
            'bitbucket': open_cfg.bitbucket_issues,
            'bitbucket_issues': open_cfg.bitbucket_issues,
        }.get(issue_platform)
        if ticket_cfg is None:
            raise ValueError(f'missing issue platform config for: {issue_platform}')

        CoreLib.connection_factory_registry.get_or_reg(self.config.core_lib.data.sqlalchemy)
        _email_core_lib = EmailCoreLib(cfg)
        _ticket_client = build_ticket_client(issue_platform, ticket_cfg, retry_cfg.max_retries)
        _implementation_openhands_client = OpenHandsClient(
            open_cfg.openhands.base_url,
            open_cfg.openhands.api_key,
            retry_cfg.max_retries,
            llm_settings=self._openhands_llm_settings(open_cfg.openhands),
            poll_interval_seconds=self._openhands_poll_interval_seconds(open_cfg.openhands),
            max_poll_attempts=self._openhands_max_poll_attempts(open_cfg.openhands),
        )
        _testing_openhands_client = OpenHandsClient(
            self._testing_openhands_base_url(open_cfg.openhands),
            open_cfg.openhands.api_key,
            retry_cfg.max_retries,
            llm_settings=self._testing_openhands_llm_settings(open_cfg.openhands),
            poll_interval_seconds=self._openhands_poll_interval_seconds(open_cfg.openhands),
            max_poll_attempts=self._openhands_max_poll_attempts(open_cfg.openhands),
        )
        _task_data_access = TaskDataAccess(ticket_cfg, _ticket_client)
        _implementation_service = ImplementationService(_implementation_openhands_client)
        _testing_service = TestingService(_testing_openhands_client)
        _repository_service = RepositoryService(open_cfg, retry_cfg.max_retries)
        _state_data_access = AgentStateDataAccess(open_cfg.state.file_path)
        notification_service = NotificationService(
            app_name=self.config.core_lib.app.name,
            email_core_lib=_email_core_lib,
            failure_email_cfg=open_cfg.failure_email,
            completion_email_cfg=open_cfg.completion_email,
        )
        self.service = AgentService(
            task_data_access=_task_data_access,
            implementation_service=_implementation_service,
            testing_service=_testing_service,
            repository_service=_repository_service,
            notification_service=notification_service,
            state_data_access=_state_data_access,
        )
        self.service.validate_connections()

    @staticmethod
    def _openhands_llm_settings(openhands_cfg: DictConfig) -> dict[str, str]:
        return {
            'llm_model': str(getattr(openhands_cfg, 'llm_model', '') or '').strip(),
            'llm_base_url': str(getattr(openhands_cfg, 'llm_base_url', '') or '').strip(),
        }

    @staticmethod
    def _testing_openhands_base_url(openhands_cfg: DictConfig) -> str:
        if not bool(getattr(openhands_cfg, 'testing_container_enabled', False)):
            return str(openhands_cfg.base_url or '').strip()
        return str(getattr(openhands_cfg, 'testing_base_url', '') or '').strip()

    @classmethod
    def _testing_openhands_llm_settings(cls, openhands_cfg: DictConfig) -> dict[str, str]:
        if not bool(getattr(openhands_cfg, 'testing_container_enabled', False)):
            return cls._openhands_llm_settings(openhands_cfg)
        return {
            'llm_model': str(getattr(openhands_cfg, 'testing_llm_model', '') or '').strip(),
            'llm_base_url': str(getattr(openhands_cfg, 'testing_llm_base_url', '') or '').strip(),
        }

    @staticmethod
    def _openhands_poll_interval_seconds(openhands_cfg: DictConfig) -> float:
        return float(openhands_cfg.get('poll_interval_seconds', 2.0))

    @staticmethod
    def _openhands_max_poll_attempts(openhands_cfg: DictConfig) -> int:
        return int(openhands_cfg.get('max_poll_attempts', 900))
