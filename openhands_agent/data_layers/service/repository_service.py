import logging
import os
import re
import subprocess
from types import SimpleNamespace

from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import PullRequestFields
from openhands_agent.repository_discovery import (
    discover_git_repositories,
    display_name_from_repo_slug,
    remote_web_base_url,
    repository_id_from_name,
    review_url_for_remote,
)


class RepositoryService(Service):
    def __init__(self, repositories_config, max_retries: int) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._max_retries = max_retries
        self._repositories = self._load_repositories(repositories_config)

    @property
    def repositories(self) -> list[object]:
        return list(self._repositories)

    def validate_connections(self) -> None:
        self._validate_inventory()
        for repository in self._repositories:
            self._validate_local_path(repository)

    def resolve_task_repositories(self, task: Task) -> list[object]:
        searchable_text = f'{task.summary}\n{task.description}'.lower()
        matches = [
            repository
            for repository in self._repositories
            if self._repository_matches(searchable_text, repository)
        ]
        if not matches:
            raise ValueError(f'no configured repository matched task {task.id}')
        return matches

    def prepare_task_repositories(self, repositories: list[object]) -> list[object]:
        prepared_repositories: list[object] = []
        for repository in repositories:
            self._validate_local_path(repository)
            setattr(repository, 'destination_branch', self.destination_branch(repository))
            prepared_repositories.append(repository)
        return prepared_repositories

    def get_repository(self, repository_id: str):
        for repository in self._repositories:
            if repository.id == repository_id:
                return repository
        raise ValueError(f'unknown repository id: {repository_id}')

    def build_branch_name(self, task: Task, repository) -> str:
        return str(task.id or '').strip()

    def create_pull_request(
        self,
        repository,
        title: str,
        source_branch: str,
        description: str = '',
    ) -> dict[str, str]:
        self._validate_local_path(repository)
        destination_branch = self.destination_branch(repository)
        self._push_branch(repository.local_path, source_branch)
        return {
            PullRequestFields.REPOSITORY_ID: repository.id,
            PullRequestFields.ID: source_branch,
            PullRequestFields.TITLE: title,
            PullRequestFields.URL: self._review_url(repository, source_branch, destination_branch),
            PullRequestFields.SOURCE_BRANCH: source_branch,
            PullRequestFields.DESTINATION_BRANCH: destination_branch,
            PullRequestFields.DESCRIPTION: description,
        }

    def list_pull_request_comments(
        self,
        repository,
        pull_request_id: str,
    ) -> list[dict[str, str]]:
        self.logger.info(
            'skipping pull request comment polling for repository %s; '
            'review comments now require direct payload handling',
            repository.id,
        )
        return []

    def destination_branch(self, repository) -> str:
        configured_branch = str(getattr(repository, 'destination_branch', '') or '').strip()
        if configured_branch:
            return configured_branch
        self._validate_local_path(repository)
        inferred_branch = self._infer_default_branch(repository.local_path)
        if not inferred_branch:
            raise ValueError(
                f'unable to determine destination branch for repository {repository.id}'
            )
        return inferred_branch

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
            discovered_repositories = self._discover_repositories_from_root(repository_source)
            if discovered_repositories:
                return discovered_repositories
            return []
        return self._normalized_repositories(repository_source)

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
        root_path = str(getattr(repository_source, 'repository_root_path', '') or '').strip()
        if not root_path:
            return []
        ignored_folders = self._ignored_repository_folders(repository_source)

        repositories: list[object] = []
        for discovered_repository in discover_git_repositories(root_path, ignored_folders):
            local_path = str(discovered_repository.local_path).strip()
            folder_name = os.path.basename(local_path)
            repo_slug = str(discovered_repository.repo_slug or folder_name).strip()
            repository_name = folder_name or repo_slug
            aliases = [folder_name, repo_slug]
            repositories.append(
                SimpleNamespace(
                    id=repository_id_from_name(repository_name),
                    display_name=display_name_from_repo_slug(repository_name),
                    local_path=local_path,
                    provider=str(discovered_repository.provider or '').strip(),
                    remote_url=str(discovered_repository.remote_url or '').strip(),
                    owner=str(discovered_repository.owner or '').strip(),
                    repo_slug=repo_slug,
                    aliases=[alias for alias in aliases if alias],
                )
            )
        return repositories

    @staticmethod
    def _ignored_repository_folders(repository_source) -> list[str]:
        ignored_folders = getattr(repository_source, 'ignored_repository_folders', [])
        if isinstance(ignored_folders, str):
            return [
                folder.strip()
                for folder in ignored_folders.split(',')
                if folder.strip()
            ]
        if not ignored_folders:
            return []
        return [
            str(folder).strip()
            for folder in ignored_folders
            if str(folder).strip()
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
        local_path_alias = os.path.basename(str(getattr(repository, 'local_path', '') or '').strip())
        if local_path_alias in {'', '.'}:
            local_path_alias = ''
        aliases = [
            str(getattr(repository, 'id', '') or '').strip().lower(),
            str(getattr(repository, 'display_name', '') or '').strip().lower(),
            str(getattr(repository, 'repo_slug', '') or '').strip().lower(),
            local_path_alias.lower(),
        ]
        for alias in getattr(repository, 'aliases', []) or []:
            aliases.append(str(alias).strip().lower())
        return [alias for alias in aliases if alias]

    @staticmethod
    def _validate_local_path(repository) -> None:
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path or not os.path.isdir(local_path):
            raise ValueError(
                f'missing local repository path for {repository.id}: {local_path or "<empty>"}'
            )

    def _push_branch(self, local_path: str, branch_name: str) -> None:
        result = subprocess.run(
            ['git', '-C', local_path, 'push', '-u', 'origin', branch_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        raise RuntimeError(
            f'failed to push branch {branch_name}: '
            f'{result.stderr.strip() or result.stdout.strip() or "git push failed"}'
        )

    def _review_url(self, repository, source_branch: str, destination_branch: str) -> str:
        remote_url = str(getattr(repository, 'remote_url', '') or '').strip()
        provider = str(getattr(repository, 'provider', '') or '').strip()
        owner = str(getattr(repository, 'owner', '') or '').strip()
        repo_slug = str(getattr(repository, 'repo_slug', '') or '').strip()

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
            str(getattr(repository, 'provider_base_url', '') or '').strip()
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
        remote_url = str(getattr(repository, 'remote_url', '') or '').strip()
        if remote_url:
            return remote_web_base_url(remote_url)
        provider_base_url = str(getattr(repository, 'provider_base_url', '') or '').strip()
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
    def _infer_default_branch(local_path: str) -> str:
        commands = [
            ['git', '-C', local_path, 'symbolic-ref', 'refs/remotes/origin/HEAD'],
            ['git', '-C', local_path, 'branch', '--show-current'],
        ]
        for command in commands:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout.strip()
            if result.returncode != 0 or not output:
                continue
            if output.startswith('refs/remotes/'):
                return output.rsplit('/', 1)[-1]
            return output
        return ''
