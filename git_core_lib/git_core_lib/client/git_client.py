"""GitClientMixin — provider-agnostic git subprocess engine.

Mixin class providing all git subprocess operations. Subclasses must
supply ``self.logger``. HTTP auth injection is a hook:
override ``_build_git_http_auth_header(repository)`` to return a
non-empty header string when the repository uses an HTTP remote.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from git_core_lib.git_core_lib.helpers.text_utils import (
    normalized_lower_text,
    normalized_text,
)


class GitClientMixin:
    """Mixin providing git subprocess operations for any service class.

    Requirements for the host class:
    - Must have a ``self.logger`` attribute (``logging.Logger``).
    - May override ``_build_git_http_auth_header(repository) -> str``
      to inject provider-specific HTTP auth headers.
    """

    GIT_SUBPROCESS_TIMEOUT_SECONDS = 300
    NON_FAST_FORWARD_PUSH_REJECTION_MARKERS = (
        'fetch first',
        'non-fast-forward',
        'updates were rejected because the remote contains work',
    )

    # ----- hook for subclasses -----

    def _build_git_http_auth_header(self, repository) -> str:
        """Return an HTTP ``Authorization`` header for git HTTP remotes.

        Default returns '' (no auth). Subclasses override to inject
        provider-specific credentials (e.g. Basic auth for Bitbucket).
        """
        return ''

    # ----- subprocess core -----

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
        # ``core.hooksPath=/dev/null`` disables every git hook for kato's own
        # invocations — guards against a sandboxed agent dropping a malicious
        # hook that would fire with operator privileges on the next push.
        return [
            'git',
            *cls._git_safe_directory_args(local_path),
            '-c', 'core.hooksPath=/dev/null',
            '-C',
            local_path,
            *args,
        ]

    @classmethod
    def _run_capture(cls, cmd: list[str], *, env=None):
        """Run ``cmd`` capturing text output, never raising on a
        non-zero exit. The shared kwargs for every plain-capture
        subprocess invocation in this mixin live here."""
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            env=env,
            timeout=cls.GIT_SUBPROCESS_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _failure_detail(result) -> str:
        return result.stderr.strip() or result.stdout.strip() or 'git command failed'

    def _run_git_subprocess(
        self,
        local_path: str,
        args: list[str],
        repository=None,
    ):
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        auth_header = self._build_git_http_auth_header(repository)
        if auth_header:
            env['GIT_CONFIG_COUNT'] = '1'
            env['GIT_CONFIG_KEY_0'] = 'http.extraHeader'
            env['GIT_CONFIG_VALUE_0'] = auth_header
        else:
            env.pop('GIT_CONFIG_COUNT', None)
            env.pop('GIT_CONFIG_KEY_0', None)
            env.pop('GIT_CONFIG_VALUE_0', None)
        return self._run_capture(self._git_command(local_path, args), env=env)

    def _run_git(
        self,
        local_path: str,
        args: list[str],
        failure_message: str,
        repository=None,
    ):
        self._validate_git_executable()
        result = self._run_git_subprocess(local_path, args, repository)
        if result.returncode == 0:
            return result
        failure_detail = self._failure_detail(result)
        if self._is_git_index_lock_error(failure_detail) and self._clear_stale_git_index_lock(
            local_path
        ):
            result = self._run_git_subprocess(local_path, args, repository)
            if result.returncode == 0:
                return result
            failure_detail = self._failure_detail(result)
        raise RuntimeError(f'{failure_message}: {failure_detail}')

    def _git_stdout(
        self,
        local_path: str,
        args: list[str],
        failure_message: str,
        repository=None,
    ) -> str:
        result = self._run_git(local_path, args, failure_message, repository)
        return result.stdout.strip()

    # ----- reference / status queries -----

    def _git_reference_exists(self, local_path: str, reference: str) -> bool:
        result = self._run_capture(
            self._git_command(local_path, ['rev-parse', '--verify', reference]),
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

    # ----- index lock recovery -----

    @staticmethod
    def _is_git_index_lock_error(error_text: str) -> bool:
        normalized = normalized_lower_text(error_text)
        return 'index.lock' in normalized and 'file exists' in normalized

    def _clear_stale_git_index_lock(self, local_path: str) -> bool:
        lock_path = Path(local_path) / '.git' / 'index.lock'
        if self._has_running_git_process(local_path):
            self.logger.warning(
                'leaving git index lock in place at %s because another git process is still running',
                lock_path,
            )
            return False
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return False
        self.logger.warning('removed stale git index lock at %s', lock_path)
        return True

    @staticmethod
    def _has_running_git_process(local_path: str) -> bool:
        try:
            result = subprocess.run(
                ['ps', '-eo', 'command='],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=GitClientMixin.GIT_SUBPROCESS_TIMEOUT_SECONDS,
            )
        except OSError:
            return False
        if result.returncode != 0:
            return False
        repository_arg = f'-C {local_path}'
        for command_line in result.stdout.splitlines():
            normalized_command_line = command_line.strip()
            if not normalized_command_line.startswith('git '):
                continue
            if repository_arg in normalized_command_line:
                return True
        return False

    # ----- push / sync / rebase -----

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
        try:
            self._run_git(local_path, push_args, f'failed to push branch {branch_name}', repository)
        except RuntimeError as exc:
            if dry_run or not self._is_non_fast_forward_push_rejection(exc):
                raise
            self.logger.warning(
                'push for branch %s was rejected because origin has newer commits; '
                'fetching and rebasing before retrying',
                branch_name,
            )
            self._sync_branch_with_remote(local_path, branch_name, repository)
            self._run_git(
                local_path,
                push_args,
                f'failed to push branch {branch_name} after syncing with origin/{branch_name}',
                repository,
            )

    def _sync_branch_with_remote(
        self, local_path: str, branch_name: str, repository=None
    ) -> None:
        remote_branch_ref = f'refs/remotes/origin/{branch_name}'
        remote_branch = f'origin/{branch_name}'
        self._run_git(
            local_path,
            ['fetch', 'origin', f'{branch_name}:{remote_branch_ref}'],
            f'failed to fetch latest {remote_branch} before pushing {branch_name}',
            repository,
        )
        if not self._git_reference_exists(local_path, remote_branch):
            raise RuntimeError(
                f'failed to fetch latest {remote_branch} before pushing {branch_name}: '
                f'{remote_branch} is not available locally'
            )
        self._rebase_branch_onto_remote(local_path, branch_name, remote_branch, repository)

    def _rebase_branch_onto_remote(
        self,
        local_path: str,
        branch_name: str,
        remote_branch: str,
        repository=None,
    ) -> None:
        try:
            self._run_git(
                local_path,
                ['rebase', remote_branch],
                f'failed to rebase branch {branch_name} onto {remote_branch}',
                repository,
            )
        except RuntimeError:
            self._abort_rebase_after_failure(local_path, branch_name, repository)
            raise

    def _abort_rebase_after_failure(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
    ) -> None:
        try:
            self._run_git(
                local_path,
                ['rebase', '--abort'],
                f'failed to abort rebase for branch {branch_name}',
                repository,
            )
        except RuntimeError as abort_exc:
            self.logger.warning(
                'failed to abort rebase for branch %s after push-sync failure: %s',
                branch_name,
                abort_exc,
            )

    @classmethod
    def _is_non_fast_forward_push_rejection(cls, exc: RuntimeError) -> bool:
        message = normalized_lower_text(str(exc))
        return any(marker in message for marker in cls.NON_FAST_FORWARD_PUSH_REJECTION_MARKERS)

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

    # ----- misc -----

    @staticmethod
    def _uses_http_remote(remote_url: str) -> bool:
        normalized = normalized_lower_text(remote_url)
        return normalized.startswith('https://') or normalized.startswith('http://')

    @classmethod
    def _infer_default_branch(cls, local_path: str) -> str:
        cls._validate_git_executable()
        commands = [
            ['symbolic-ref', 'refs/remotes/origin/HEAD'],
            ['branch', '--show-current'],
        ]
        for command in commands:
            result = cls._run_capture(cls._git_command(local_path, command))
            output = result.stdout.strip()
            if result.returncode != 0 or not output:
                continue
            if output.startswith('refs/remotes/'):
                return output.rsplit('/', 1)[-1]
            return output
        raise ValueError(
            f'unable to determine destination branch for repository at {local_path}'
        )
