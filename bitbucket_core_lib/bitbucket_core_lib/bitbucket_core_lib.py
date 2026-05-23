from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_client import BitbucketClient
from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_issues_client import (
    BitbucketIssuesClient,
)


class BitbucketCoreLib(CoreLib):
    """Compose Bitbucket repository and issue clients."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        bitbucket_cfg = cfg.core_lib.bitbucket_core_lib
        pr_username = bitbucket_cfg.get('api_email', '') or bitbucket_cfg.get('username', '')
        self.pull_request = BitbucketClient(
            bitbucket_cfg.base_url,
            bitbucket_cfg.token,
            bitbucket_cfg.max_retries,
            username=pr_username,
        )
        # ``assignee`` is the Bitbucket login the host scans tasks
        # under — re-used as ``bot_login`` so the client can filter
        # @-mentioned-elsewhere comments. Falls back to ``username``
        # for older configs where the bot's auth identity is also
        # its scanning identity. Defaults to empty when neither key
        # is set (filter disabled).
        self.issue = BitbucketIssuesClient(
            bitbucket_cfg.base_url,
            bitbucket_cfg.token,
            bitbucket_cfg.workspace,
            bitbucket_cfg.get('repo_slug', ''),
            bitbucket_cfg.max_retries,
            username=bitbucket_cfg.get('username', ''),
            bot_login=str(
                bitbucket_cfg.get('assignee', '')
                or bitbucket_cfg.get('username', '')
                or ''
            ),
        )
