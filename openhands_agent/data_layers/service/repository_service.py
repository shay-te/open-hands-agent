import os
import re
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import urlparse

from core_lib.data_layers.service.service import Service
from omegaconf import OmegaConf

from openhands_agent.client.pull_request_client_factory import build_pull_request_client
from openhands_agent.data_layers.data_access.pull_request_data_access import PullRequestDataAccess
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import PullRequestFields, RepositoryFields
from openhands_agent.logging_utils import configure_logger
from openhands_agent.repository_discovery import (
    discover_git_repositories,
    display_name_from_repo_slug,
    remote_web_base_url,
    repository_id_from_name,
    review_url_for_remote,
)


class RepositoryService(Service):
    def __init__(self, repositories_config, max_retries: int) -> None:
        self.logger = configure_logger(self.__class__.__name__)
        self._max_retries = max_retries
        self._provider_api_defaults = self._provider_api_defaults_from_source(repositories_config)
        self._repositories = self._load_repositories(repositories_config)

    @property
    def repositories(self) -> list[object]:
        return list(self._repositories)

    def validate_connections(self) -> None:
        self._validate_inventory()
        self._validate_git_executable()
        for repository in self._repositories:
            self._validate_local_path(repository)
            self._prepare_pull_request_api(repository)

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
        self._validate_git_executable()
        prepared_repositories: list[object] = []
        for repository in repositories:
            self._validate_local_path(repository)
            self._prepare_pull_request_api(repository)
            setattr(repository, 'destination_branch', self.destination_branch(repository))
            self._prepare_workspace_for_task(
                repository.local_path,
                repository.destination_branch,
            )
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
        commit_message: str = '',
    ) -> dict[str, str]:
        self._validate_local_path(repository)
        self._prepare_pull_request_api(repository)
        destination_branch = self.destination_branch(repository)
        final_commit_message = str(commit_message or '').strip() or f'Implement {source_branch}'
        self._publish_branch_updates(
            repository.local_path,
            source_branch,
            destination_branch,
            final_commit_message,
        )
        pull_request = self._pull_request_data_access(repository).create_pull_request(
            title=title,
            source_branch=source_branch,
            destination_branch=destination_branch,
            description=description,
        )
        return {
            PullRequestFields.REPOSITORY_ID: repository.id,
            PullRequestFields.ID: str(pull_request.get(PullRequestFields.ID, '') or ''),
            PullRequestFields.TITLE: str(
                pull_request.get(PullRequestFields.TITLE, '') or title
            ),
            PullRequestFields.URL: str(
                pull_request.get(PullRequestFields.URL, '')
                or self._review_url(repository, source_branch, destination_branch)
            ),
            PullRequestFields.SOURCE_BRANCH: source_branch,
            PullRequestFields.DESTINATION_BRANCH: destination_branch,
            PullRequestFields.DESCRIPTION: description,
        }

    def publish_review_fix(
        self,
        repository,
        branch_name: str,
        commit_message: str = '',
    ) -> None:
        self._validate_local_path(repository)
        destination_branch = self.destination_branch(repository)
        final_commit_message = str(commit_message or '').strip() or 'Address review comments'
        self._publish_branch_updates(
            repository.local_path,
            branch_name,
            destination_branch,
            final_commit_message,
        )

    def list_pull_request_comments(
        self,
        repository,
        pull_request_id: str,
    ) -> list[dict[str, str]]:
        try:
            self._prepare_pull_request_api(repository)
        except Exception as exc:
            self.logger.info(
                'skipping pull request comment polling for repository %s: %s',
                repository.id,
                exc,
            )
            return []
        return self._pull_request_data_access(repository).list_pull_request_comments(
            pull_request_id
        )

    def resolve_review_comment(self, repository, comment) -> None:
        self._prepare_pull_request_api(repository)
        self._pull_request_data_access(repository).resolve_review_comment(comment)

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
    def _provider_api_defaults_from_source(repository_source) -> dict[str, dict[str, str]]:
        def provider_values(attribute: str) -> dict[str, str]:
            provider_cfg = getattr(repository_source, attribute, None)
            return {
                RepositoryFields.PROVIDER_BASE_URL: str(
                    getattr(provider_cfg, 'base_url', '') or ''
                ).strip(),
                'token': str(getattr(provider_cfg, 'token', '') or '').strip(),
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

    def _prepare_pull_request_api(self, repository) -> None:
        provider = str(getattr(repository, 'provider', '') or '').strip().lower()
        provider_base_url = str(
            getattr(repository, RepositoryFields.PROVIDER_BASE_URL, '') or ''
        ).strip()
        token = str(getattr(repository, 'token', '') or '').strip()

        if not provider:
            provider = self._provider_from_base_url(provider_base_url)
        if not provider:
            provider = self._provider_from_remote_url(
                str(getattr(repository, 'remote_url', '') or '').strip()
            )
        if not provider:
            raise ValueError(
                f'unable to determine pull request provider for repository {repository.id}'
            )

        defaults = self._provider_api_defaults.get(provider, {})
        provider_base_url = provider_base_url or str(
            defaults.get(RepositoryFields.PROVIDER_BASE_URL, '') or ''
        ).strip()
        token = token or str(defaults.get('token', '') or '').strip()

        if not provider_base_url:
            provider_base_url = self._default_provider_base_url(
                provider,
                str(getattr(repository, 'remote_url', '') or '').strip(),
            )
        if not provider_base_url:
            raise ValueError(
                f'missing pull request API base URL for repository {repository.id}'
            )
        if not token:
            raise ValueError(
                self._missing_pull_request_token_message(repository.id, provider)
            )

        setattr(repository, 'provider', provider)
        setattr(repository, RepositoryFields.PROVIDER_BASE_URL, provider_base_url)
        setattr(repository, 'token', token)

    def _pull_request_data_access(self, repository) -> PullRequestDataAccess:
        provider_base_url = str(
            getattr(repository, RepositoryFields.PROVIDER_BASE_URL, '') or ''
        ).strip()
        owner = str(getattr(repository, RepositoryFields.OWNER, '') or '').strip()
        repo_slug = str(getattr(repository, RepositoryFields.REPO_SLUG, '') or '').strip()
        token = str(getattr(repository, 'token', '') or '').strip()
        destination_branch = str(getattr(repository, RepositoryFields.DESTINATION_BRANCH, '') or '').strip()
        if not provider_base_url or not owner or not repo_slug or not token:
            raise ValueError(
                f'incomplete pull request configuration for repository {repository.id}'
            )
        config = OmegaConf.create(
            {
                'base_url': provider_base_url,
                'token': token,
                'owner': owner,
                'repo_slug': repo_slug,
                RepositoryFields.DESTINATION_BRANCH: destination_branch,
            }
        )
        client = build_pull_request_client(config, self._max_retries)
        return PullRequestDataAccess(config, client)

    @staticmethod
    def _validate_git_executable() -> None:
        if shutil.which('git'):
            return
        raise RuntimeError('git executable is required but was not found on PATH')

    def _prepare_branch_for_publication(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
        commit_message: str,
    ) -> None:
        current_branch = self._current_branch(local_path)
        if current_branch != branch_name:
            raise RuntimeError(
                f'expected repository at {local_path} to be on branch {branch_name}, '
                f'but found {current_branch or "<unknown>"}'
            )

        status_output = self._working_tree_status(local_path)
        if status_output:
            self._run_git(
                local_path,
                ['add', '-A'],
                f'failed to stage changes for branch {branch_name}',
            )
            self._run_git(
                local_path,
                ['commit', '-m', commit_message],
                f'failed to commit changes for branch {branch_name}',
            )

        comparison_ref = self._comparison_reference(local_path, destination_branch)
        ahead_count_text = self._git_stdout(
            local_path,
            ['rev-list', '--count', f'{comparison_ref}..{branch_name}'],
            f'failed to compare branch {branch_name} against {comparison_ref}',
        )
        try:
            ahead_count = int(ahead_count_text or '0')
        except ValueError as exc:
            raise RuntimeError(
                f'failed to parse ahead count for branch {branch_name}: '
                f'{ahead_count_text or "<empty>"}'
            ) from exc
        if ahead_count < 1:
            raise RuntimeError(
                f'branch {branch_name} has no committed changes ahead of {comparison_ref}'
            )

    def _publish_branch_updates(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
        commit_message: str,
    ) -> None:
        try:
            self._prepare_branch_for_publication(
                local_path,
                branch_name,
                destination_branch,
                commit_message,
            )
            self._push_branch(local_path, branch_name)
        finally:
            self._prepare_workspace_for_task(local_path, destination_branch)

    def _prepare_workspace_for_task(
        self,
        local_path: str,
        destination_branch: str,
    ) -> None:
        current_branch = self._current_branch(local_path)
        status_output = self._working_tree_status(local_path)
        if status_output:
            raise RuntimeError(
                f'repository at {local_path} has uncommitted changes on branch '
                f'{current_branch or "<unknown>"}; refusing to start a new task'
            )
        if current_branch and current_branch != destination_branch:
            self._run_git(
                local_path,
                ['checkout', destination_branch],
                f'failed to switch repository at {local_path} to {destination_branch}',
            )
            current_branch = self._current_branch(local_path)
        if current_branch != destination_branch:
            raise RuntimeError(
                f'repository at {local_path} is on branch '
                f'{current_branch or "<unknown>"} instead of {destination_branch}'
            )
        self._validate_destination_branch_tracking_state(local_path, destination_branch)

    def _validate_destination_branch_tracking_state(
        self,
        local_path: str,
        destination_branch: str,
    ) -> None:
        remote_reference = f'origin/{destination_branch}'
        if not self._git_reference_exists(local_path, remote_reference):
            return
        ahead_count, _ = self._left_right_commit_counts(
            local_path,
            destination_branch,
            remote_reference,
        )
        if ahead_count > 0:
            raise RuntimeError(
                f'destination branch {destination_branch} at {local_path} has '
                f'{ahead_count} local commit(s) not on {remote_reference}; '
                'refusing to start a new task'
            )

    def _comparison_reference(self, local_path: str, destination_branch: str) -> str:
        for reference in (destination_branch, f'origin/{destination_branch}'):
            if self._git_reference_exists(local_path, reference):
                return reference
        raise RuntimeError(
            f'destination branch {destination_branch} is not available locally'
        )

    def _current_branch(self, local_path: str) -> str:
        return self._git_stdout(
            local_path,
            ['rev-parse', '--abbrev-ref', 'HEAD'],
            f'failed to determine current branch for {local_path}',
        )

    def _working_tree_status(self, local_path: str) -> str:
        return self._git_stdout(
            local_path,
            ['status', '--porcelain'],
            f'failed to inspect working tree for repository at {local_path}',
        )

    def _git_reference_exists(self, local_path: str, reference: str) -> bool:
        result = subprocess.run(
            ['git', '-C', local_path, 'rev-parse', '--verify', reference],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _left_right_commit_counts(
        self,
        local_path: str,
        left_reference: str,
        right_reference: str,
    ) -> tuple[int, int]:
        counts_text = self._git_stdout(
            local_path,
            ['rev-list', '--left-right', '--count', f'{left_reference}...{right_reference}'],
            f'failed to compare {left_reference} against {right_reference}',
        )
        parts = counts_text.split()
        if len(parts) != 2:
            raise RuntimeError(
                f'failed to parse commit counts for {left_reference}...{right_reference}: '
                f'{counts_text or "<empty>"}'
            )
        try:
            return int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise RuntimeError(
                f'failed to parse commit counts for {left_reference}...{right_reference}: '
                f'{counts_text or "<empty>"}'
            ) from exc

    def _git_stdout(self, local_path: str, args: list[str], failure_message: str) -> str:
        result = self._run_git(local_path, args, failure_message)
        return result.stdout.strip()

    def _run_git(self, local_path: str, args: list[str], failure_message: str):
        self._validate_git_executable()
        result = subprocess.run(
            ['git', '-C', local_path, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result
        raise RuntimeError(
            f'{failure_message}: '
            f'{result.stderr.strip() or result.stdout.strip() or "git command failed"}'
        )

    def _push_branch(self, local_path: str, branch_name: str) -> None:
        self._run_git(
            local_path,
            ['push', '-u', 'origin', branch_name],
            f'failed to push branch {branch_name}',
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

    @staticmethod
    def _infer_default_branch(local_path: str) -> str:
        RepositoryService._validate_git_executable()
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
