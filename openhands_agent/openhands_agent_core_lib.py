from omegaconf import DictConfig

from core_lib.connection.sql_alchemy_connection_factory import (
    SqlAlchemyConnectionFactory,
)
from core_lib.core_lib import CoreLib

from openhands_agent.client.bitbucket_client import BitbucketClient
from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)


class OpenHandsAgentCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        open_cfg = cfg.openhands_agent
        self._db_connection: SqlAlchemyConnectionFactory = (
            CoreLib.connection_factory_registry.get_or_reg(
                self.config.core_lib.data.sqlalchemy
            )
        )
        self._youtrack_client = YouTrackClient(open_cfg.youtrack.base_url, open_cfg.youtrack.token)
        self._openhands_client = OpenHandsClient(open_cfg.openhands.base_url, open_cfg.openhands.api_key)
        self._bitbucket_client = BitbucketClient(open_cfg.bitbucket.base_url, open_cfg.bitbucket.token)
        self._task_data_access = TaskDataAccess(
            open_cfg.youtrack,
            self._youtrack_client,
        )
        self._implementation_service = ImplementationService(
            self._openhands_client,
        )
        self._pull_request_data_access = PullRequestDataAccess(
            open_cfg.bitbucket,
            self._bitbucket_client,
        )
        self.service = AgentService(
            task_data_access=self._task_data_access,
            implementation_service=self._implementation_service,
            pull_request_data_access=self._pull_request_data_access,
        )
