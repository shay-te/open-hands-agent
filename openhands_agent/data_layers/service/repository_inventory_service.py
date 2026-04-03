import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from core_lib.data_layers.service.service import Service
from omegaconf import OmegaConf

from openhands_agent.client.bitbucket_auth import basic_auth_header
from openhands_agent.data_layers.data.fields import RepositoryFields
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
    _GENERIC_DISCOVERED_FOLDER_NAMES = {
        'project',
        'projects',
        'repo',
        'repos',
        'repository',
        'workspace',
    }

    def __init__(self, repositories_config) -> None:
        self.logger = configure_logger(self.__class__.__name__)
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
        searchable_text = f'{task.summary}\n{task.description}'.lower()
        matches = [
            repository
            for repository in self._repositories
            if self._repository_matches(searchable_text, repository)
        ]
        if not matches:
            raise ValueError(f'no configured repository matched task {task.id}')
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
