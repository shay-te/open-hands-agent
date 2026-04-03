import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from openhands_agent.client.bitbucket_auth import basic_auth_header
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data.fields import RepositoryFields
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.text_utils import (
    normalized_lower_text,
    normalized_text,
    text_from_attr,
)
from openhands_agent.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from openhands_agent.data_layers.service.repository_publication_service import (
    RepositoryPublicationService,
)


class RepositoryService(RepositoryInventoryService):
    """Manage repository worktree preparation, branch publication, and cleanup."""

    def __init__(self, repositories_config, max_retries: int) -> None:
        super().__init__(repositories_config, max_retries)
        self._publication_service = RepositoryPublicationService(self, max_retries)

    def prepare_task_repositories(self, repositories: list[object]) -> list[object]:
        self._validate_git_executable()
        return [
            self._prepare_task_repository(repository)
            for repository in repositories
        ]

    def restore_task_repositories(
        self,
        repositories: list[object],
        *,
        force: bool = False,
    ) -> list[object]:
        self._validate_git_executable()
        for repository in repositories:
            self._restore_task_repository(repository, force=force)
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

    def validate_task_branches_are_publishable(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> list[object]:
        from openhands_agent.validation.branch_publishability import (
            TaskBranchPublishabilityValidator,
        )

        TaskBranchPublishabilityValidator(self).validate(
            repositories,
            repository_branches,
        )
        return repositories

    def validate_task_branches_are_pushable(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> list[object]:
        from openhands_agent.validation.branch_push import (
            TaskBranchPushValidator,
        )

        TaskBranchPushValidator(self).validate(
            repositories,
            repository_branches,
        )
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
        return self._publication_service.create_pull_request(
            repository,
            title,
            source_branch,
            description=description,
            commit_message=commit_message,
        )

    def publish_review_fix(
        self,
        repository,
        branch_name: str,
        commit_message: str = '',
    ) -> None:
        self._publication_service.publish_review_fix(
            repository,
            branch_name,
            commit_message=commit_message,
        )

    def list_pull_request_comments(
        self,
        repository,
        pull_request_id: str,
    ) -> list[dict[str, str]]:
        return self._publication_service.list_pull_request_comments(
            repository,
            pull_request_id,
        )

    def resolve_review_comment(self, repository, comment) -> None:
        self._publication_service.resolve_review_comment(repository, comment)

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

    def _ensure_branch_is_pushable(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
    ) -> None:
        try:
            self._push_branch(local_path, branch_name, repository, dry_run=True)
        except RuntimeError as exc:
            error_text = str(exc)
            error_detail = error_text.split(': ', 1)[1] if ': ' in error_text else error_text
            if (
                'could not read Password' in error_text
                or 'terminal prompts disabled' in error_text
                or 'Authentication failed' in error_text
                or 'credentials lack one or more required privilege scopes' in error_text
            ):
                raise RuntimeError(
                    f'[Error] {local_path} missing git push permissions. cannot work. '
                    f'{error_detail}'
                ) from None
            raise RuntimeError(
                f'[Error] {local_path} git push validation failed. {error_detail}'
            ) from None

    def _prepare_task_repository(self, repository):
        self._prepare_repository_access(repository)
        setattr(repository, 'destination_branch', self.destination_branch(repository))
        self._prepare_workspace_for_task(
            repository.local_path,
            repository.destination_branch,
            repository,
        )
        return repository

    def _restore_task_repository(self, repository, force: bool = False) -> None:
        self._validate_local_path(repository)
        destination_branch = text_from_attr(repository, 'destination_branch') or self.destination_branch(
            repository
        )
        current_branch = self._current_branch(repository.local_path)
        dirty_worktree = bool(self._working_tree_status(repository.local_path))
        if current_branch == destination_branch and not dirty_worktree:
            return
        if dirty_worktree and not force:
            self.logger.warning(
                'skipping repository restore for %s because the worktree is dirty on branch %s',
                repository.id,
                current_branch or '<unknown>',
            )
            return
        if dirty_worktree and force:
            self.logger.warning(
                'forcing repository restore for %s to branch %s despite dirty worktree on branch %s',
                repository.id,
                destination_branch,
                current_branch or '<unknown>',
            )
        try:
            checkout_args = ['checkout']
            if dirty_worktree:
                checkout_args.append('-f')
            checkout_args.append(destination_branch)
            self._run_git(
                repository.local_path,
                checkout_args,
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

    @staticmethod
    def _validate_git_executable() -> None:
        if shutil.which('git'):
            return
        raise RuntimeError('git executable is required but was not found on PATH')

    @staticmethod
    def _git_safe_directory_args(local_path: str) -> list[str]:
        safe_directory = normalized_text(local_path)
        if not safe_directory:
            return []
        return ['-c', f'safe.directory={safe_directory}']

    @classmethod
    def _git_command(cls, local_path: str, args: list[str]) -> list[str]:
        return [
            'git',
            *cls._git_safe_directory_args(local_path),
            '-C',
            local_path,
            *args,
        ]

    def _prepare_branch_for_publication(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
        commit_message: str,
    ) -> str:
        self._assert_branch_checked_out(local_path, branch_name)
        validation_report_description = self._commit_branch_changes_if_needed(
            local_path,
            branch_name,
            commit_message,
        )
        self._ensure_branch_is_publishable(
            local_path,
            branch_name,
            destination_branch,
        )
        return validation_report_description

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
    ) -> str:
        status_output = self._working_tree_status(local_path)
        if not status_output:
            return ''
        self._run_git(
            local_path,
            ['add', '-A'],
            f'failed to stage changes for branch {branch_name}',
        )
        validation_report_descriptions: list[str] = []
        for artifact_path in self._generated_artifact_paths_from_status(status_output):
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', artifact_path],
                (
                    f'failed to exclude generated artifact path '
                    f'{artifact_path} from branch {branch_name}'
                ),
            )
            artifact_full_path = os.path.join(local_path, artifact_path)
            if os.path.isdir(artifact_full_path):
                shutil.rmtree(artifact_full_path)
            elif os.path.exists(artifact_full_path):
                os.remove(artifact_full_path)
        for validation_report_path in self._validation_report_paths_from_status(status_output):
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', validation_report_path],
                (
                    f'failed to exclude validation report file '
                    f'{validation_report_path} from branch {branch_name}'
                ),
            )
            # The report is published as a task comment, not as a committed file.
            validation_report_full_path = os.path.join(local_path, validation_report_path)
            validation_report_description = self._validation_report_text(
                validation_report_full_path,
            )
            if validation_report_description is None:
                self.logger.warning(
                    'validation report file was reported by git status but missing at %s',
                    validation_report_full_path,
                )
            elif not validation_report_description:
                self.logger.warning(
                    'validation report file was empty at %s',
                    validation_report_full_path,
                )
            else:
                validation_report_descriptions.append(validation_report_description)
            if os.path.exists(validation_report_full_path):
                os.remove(validation_report_full_path)
        self._run_git(
            local_path,
            ['commit', '-m', commit_message],
            f'failed to commit changes for branch {branch_name}',
        )
        self._ensure_clean_worktree(
            local_path,
            branch_name,
        )
        return '\n\n'.join(validation_report_descriptions).strip()

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
            f'branch {branch_name} has no task changes ahead of {comparison_ref}'
        )

    def _ensure_branch_has_task_changes(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
    ) -> None:
        if self._working_tree_status(local_path):
            return
        self._ensure_branch_is_publishable(local_path, branch_name, destination_branch)

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
        *,
        restore_workspace: bool = True,
    ) -> str:
        validation_report_description = ''
        try:
            validation_report_description = self._prepare_branch_for_publication(
                local_path,
                branch_name,
                destination_branch,
                commit_message,
            )
            self._push_branch(local_path, branch_name, repository)
        finally:
            if restore_workspace:
                self._prepare_workspace_for_task(local_path, destination_branch, repository)
        return validation_report_description

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

    @staticmethod
    def _validation_report_paths_from_status(status_output: str) -> list[str]:
        validation_report_paths = []
        for line in status_output.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            if path.endswith('validation_report.md'):
                validation_report_paths.append(path)
        return validation_report_paths

    @staticmethod
    def _validation_report_text(validation_report_full_path: str) -> str | None:
        if not os.path.exists(validation_report_full_path):
            return None
        return Path(validation_report_full_path).read_text(encoding='utf-8').strip()

    @staticmethod
    def _generated_artifact_paths_from_status(status_output: str) -> list[str]:
        generated_artifact_paths: list[str] = []
        generated_artifact_roots = {'build', 'dist', 'out', 'coverage', 'target'}
        for line in status_output.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            normalized_path = path.strip().rstrip('/')
            if not normalized_path:
                continue
            if normalized_path.endswith('validation_report.md'):
                continue
            path_root = normalized_path.split('/', 1)[0]
            if path_root not in generated_artifact_roots:
                continue
            if path_root not in generated_artifact_paths:
                generated_artifact_paths.append(path_root)
        return generated_artifact_paths

    def _git_reference_exists(self, local_path: str, reference: str) -> bool:
        result = subprocess.run(
            self._git_command(local_path, ['rev-parse', '--verify', reference]),
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
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        auth_header = self._git_http_auth_header(repository)
        if auth_header:
            command.extend(['-c', f'http.extraHeader={auth_header}'])
        result = subprocess.run(
            [*command, *self._git_safe_directory_args(local_path), '-C', local_path, *args],
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

    def _push_branch(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
        *,
        dry_run: bool = False,
    ) -> None:
        push_args = ['push']
        if dry_run:
            push_args.append('--dry-run')
        push_args.extend(['-u', 'origin', branch_name])
        self._run_git(
            local_path,
            push_args,
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
        return f'Authorization: {basic_auth_header(username, token)}'

    @classmethod
    def _git_http_username(cls, repository, remote_url: str) -> str:
        parsed = urlparse(remote_url)
        provider = normalized_lower_text(text_from_attr(repository, 'provider'))
        if provider == 'bitbucket':
            bitbucket_username = text_from_attr(repository, RepositoryFields.BITBUCKET_USERNAME)
            if bitbucket_username:
                return bitbucket_username
            username = text_from_attr(repository, 'username')
            if username:
                return username
            return parsed.username or 'x-token-auth'
        if parsed.username:
            return parsed.username
        return {
            'github': 'x-access-token',
            'gitlab': 'oauth2',
            'bitbucket': 'x-token-auth',
        }.get(provider, 'git')

    @staticmethod
    def _uses_http_remote(remote_url: str) -> bool:
        normalized = normalized_lower_text(remote_url)
        return normalized.startswith('https://') or normalized.startswith('http://')

    @staticmethod
    def _infer_default_branch(local_path: str) -> str:
        RepositoryService._validate_git_executable()
        commands = [
            ['symbolic-ref', 'refs/remotes/origin/HEAD'],
            ['branch', '--show-current'],
        ]
        for command in commands:
            result = subprocess.run(
                RepositoryService._git_command(local_path, command),
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
