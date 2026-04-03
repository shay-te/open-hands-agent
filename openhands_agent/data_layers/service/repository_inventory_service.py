import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from core_lib.data_layers.service.service import Service
from omegaconf import OmegaConf

from openhands_agent.client.pull_request_client_factory import build_pull_request_client
from openhands_agent.data_layers.data.fields import RepositoryFields
from openhands_agent.data_layers.data_access.pull_request_data_access import PullRequestDataAccess
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.repository_discovery_utils import (
    discover_git_repositories,
    display_name_from_repo_slug,
    remote_web_base_url,
    repository_id_from_name,
    review_url_for_remote,
)
from openhands_agent.helpers.text_utils import (
    normalized_lower_text,
    normalized_text,
    text_from_attr,
)


class RepositoryInventoryService(Service):
    """Own repository inventory, discovery, access configuration, and repository lookup."""
    _GENERIC_DISCOVERED_FOLDER_NAMES = {
        'project',
        'projects',
        'repo',
        'repos',
        'repository',
        'workspace',
    }

    def __init__(self, repositories_config, max_retries: int = 1) -> None:
        self.logger = configure_logger(self.__class__.__name__)
        self._max_retries = max_retries
        self._provider_api_defaults = self._provider_api_defaults_from_source(
            repositories_config
        )
        self._repositories = self._load_repositories(repositories_config)

    @property
    def repositories(self) -> list[object]:
        return list(self._repositories)

    def validate_connections(self) -> None:
        from openhands_agent.validation.repository_connections import (
            RepositoryConnectionsValidator,
        )

        RepositoryConnectionsValidator(self).validate()

    def resolve_task_repositories(self, task) -> list[object]:
        tagged_repositories = self._repositories_from_task_tags(task)
        if tagged_repositories:
            return tagged_repositories
        searchable_text = f'{task.summary}\n{task.description}'.lower()
        matches = [
            repository
            for repository in self._repositories
            if self._repository_matches(searchable_text, repository)
        ]
        if not matches:
            raise ValueError(f'no configured repository matched task {task.id}')
        return matches

    def _repositories_from_task_tags(self, task) -> list[object]:
        repository_tags = self._repository_tags(task)
        if not repository_tags:
            return []
        matches = [
            repository
            for repository in self._repositories
            if any(
                self._repository_matches(repository_tag, repository)
                for repository_tag in repository_tags
            )
        ]
        if not matches:
            raise ValueError(
                f'no configured repository matched repo tags on task {task.id}'
            )
        return matches

    def get_repository(self, repository_id: str):
        for repository in self._repositories:
            if repository.id == repository_id:
                return repository
        raise ValueError(f'unknown repository id: {repository_id}')

    def _validate_inventory(self) -> None:
        if not self._repositories:
            raise ValueError('at least one repository must be configured')

        seen_repository_ids: set[str] = set()
        seen_aliases: dict[str, str] = {}
        for repository in self._repositories:
            if repository.id in seen_repository_ids:
                raise ValueError(f'duplicate repository id: {repository.id}')
            seen_repository_ids.add(repository.id)
            for alias in self._repository_aliases(repository):
                if alias in seen_aliases and seen_aliases[alias] != repository.id:
                    raise ValueError(
                        f'duplicate repository alias "{alias}" for '
                        f'{seen_aliases[alias]} and {repository.id}'
                    )
                seen_aliases[alias] = repository.id

    @staticmethod
    def _normalized_repositories(repositories_config) -> list[object]:
        if repositories_config is None:
            return []
        if isinstance(repositories_config, list):
            return list(repositories_config)
        if hasattr(repositories_config, '__iter__') and not isinstance(repositories_config, str):
            try:
                return list(repositories_config)
            except TypeError:
                return [repositories_config]
        return [repositories_config]

    def _load_repositories(self, repository_source) -> list[object]:
        if self._looks_like_repository_settings(repository_source):
            configured_repositories = self._normalized_repositories(
                getattr(repository_source, 'repositories', None)
            )
            if configured_repositories:
                return configured_repositories
            discovered_repositories = self._discover_repositories_from_root(
                repository_source
            )
            if discovered_repositories:
                return discovered_repositories
            return []
        return self._normalized_repositories(repository_source)

    @staticmethod
    def _provider_api_defaults_from_source(repository_source) -> dict[str, dict[str, str]]:
        def provider_values(attribute: str) -> dict[str, str]:
            provider_cfg = getattr(repository_source, attribute, None)
            return {
                RepositoryFields.PROVIDER_BASE_URL: text_from_attr(provider_cfg, 'base_url'),
                'token': text_from_attr(provider_cfg, 'token'),
                'username': text_from_attr(provider_cfg, 'username'),
                'api_email': text_from_attr(provider_cfg, 'api_email'),
            }

        return {
            'github': provider_values('github_issues'),
            'gitlab': provider_values('gitlab_issues'),
            'bitbucket': provider_values('bitbucket_issues'),
        }

    @staticmethod
    def _looks_like_repository_settings(repository_source) -> bool:
        if repository_source is None:
            return False
        return any(
            hasattr(repository_source, attribute)
            for attribute in (
                'repositories',
                'repository_root_path',
            )
        )

    def _discover_repositories_from_root(self, repository_source) -> list[object]:
        root_path = text_from_attr(repository_source, 'repository_root_path')
        if not root_path:
            return []
        ignored_folders = self._ignored_repository_folders(repository_source)
        repositories: list[object] = []
        for discovered_repository in discover_git_repositories(root_path, ignored_folders):
            local_path = normalized_text(discovered_repository.local_path)
            folder_name = os.path.basename(local_path)
            repo_slug = normalized_text(discovered_repository.repo_slug or folder_name)
            repository_name = self._discovered_repository_name(folder_name, repo_slug)
            aliases = [folder_name, repo_slug]
            repositories.append(
                SimpleNamespace(
                    id=repository_id_from_name(repository_name),
                    display_name=display_name_from_repo_slug(repository_name),
                    local_path=local_path,
                    provider=normalized_text(discovered_repository.provider),
                    remote_url=normalized_text(discovered_repository.remote_url),
                    owner=normalized_text(discovered_repository.owner),
                    repo_slug=repo_slug,
                    aliases=[alias for alias in aliases if alias],
                )
            )
        return repositories

    @classmethod
    def _discovered_repository_name(cls, folder_name: str, repo_slug: str) -> str:
        normalized_folder_name = normalized_text(folder_name)
        normalized_repo_slug = normalized_text(repo_slug)
        if normalized_repo_slug and (
            not normalized_folder_name
            or normalized_folder_name.lower() in cls._GENERIC_DISCOVERED_FOLDER_NAMES
        ):
            return normalized_repo_slug
        return normalized_folder_name or normalized_repo_slug

    @staticmethod
    def _ignored_repository_folders(repository_source) -> list[str]:
        ignored_folders = getattr(repository_source, 'ignored_repository_folders', [])
        if isinstance(ignored_folders, str):
            return [
                normalized_text(folder)
                for folder in ignored_folders.split(',')
                if normalized_text(folder)
            ]
        if not ignored_folders:
            return []
        return [
            normalized_text(folder)
            for folder in ignored_folders
            if normalized_text(folder)
        ]

    def _repository_matches(self, searchable_text: str, repository) -> bool:
        return any(
            self._keyword_matches(searchable_text, keyword)
            for keyword in self._repository_aliases(repository)
        )

    @staticmethod
    def _keyword_matches(searchable_text: str, keyword: str) -> bool:
        if not keyword:
            return False
        pattern = rf'(?<![a-z0-9_.-]){re.escape(keyword.lower())}(?![a-z0-9_.-])'
        return re.search(pattern, searchable_text) is not None

    @staticmethod
    def _repository_aliases(repository) -> list[str]:
        local_path_alias = os.path.basename(text_from_attr(repository, 'local_path'))
        if local_path_alias in {'', '.'}:
            local_path_alias = ''
        aliases = [
            normalized_lower_text(text_from_attr(repository, 'id')),
            normalized_lower_text(text_from_attr(repository, 'display_name')),
            normalized_lower_text(text_from_attr(repository, 'repo_slug')),
            local_path_alias.lower(),
        ]
        for alias in getattr(repository, 'aliases', []) or []:
            aliases.append(normalized_lower_text(alias))
        return [alias for alias in aliases if alias]

    @staticmethod
    def _repository_tags(task) -> list[str]:
        raw_tags = getattr(task, 'tags', []) or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        repository_tags: list[str] = []
        for raw_tag in raw_tags:
            if isinstance(raw_tag, dict):
                tag_text = normalized_text(raw_tag.get('name', ''))
            else:
                tag_text = normalized_text(getattr(raw_tag, 'name', raw_tag))
            if not tag_text.lower().startswith(RepositoryFields.REPOSITORY_TAG_PREFIX):
                continue
            repository_tag = normalized_text(
                tag_text[len(RepositoryFields.REPOSITORY_TAG_PREFIX) :]
            )
            if repository_tag:
                repository_tags.append(repository_tag)
        return repository_tags

    @staticmethod
    def _validate_local_path(repository) -> None:
        local_path = text_from_attr(repository, 'local_path')
        if not local_path or not os.path.isdir(local_path):
            raise ValueError(
                f'missing local repository path for {repository.id}: {local_path or "<empty>"}'
            )

    @staticmethod
    def _validate_git_remote_auth(repository) -> None:
        remote_url = text_from_attr(repository, 'remote_url')
        if not RepositoryInventoryService._uses_ssh_remote(remote_url):
            return
    
        ssh_auth_sock = normalized_text(os.getenv('SSH_AUTH_SOCK', ''))
        if not ssh_auth_sock:
            raise ValueError(
                f'repository {repository.id} uses an SSH git remote but SSH_AUTH_SOCK is not configured'
            )
        if not os.path.exists(ssh_auth_sock):
            raise ValueError(
                f'repository {repository.id} uses an SSH git remote but SSH_AUTH_SOCK does not exist: '
                f'{ssh_auth_sock}'
            )

    @staticmethod
    def _uses_ssh_remote(remote_url: str) -> bool:
        normalized = normalized_lower_text(remote_url)
        return normalized.startswith('ssh://') or bool(re.match(r'^[^@]+@[^:]+:.+', normalized))

    def _prepare_repository_access(self, repository) -> None:
        self._validate_local_path(repository)
        self._validate_git_remote_auth(repository)
        self._prepare_repository_git_auth(repository)
        self._prepare_pull_request_api(repository)

    def _prepare_repository_git_auth(self, repository) -> None:
        if normalized_lower_text(text_from_attr(repository, 'provider')) != 'bitbucket':
            return
        username = self._resolved_bitbucket_username(repository)
        if username:
            setattr(repository, RepositoryFields.BITBUCKET_USERNAME, username)

    def _validate_repository_git_access(self, repository) -> None:
        local_path = text_from_attr(repository, 'local_path')
        try:
            self._run_git(
                local_path,
                ['ls-remote', '--heads', 'origin'],
                f'failed to validate git access for repository at {local_path}',
                repository,
            )
        except RuntimeError as exc:
            error_text = str(exc)
            error_detail = error_text.split(': ', 1)[1] if ': ' in error_text else error_text
            if (
                'could not read Password' in error_text
                or 'terminal prompts disabled' in error_text
            ):
                raise RuntimeError(
                    f'[Error] {local_path} missing git permissions. cannot work. '
                    f'{error_detail}'
                ) from None
            raise RuntimeError(
                f'[Error] {local_path} git validation failed. {error_detail}'
            ) from None

    def _prepare_pull_request_api(self, repository) -> None:
        provider = self._resolved_pull_request_provider(repository)
        provider_base_url, token = self._resolved_pull_request_api_values(
            repository,
            provider,
        )
        api_email = (
            self._resolved_bitbucket_api_email(repository) if provider == 'bitbucket' else ''
        )
        self._validate_pull_request_api_values(
            repository.id,
            provider,
            provider_base_url,
            token,
            api_email,
        )
        self._apply_pull_request_api_values(
            repository,
            provider,
            provider_base_url,
            token,
            api_email,
        )

    def _resolved_pull_request_provider(self, repository) -> str:
        provider = normalized_lower_text(text_from_attr(repository, 'provider'))
        if provider:
            return provider
        provider_base_url = text_from_attr(repository, RepositoryFields.PROVIDER_BASE_URL)
        provider = self._provider_from_base_url(provider_base_url)
        if provider:
            return provider
        provider = self._provider_from_remote_url(text_from_attr(repository, 'remote_url'))
        if provider:
            return provider
        raise ValueError(
            f'unable to determine pull request provider for repository {repository.id}'
        )

    def _resolved_pull_request_api_values(
        self,
        repository,
        provider: str,
    ) -> tuple[str, str]:
        defaults = self._provider_api_defaults.get(provider, {})
        provider_base_url = text_from_attr(repository, RepositoryFields.PROVIDER_BASE_URL)
        token = text_from_attr(repository, 'token')
        provider_base_url = provider_base_url or normalized_text(
            defaults.get(RepositoryFields.PROVIDER_BASE_URL, '')
        )
        token = token or normalized_text(defaults.get('token', ''))
        if provider_base_url:
            return provider_base_url, token
        return self._default_provider_base_url(
            provider,
            text_from_attr(repository, 'remote_url'),
        ), token

    def _resolved_bitbucket_username(self, repository) -> str:
        username = text_from_attr(repository, RepositoryFields.BITBUCKET_USERNAME) or text_from_attr(
            repository,
            'username',
        )
        if username:
            return username
        return normalized_text(self._provider_api_defaults.get('bitbucket', {}).get('username', ''))

    def _resolved_bitbucket_api_email(self, repository) -> str:
        api_email = text_from_attr(repository, RepositoryFields.BITBUCKET_API_EMAIL) or text_from_attr(
            repository,
            'api_email',
        )
        if api_email:
            return api_email
        return normalized_text(self._provider_api_defaults.get('bitbucket', {}).get('api_email', ''))

    def _validate_pull_request_api_values(
        self,
        repository_id: str,
        provider: str,
        provider_base_url: str,
        token: str,
        api_email: str = '',
    ) -> None:
        if not provider_base_url:
            raise ValueError(
                f'missing pull request API base URL for repository {repository_id}'
            )
        if token:
            if provider != 'bitbucket' or api_email:
                return
            raise ValueError(
                f'missing Bitbucket API email for repository {repository_id}'
            )
        raise ValueError(
            self._missing_pull_request_token_message(repository_id, provider)
        )

    @staticmethod
    def _apply_pull_request_api_values(
        repository,
        provider: str,
        provider_base_url: str,
        token: str,
        api_email: str = '',
    ) -> None:
        setattr(repository, 'provider', provider)
        setattr(repository, RepositoryFields.PROVIDER_BASE_URL, provider_base_url)
        setattr(repository, 'token', token)
        if provider == 'bitbucket':
            setattr(repository, RepositoryFields.BITBUCKET_API_EMAIL, api_email)

    def _pull_request_data_access(self, repository) -> PullRequestDataAccess:
        provider_base_url = text_from_attr(repository, RepositoryFields.PROVIDER_BASE_URL)
        owner = text_from_attr(repository, RepositoryFields.OWNER)
        repo_slug = text_from_attr(repository, RepositoryFields.REPO_SLUG)
        token = text_from_attr(repository, 'token')
        api_email = text_from_attr(repository, RepositoryFields.BITBUCKET_API_EMAIL)
        destination_branch = text_from_attr(repository, RepositoryFields.DESTINATION_BRANCH)
        if not provider_base_url or not owner or not repo_slug or not token:
            raise ValueError(
                f'incomplete pull request configuration for repository {repository.id}'
            )
        if provider_base_url and 'bitbucket' in provider_base_url.lower() and not api_email:
            raise ValueError(
                f'missing Bitbucket API email for repository {repository.id}'
            )
        config = OmegaConf.create(
            {
                'base_url': provider_base_url,
                'token': token,
                'owner': owner,
                'repo_slug': repo_slug,
                'api_email': api_email,
                RepositoryFields.DESTINATION_BRANCH: destination_branch,
            }
        )
        client = build_pull_request_client(config, self._max_retries)
        return PullRequestDataAccess(config, client)

    def _review_url(self, repository, source_branch: str, destination_branch: str) -> str:
        remote_url = text_from_attr(repository, 'remote_url')
        provider = text_from_attr(repository, 'provider')
        owner = text_from_attr(repository, 'owner')
        repo_slug = text_from_attr(repository, 'repo_slug')

        if remote_url and provider and owner and repo_slug:
            return review_url_for_remote(
                remote_url=remote_url,
                provider=provider,
                owner=owner,
                repo_slug=repo_slug,
                source_branch=source_branch,
                destination_branch=destination_branch,
            )

        web_base_url = self._fallback_web_base_url(repository)
        if not web_base_url or not owner or not repo_slug:
            return ''
        provider = provider or self._provider_from_base_url(
            text_from_attr(repository, 'provider_base_url')
        )
        if provider:
            return review_url_for_remote(
                remote_url=f'{web_base_url}/{owner}/{repo_slug}.git',
                provider=provider,
                owner=owner,
                repo_slug=repo_slug,
                source_branch=source_branch,
                destination_branch=destination_branch,
            )
        repository_path = f'{owner}/{repo_slug}'.strip('/')
        return f'{web_base_url}/{repository_path}'

    @staticmethod
    def _fallback_web_base_url(repository) -> str:
        remote_url = text_from_attr(repository, 'remote_url')
        if remote_url:
            return remote_web_base_url(remote_url)
        provider_base_url = text_from_attr(repository, 'provider_base_url')
        if not provider_base_url:
            return ''
        if 'api.bitbucket.org' in provider_base_url:
            return 'https://bitbucket.org'
        if provider_base_url.rstrip('/').endswith('/api/v4'):
            return provider_base_url[: -len('/api/v4')]
        if provider_base_url.rstrip('/').endswith('/api/v3'):
            return provider_base_url[: -len('/api/v3')]
        if provider_base_url.rstrip('/').endswith('/api'):
            return provider_base_url[: -len('/api')]
        return provider_base_url

    @staticmethod
    def _provider_from_base_url(provider_base_url: str) -> str:
        normalized = provider_base_url.lower()
        if 'bitbucket' in normalized:
            return 'bitbucket'
        if 'github' in normalized:
            return 'github'
        if 'gitlab' in normalized:
            return 'gitlab'
        return ''

    @staticmethod
    def _provider_from_remote_url(remote_url: str) -> str:
        normalized = remote_url.lower()
        if 'bitbucket' in normalized:
            return 'bitbucket'
        if 'github' in normalized:
            return 'github'
        if 'gitlab' in normalized:
            return 'gitlab'
        return ''

    @staticmethod
    def _default_provider_base_url(provider: str, remote_url: str) -> str:
        web_base_url = remote_web_base_url(remote_url)
        if not web_base_url:
            return ''
        host = str(urlparse(web_base_url).hostname or '').lower()
        if provider == 'github':
            if host == 'github.com':
                return 'https://api.github.com'
            return f'{web_base_url}/api/v3'
        if provider == 'gitlab':
            return f'{web_base_url}/api/v4'
        if provider == 'bitbucket' and host == 'bitbucket.org':
            return 'https://api.bitbucket.org/2.0'
        return ''

    @staticmethod
    def _missing_pull_request_token_message(repository_id: str, provider: str) -> str:
        env_key = {
            'github': 'GITHUB_API_TOKEN',
            'gitlab': 'GITLAB_API_TOKEN',
            'bitbucket': 'BITBUCKET_API_TOKEN',
        }.get(provider, '<provider-token>')
        return (
            f'missing pull request API token for repository {repository_id}; '
            f'set {env_key} or configure repository token explicitly'
        )
