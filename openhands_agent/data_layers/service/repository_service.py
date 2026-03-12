import logging
import os
import re
import subprocess
from types import SimpleNamespace

from omegaconf import DictConfig

from openhands_agent.client.pull_request_client_factory import build_pull_request_client
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.fields import PullRequestFields


class RepositoryService:
    def __init__(self, repositories_config, max_retries: int) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._max_retries = max_retries
        self._repositories = self._normalized_repositories(repositories_config)
        self._data_access_by_id: dict[str, PullRequestDataAccess] = {}
        self._validate_inventory()

    @property
    def repositories(self) -> list[object]:
        return list(self._repositories)

    def validate_connections(self) -> None:
        for repository in self._repositories:
            self._validate_local_path(repository)
            self._data_access(repository).validate_connection()

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

    def get_repository(self, repository_id: str):
        for repository in self._repositories:
            if repository.id == repository_id:
                return repository
        raise ValueError(f'unknown repository id: {repository_id}')

    def build_branch_name(self, task: Task, repository) -> str:
        return f'feature/{task.id.lower()}/{repository.id.lower()}'

    def create_pull_request(
        self,
        repository,
        title: str,
        source_branch: str,
        description: str = '',
    ) -> dict[str, str]:
        pull_request = self._data_access(repository).create_pull_request(
            title=title,
            source_branch=source_branch,
            destination_branch=self.destination_branch(repository),
            description=description,
        )
        return {
            'repository_id': repository.id,
            PullRequestFields.ID: pull_request[PullRequestFields.ID],
            PullRequestFields.TITLE: pull_request[PullRequestFields.TITLE],
            PullRequestFields.URL: pull_request[PullRequestFields.URL],
            PullRequestFields.SOURCE_BRANCH: source_branch,
            PullRequestFields.DESTINATION_BRANCH: self.destination_branch(repository),
        }

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

    def _data_access(self, repository) -> PullRequestDataAccess:
        if repository.id not in self._data_access_by_id:
            client = build_pull_request_client(
                SimpleNamespace(
                    base_url=repository.provider_base_url,
                    token=repository.token,
                ),
                self._max_retries,
            )
            self._data_access_by_id[repository.id] = PullRequestDataAccess(repository, client)
        return self._data_access_by_id[repository.id]

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

    def _repository_matches(self, searchable_text: str, repository) -> bool:
        return any(
            self._keyword_matches(searchable_text, keyword)
            for keyword in self._repository_aliases(repository)
        )

    @staticmethod
    def _keyword_matches(searchable_text: str, keyword: str) -> bool:
        if not keyword:
            return False
        pattern = rf'(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])'
        return re.search(pattern, searchable_text) is not None

    @staticmethod
    def _repository_aliases(repository) -> list[str]:
        aliases = [
            str(getattr(repository, 'id', '') or '').strip().lower(),
            str(getattr(repository, 'display_name', '') or '').strip().lower(),
            str(getattr(repository, 'repo_slug', '') or '').strip().lower(),
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
