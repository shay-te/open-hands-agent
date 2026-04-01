from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig

from core_lib.core_lib import CoreLib
from email_core_lib.email_core_lib import EmailCoreLib

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.ticket_client_factory import build_ticket_client
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.data_layers.service.testing_service import TestingService
from openhands_agent.logging_utils import configure_logger
from openhands_agent.openhands_config_utils import (
    resolved_openhands_base_url,
    resolved_openhands_llm_settings,
)

logger = configure_logger('OpenHandsAgentCoreLib')
ISSUE_PLATFORM_CONFIG_NAMES = {
    'youtrack': 'youtrack',
    'jira': 'jira',
    'github': 'github_issues',
    'github_issues': 'github_issues',
    'gitlab': 'gitlab_issues',
    'gitlab_issues': 'gitlab_issues',
    'bitbucket': 'bitbucket_issues',
    'bitbucket_issues': 'bitbucket_issues',
}


class OpenHandsAgentCoreLib(CoreLib):
    @staticmethod
    def install(cfg: DictConfig):
        GlobalHydra.instance().clear()
        logger.info('Installing OpenHandsAgentCoreLib without a local database')
        logger.info('OpenHandsAgentCoreLib installed successfully')

    @staticmethod
    def uninstall(cfg: DictConfig):
        GlobalHydra.instance().clear()
        logger.info('Uninstalling OpenHandsAgentCoreLib without a local database')
        logger.info('OpenHandsAgentCoreLib uninstalled successfully')

    @staticmethod
    def create(cfg: DictConfig, name: str):
        logger.info('Skipping migration creation for %s because local database support is disabled', name)

    @staticmethod
    def downgrade(cfg: DictConfig):
        logger.info('Skipping database downgrade because local database support is disabled')

    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self.logger = configure_logger(cfg.core_lib.app.name)
        self.service = self._build_agent_service(cfg.openhands_agent)
        self.service.validate_connections()

    def _build_agent_service(self, open_cfg: DictConfig) -> AgentService:
        retry_cfg = open_cfg.retry
        issue_platform, ticket_cfg = self._resolve_ticket_platform_config(open_cfg)
        ticket_client = build_ticket_client(
            issue_platform,
            ticket_cfg,
            retry_cfg.max_retries,
        )
        implementation_service = ImplementationService(
            self._build_openhands_client(
                open_cfg.openhands,
                retry_cfg.max_retries,
            )
        )
        testing_service = TestingService(
            self._build_openhands_client(
                open_cfg.openhands,
                retry_cfg.max_retries,
                testing=True,
            )
        )
        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        return AgentService(
            task_service=TaskService(ticket_cfg, task_data_access),
            implementation_service=implementation_service,
            testing_service=testing_service,
            repository_service=RepositoryService(open_cfg, retry_cfg.max_retries),
            notification_service=self._build_notification_service(open_cfg),
        )

    @staticmethod
    def _resolve_ticket_platform_config(
        open_cfg: DictConfig,
    ) -> tuple[str, DictConfig]:
        issue_platform = str(
            open_cfg.issue_platform
            or open_cfg.ticket_system
            or 'youtrack'
        ).strip().lower()
        config_name = ISSUE_PLATFORM_CONFIG_NAMES.get(issue_platform)
        ticket_cfg = getattr(open_cfg, config_name, None) if config_name else None
        if ticket_cfg is None:
            raise ValueError(f'missing issue platform config for: {issue_platform}')
        return issue_platform, ticket_cfg

    def _build_notification_service(self, open_cfg: DictConfig) -> NotificationService:
        return NotificationService(
            app_name=self.config.core_lib.app.name,
            email_core_lib=EmailCoreLib(self.config),
            failure_email_cfg=open_cfg.failure_email,
            completion_email_cfg=open_cfg.completion_email,
        )

    @classmethod
    def _build_openhands_client(
        cls,
        openhands_cfg: DictConfig,
        max_retries: int,
        *,
        testing: bool = False,
    ) -> OpenHandsClient:
        return OpenHandsClient(
            resolved_openhands_base_url(openhands_cfg, testing=testing),
            openhands_cfg.api_key,
            max_retries,
            llm_settings=resolved_openhands_llm_settings(
                openhands_cfg,
                testing=testing,
            ),
            poll_interval_seconds=cls._openhands_poll_interval_seconds(openhands_cfg),
            max_poll_attempts=cls._openhands_max_poll_attempts(openhands_cfg),
        )

    @staticmethod
    def _openhands_poll_interval_seconds(openhands_cfg: DictConfig) -> float:
        return float(openhands_cfg.get('poll_interval_seconds', 2.0))

    @staticmethod
    def _openhands_max_poll_attempts(openhands_cfg: DictConfig) -> int:
        return int(openhands_cfg.get('max_poll_attempts', 900))
