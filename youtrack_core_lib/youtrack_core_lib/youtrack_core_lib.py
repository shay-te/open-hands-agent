from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client import YouTrackClient


class YouTrackCoreLib(CoreLib):
    """Compose the YouTrack issue client."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        youtrack_cfg = cfg.core_lib.youtrack_core_lib
        operational_comment_prefixes = tuple(
            list(getattr(youtrack_cfg, 'operational_comment_prefixes', None) or [])
        )
        # ``assignee`` is the YouTrack login the host scans for tasks
        # under — i.e. the kato bot user. Re-use it as ``bot_login``
        # so the client can filter @-mentioned-elsewhere comments
        # from the agent context. (See YouTrackClientBase docstring
        # for the filter semantics.)
        self.issue = YouTrackClient(
            youtrack_cfg.base_url,
            youtrack_cfg.token,
            youtrack_cfg.max_retries,
            operational_comment_prefixes=operational_comment_prefixes,
            bot_login=str(getattr(youtrack_cfg, 'assignee', '') or ''),
        )
