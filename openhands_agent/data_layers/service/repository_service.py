import base64
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
from openhands_agent.text_utils import (
    normalized_lower_text,
    normalized_text,
    text_from_attr,
)


class RepositoryService(Service):
    _GENERIC_DISCOVERED_FOLDER_NAMES = {
        'project',
        'projects',
        'repo',
        'repos',
        'repository',
        'workspace',
    }

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
            self._prepare_repository_access(repository)

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
        return [
            self._prepare_task_repository(repository)
            for repository in repositories
        ]

    def restore_task_repositories(self, repositories: list[object]) -> list[object]:
        self._validate_git_executable()
        for repository in repositories:
            self._restore_task_repository(repository)
        return repositories

    def prepare_task_branches(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> list[object]:
        self._validate_git_executable()
        for repository in repositories:
            branch_name = normalized_text(repository_branches.get(repository.id, ''))
            if not branch_name:
                raise ValueError(
                    f'missing task branch name for repository {repository.id}'
                )
            self._prepare_task_branch(repository, branch_name)
        return repositories

    def get_repository(self, repository_id: str):
        for repository in self._repositories:
            if repository.id == repository_id:
                return repository
        raise ValueError(f'unknown repository id: {repository_id}')

    def build_branch_name(self, task: Task, repository) -> str:
        return normalized_text(task.id)

    def create_pull_request(
        self,
        repository,
        title: str,
        source_branch: str,
        description: str = '',
        commit_message: str = '',
    ) -> dict[str, str]:
        self._prepare_pull_request_api(repository)
        destination_branch = self._publish_repository_branch(
            repository,
            source_branch,
            commit_message=commit_message,
            default_commit_message=f'Implement {source_branch}',
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
        self._publish_repository_branch(
            repository,
            branch_name,
            commit_message=commit_message,
            default_commit_message='Address review comments',
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
        configured_branch = text_from_attr(repository, 'destination_branch')
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
                RepositoryFields.PROVIDER_BASE_URL: text_from_attr(provider_cfg, 'base_url'),
                'token': text_from_attr(provider_cfg, 'token'),
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
        if not RepositoryService._uses_ssh_remote(remote_url):
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
        self._prepare_pull_request_api(repository)

    def _prepare_task_repository(self, repository):
        self._prepare_repository_access(repository)
        setattr(repository, 'destination_branch', self.destination_branch(repository))
        self._prepare_workspace_for_task(
            repository.local_path,
            repository.destination_branch,
            repository,
        )
        return repository

    def _restore_task_repository(self, repository) -> None:
        self._validate_local_path(repository)
        destination_branch = text_from_attr(repository, 'destination_branch') or self.destination_branch(
            repository
        )
        current_branch = self._current_branch(repository.local_path)
        if current_branch == destination_branch:
            return
        if self._working_tree_status(repository.local_path):
            self.logger.warning(
                'skipping repository restore for %s because the worktree is dirty on branch %s',
                repository.id,
                current_branch or '<unknown>',
            )
            return
        try:
            self._run_git(
                repository.local_path,
                ['checkout', destination_branch],
                f'failed to restore repository at {repository.local_path} to {destination_branch}',
                repository,
            )
            self.logger.info(
                'restored repository at %s to branch %s after task rejection',
                repository.local_path,
                destination_branch,
            )
        except Exception as exc:
            self.logger.warning(
                'failed to restore repository %s to %s after task rejection: %s',
                repository.id,
                destination_branch,
                exc,
            )

    def _prepare_task_branch(self, repository, branch_name: str):
        self._validate_local_path(repository)
        destination_branch = text_from_attr(
            repository,
            'destination_branch',
        ) or self.destination_branch(repository)
        setattr(repository, 'destination_branch', destination_branch)
        self._prepare_workspace_for_branch(
            repository.local_path,
            destination_branch,
            branch_name,
        )
        return repository

    def _prepare_pull_request_api(self, repository) -> None:
        provider = self._resolved_pull_request_provider(repository)
        provider_base_url, token = self._resolved_pull_request_api_values(
            repository,
            provider,
        )
        self._validate_pull_request_api_values(
            repository.id,
            provider,
            provider_base_url,
            token,
        )
        self._apply_pull_request_api_values(
            repository,
            provider,
            provider_base_url,
            token,
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

    def _validate_pull_request_api_values(
        self,
        repository_id: str,
        provider: str,
        provider_base_url: str,
        token: str,
    ) -> None:
        if not provider_base_url:
            raise ValueError(
                f'missing pull request API base URL for repository {repository_id}'
            )
        if token:
            return
        raise ValueError(
            self._missing_pull_request_token_message(repository_id, provider)
        )

    @staticmethod
    def _apply_pull_request_api_values(
        repository,
        provider: str,
        provider_base_url: str,
        token: str,
    ) -> None:
        setattr(repository, 'provider', provider)
        setattr(repository, RepositoryFields.PROVIDER_BASE_URL, provider_base_url)
        setattr(repository, 'token', token)

    def _publish_repository_branch(
        self,
        repository,
        branch_name: str,
        *,
        commit_message: str,
        default_commit_message: str,
    ) -> str:
        self._validate_local_path(repository)
        destination_branch = self.destination_branch(repository)
        self._publish_branch_updates(
            repository.local_path,
            branch_name,
            destination_branch,
            normalized_text(commit_message) or default_commit_message,
            repository,
        )
        return destination_branch

    def _pull_request_data_access(self, repository) -> PullRequestDataAccess:
        provider_base_url = text_from_attr(repository, RepositoryFields.PROVIDER_BASE_URL)
        owner = text_from_attr(repository, RepositoryFields.OWNER)
        repo_slug = text_from_attr(repository, RepositoryFields.REPO_SLUG)
        token = text_from_attr(repository, 'token')
        destination_branch = text_from_attr(repository, RepositoryFields.DESTINATION_BRANCH)
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
        self._assert_branch_checked_out(local_path, branch_name)
        self._commit_branch_changes_if_needed(local_path, branch_name, commit_message)
        self._ensure_branch_is_publishable(
            local_path,
            branch_name,
            destination_branch,
        )

    def _assert_branch_checked_out(self, local_path: str, branch_name: str) -> None:
        current_branch = self._current_branch(local_path)
        if current_branch == branch_name:
            return
        raise RuntimeError(
            f'expected repository at {local_path} to be on branch {branch_name}, '
            f'but found {current_branch or "<unknown>"}'
        )

    def _commit_branch_changes_if_needed(
        self,
        local_path: str,
        branch_name: str,
        commit_message: str,
    ) -> None:
        if not self._working_tree_status(local_path):
            return
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

    def _ensure_branch_is_publishable(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
    ) -> None:
        comparison_ref = self._comparison_reference(local_path, destination_branch)
        ahead_count = self._ahead_count(local_path, comparison_ref, branch_name)
        if ahead_count >= 1:
            return
        raise RuntimeError(
            f'branch {branch_name} has no committed changes ahead of {comparison_ref}'
        )

    def _ahead_count(
        self,
        local_path: str,
        comparison_ref: str,
        branch_name: str,
    ) -> int:
        ahead_count_text = self._git_stdout(
            local_path,
            ['rev-list', '--count', f'{comparison_ref}..{branch_name}'],
            f'failed to compare branch {branch_name} against {comparison_ref}',
        )
        try:
            return int(ahead_count_text or '0')
        except ValueError as exc:
            raise RuntimeError(
                f'failed to parse ahead count for branch {branch_name}: '
                f'{ahead_count_text or "<empty>"}'
            ) from exc

    def _publish_branch_updates(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
        commit_message: str,
        repository=None,
    ) -> None:
        try:
            self._prepare_branch_for_publication(
                local_path,
                branch_name,
                destination_branch,
                commit_message,
            )
            self._push_branch(local_path, branch_name, repository)
        finally:
            self._prepare_workspace_for_task(local_path, destination_branch, repository)

    def _prepare_workspace_for_task(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> None:
        current_branch = self._current_branch(local_path)
        self._ensure_clean_worktree(local_path, current_branch)
        current_branch = self._ensure_destination_branch_checked_out(
            local_path,
            destination_branch,
            current_branch,
        )
        self._validate_destination_branch_tracking_state(local_path, destination_branch)
        self._pull_destination_branch(local_path, destination_branch, repository)
        current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)

    def _prepare_workspace_for_branch(
        self,
        local_path: str,
        destination_branch: str,
        branch_name: str,
    ) -> None:
        current_branch = self._current_branch(local_path)
        self._ensure_clean_worktree(local_path, current_branch)
        self._validate_destination_branch_tracking_state(local_path, destination_branch)
        current_branch = self._ensure_task_branch_checked_out(
            local_path,
            destination_branch,
            branch_name,
            current_branch,
        )
        self._assert_current_branch(local_path, branch_name, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)

    def _ensure_clean_worktree(self, local_path: str, current_branch: str = '') -> None:
        status_output = self._working_tree_status(local_path)
        if not status_output:
            return
        raise RuntimeError(
            f'repository at {local_path} has uncommitted changes on branch '
            f'{current_branch or "<unknown>"}; refusing to start a new task'
        )

    def _ensure_destination_branch_checked_out(
        self,
        local_path: str,
        destination_branch: str,
        current_branch: str,
    ) -> str:
        if current_branch and current_branch != destination_branch:
            self._run_git(
                local_path,
                ['checkout', destination_branch],
                f'failed to switch repository at {local_path} to {destination_branch}',
            )
            current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        return current_branch

    def _ensure_task_branch_checked_out(
        self,
        local_path: str,
        destination_branch: str,
        branch_name: str,
        current_branch: str,
    ) -> str:
        if current_branch == branch_name:
            return current_branch
        restored_branch = self._checkout_existing_task_branch(local_path, branch_name)
        if restored_branch:
            return restored_branch
        current_branch = self._ensure_destination_branch_checked_out(
            local_path,
            destination_branch,
            current_branch,
        )
        self._create_task_branch(local_path, branch_name, destination_branch)
        return self._current_branch(local_path)

    def _checkout_existing_task_branch(
        self,
        local_path: str,
        branch_name: str,
    ) -> str:
        local_branch_ref = f'refs/heads/{branch_name}'
        remote_branch_ref = f'refs/remotes/origin/{branch_name}'
        if self._git_reference_exists(local_path, local_branch_ref):
            self._run_git(
                local_path,
                ['checkout', branch_name],
                f'failed to switch repository at {local_path} to {branch_name}',
            )
            return self._current_branch(local_path)
        if not self._git_reference_exists(local_path, remote_branch_ref):
            return ''
        self._run_git(
            local_path,
            ['checkout', '-b', branch_name, f'origin/{branch_name}'],
            f'failed to restore branch {branch_name} from origin/{branch_name}',
        )
        return self._current_branch(local_path)

    def _create_task_branch(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
    ) -> None:
        self._run_git(
            local_path,
            ['checkout', '-b', branch_name],
            f'failed to create branch {branch_name} from {destination_branch}',
        )

    @staticmethod
    def _assert_current_branch(
        local_path: str,
        destination_branch: str,
        current_branch: str,
    ) -> None:
        if current_branch == destination_branch:
            return
        raise RuntimeError(
            f'repository at {local_path} is on branch '
            f'{current_branch or "<unknown>"} instead of {destination_branch}'
        )

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

    def _git_stdout(
        self,
        local_path: str,
        args: list[str],
        failure_message: str,
        repository=None,
    ) -> str:
        result = self._run_git(local_path, args, failure_message, repository)
        return result.stdout.strip()

    def _run_git(
        self,
        local_path: str,
        args: list[str],
        failure_message: str,
        repository=None,
    ):
        self._validate_git_executable()
        command = ['git']
        env = None
        auth_header = self._git_http_auth_header(repository)
        if auth_header:
            command.extend(['-c', f'http.extraHeader={auth_header}'])
            env = os.environ.copy()
            env['GIT_TERMINAL_PROMPT'] = '0'
        result = subprocess.run(
            [*command, '-C', local_path, *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode == 0:
            return result
        raise RuntimeError(
            f'{failure_message}: '
            f'{result.stderr.strip() or result.stdout.strip() or "git command failed"}'
        )

    def _push_branch(self, local_path: str, branch_name: str, repository=None) -> None:
        self._run_git(
            local_path,
            ['push', '-u', 'origin', branch_name],
            f'failed to push branch {branch_name}',
            repository,
        )

    def _pull_destination_branch(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> None:
        self._run_git(
            local_path,
            ['pull', '--ff-only', 'origin', destination_branch],
            f'failed to pull latest {destination_branch} for repository at {local_path}',
            repository,
        )

    @classmethod
    def _git_http_auth_header(cls, repository) -> str:
        if repository is None:
            return ''
        remote_url = text_from_attr(repository, 'remote_url')
        if not cls._uses_http_remote(remote_url):
            return ''
        token = text_from_attr(repository, 'token')
        if not token:
            return ''
        username = cls._git_http_username(repository, remote_url)
        if not username:
            return ''
        encoded_credentials = base64.b64encode(
            f'{username}:{token}'.encode('utf-8')
        ).decode('ascii')
        return f'Authorization: Basic {encoded_credentials}'

    @classmethod
    def _git_http_username(cls, repository, remote_url: str) -> str:
        parsed = urlparse(remote_url)
        if parsed.username:
            return parsed.username
        provider = normalized_lower_text(text_from_attr(repository, 'provider'))
        return {
            'github': 'x-access-token',
            'gitlab': 'oauth2',
            'bitbucket': 'x-token-auth',
        }.get(provider, 'git')

    @staticmethod
    def _uses_http_remote(remote_url: str) -> bool:
        normalized = normalized_lower_text(remote_url)
        return normalized.startswith('https://') or normalized.startswith('http://')

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
