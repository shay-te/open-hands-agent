from omegaconf import DictConfig

from core_lib.core_lib import CoreLib
from email_core_lib.email_core_lib import EmailCoreLib

from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.ticket_client_factory import build_ticket_client
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.agent_state_registry import AgentStateRegistry
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_failure_handler import TaskFailureHandler
from openhands_agent.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from openhands_agent.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from openhands_agent.data_layers.service.task_publisher import TaskPublisher
from openhands_agent.data_layers.service.task_state_service import TaskStateService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.data_layers.service.testing_service import TestingService
from openhands_agent.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from openhands_agent.validation.branch_push import TaskBranchPushValidator
from openhands_agent.validation.model_access import TaskModelAccessValidator
from openhands_agent.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from openhands_agent.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.openhands_config_utils import (
    resolved_openhands_base_url,
    resolved_openhands_llm_settings,
    skip_testing_enabled,
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
        task_service = TaskService(ticket_cfg, task_data_access)
        task_state_service = TaskStateService(ticket_cfg, task_data_access)
        repository_service = RepositoryService(open_cfg, retry_cfg.max_retries)
        notification_service = self._build_notification_service(open_cfg)
        state_registry = AgentStateRegistry()
        repository_connections_validator = RepositoryConnectionsValidator(repository_service)
        startup_validator = StartupDependencyValidator(
            repository_connections_validator,
            task_service,
            implementation_service,
            testing_service,
            skip_testing_enabled(open_cfg.openhands),
        )
        task_model_access_validator = TaskModelAccessValidator(
            implementation_service,
        )
        task_branch_push_validator = TaskBranchPushValidator(repository_service)
        task_branch_publishability_validator = TaskBranchPublishabilityValidator(
            repository_service
        )
        task_preflight_service = TaskPreflightService(
            task_model_access_validator=task_model_access_validator,
            task_service=task_service,
            repository_service=repository_service,
            task_branch_push_validator=task_branch_push_validator,
            task_branch_publishability_validator=task_branch_publishability_validator,
        )
        task_failure_handler = TaskFailureHandler(
            task_service=task_service,
            task_state_service=task_state_service,
            repository_service=repository_service,
            notification_service=notification_service,
        )
        task_publisher = TaskPublisher(
            task_service=task_service,
            task_state_service=task_state_service,
            repository_service=repository_service,
            notification_service=notification_service,
            state_registry=state_registry,
            failure_handler=task_failure_handler,
        )
        review_comment_service = ReviewCommentService(
            task_service=task_service,
            implementation_service=implementation_service,
            repository_service=repository_service,
            state_registry=state_registry,
        )
        return AgentService(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=implementation_service,
            testing_service=testing_service,
            repository_service=repository_service,
            notification_service=notification_service,
            state_registry=state_registry,
            review_comment_service=review_comment_service,
            task_failure_handler=task_failure_handler,
            task_publisher=task_publisher,
            repository_connections_validator=repository_connections_validator,
            startup_validator=startup_validator,
            task_preflight_service=task_preflight_service,
            skip_testing=skip_testing_enabled(open_cfg.openhands),
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
            model_smoke_test_enabled=not testing
            and bool(getattr(openhands_cfg, 'model_smoke_test_enabled', True)),
        )

    @staticmethod
    def _openhands_poll_interval_seconds(openhands_cfg: DictConfig) -> float:
        return float(openhands_cfg.get('poll_interval_seconds', 2.0))

    @staticmethod
    def _openhands_max_poll_attempts(openhands_cfg: DictConfig) -> int:
        return int(openhands_cfg.get('max_poll_attempts', 900))
