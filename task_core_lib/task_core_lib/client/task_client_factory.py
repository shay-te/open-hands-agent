from __future__ import annotations

from omegaconf import OmegaConf

from task_core_lib.task_core_lib.platform import Platform


class TaskClientFactory:
    """Build issue providers for the configured task platform.

    ``provider_factories`` is an optional ``dict[Platform, callable]``
    where each value is a ``(config, max_retries) -> issue_provider``
    callable.  When supplied, it is used directly — no platform libraries
    are imported.  When absent, :meth:`_build_default` performs lazy
    imports so this file can be imported in environments where optional
    platform dependencies are not installed.
    """

    def __init__(self, config, max_retries: int, *, provider_factories=None) -> None:
        self._config = config
        self._max_retries = max_retries
        self._provider_factories = provider_factories

    def get(self, platform: Platform):
        """Return the issue provider for *platform*, or ``None`` if unsupported."""
        if self._provider_factories is not None:
            factory = self._provider_factories.get(platform)
            return factory(self._config, self._max_retries) if factory else None
        return self._build_default(platform)

    def _build_default(self, platform: Platform):
        if platform == Platform.YOUTRACK:
            from youtrack_core_lib.youtrack_core_lib.youtrack_core_lib import (  # noqa: PLC0415
                YouTrackCoreLib,
            )
            # Resolve interpolations before wrapping to avoid circular references.
            config_dict = OmegaConf.to_container(self._config, resolve=True)
            youtrack_config = self._wrap_merged_config('youtrack_core_lib', config_dict)
            return YouTrackCoreLib(youtrack_config).issue

        if platform == Platform.JIRA:
            from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib  # noqa: PLC0415
            jira_config = self._wrap_merged_config('jira_core_lib', self._config)
            return JiraCoreLib(jira_config).issue

        if platform in {Platform.BITBUCKET, Platform.BITBUCKET_ISSUES}:
            from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import (  # noqa: PLC0415
                BitbucketCoreLib,
            )
            bitbucket_config = OmegaConf.create(
                {
                    'core_lib': {
                        'bitbucket_core_lib': {
                            'base_url': self._config.base_url,
                            'token': self._config.token,
                            'username': getattr(self._config, 'username', ''),
                            'api_email': getattr(self._config, 'api_email', ''),
                            'workspace': getattr(self._config, 'workspace', ''),
                            'repo_slug': getattr(self._config, 'repo_slug', ''),
                            'max_retries': self._max_retries,
                        },
                    },
                }
            )
            return BitbucketCoreLib(bitbucket_config).issue

        if platform in {Platform.GITHUB, Platform.GITHUB_ISSUES}:
            from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib  # noqa: PLC0415
            github_config = self._wrap_merged_config('github_core_lib', self._config)
            return GitHubCoreLib(github_config).issue

        if platform in {Platform.GITLAB, Platform.GITLAB_ISSUES}:
            from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib  # noqa: PLC0415
            gitlab_config = self._wrap_merged_config('gitlab_core_lib', self._config)
            return GitLabCoreLib(gitlab_config).issue

        return None

    def _wrap_merged_config(self, lib_key: str, base_config):
        """Wrap ``base_config`` merged with ``max_retries`` under
        ``core_lib.<lib_key>`` — the shared shape for the providers
        whose config is a plain merge of the task config block."""
        return OmegaConf.create(
            {
                'core_lib': {
                    lib_key: OmegaConf.merge(
                        base_config,
                        {'max_retries': self._max_retries},
                    ),
                },
            }
        )
