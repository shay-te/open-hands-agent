from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from jira_core_lib.jira_core_lib.client.jira_client import JiraClient


class JiraCoreLib(CoreLib):
    """Compose the Jira ticket client."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        jira_cfg = cfg.core_lib.jira_core_lib
        # ``assignee`` is the Jira account-id / username the host scans
        # tasks under — i.e. the kato bot user. Re-used as ``bot_login``
        # so the client can filter @-mentioned-elsewhere comments from
        # the agent context. Defaults to empty (filter disabled) when
        # an older config yaml omits the key.
        self.issue = JiraClient(
            jira_cfg.base_url,
            jira_cfg.token,
            jira_cfg.email,
            jira_cfg.max_retries,
            bot_login=str(getattr(jira_cfg, 'assignee', '') or ''),
        )
