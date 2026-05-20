from __future__ import annotations

import os
from pathlib import Path

from bitbucket_core_lib.bitbucket_core_lib.helpers.git_auth import git_http_auth_header
from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from git_core_lib.git_core_lib.helpers.git_clean_utils import (
    generated_artifact_paths_from_status,
    git_ready_command_summary,
    status_contains_only_removable_artifacts,
    validation_report_paths_from_status,
)
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data.fields import RepositoryFields
from kato_core_lib.helpers.text_utils import (
    normalized_text,
    text_from_attr,
)
from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from kato_core_lib.data_layers.service.repository_publication_service import (
    RepositoryPublicationService,
)


def _is_per_task_workspace_clone(repository) -> bool:
    """True when ``repository.local_path`` is under a per-task kato workspace.

    Per-task clones live at ``<workspace_root>/<task_id>/<repo_id>/``
    next to a ``.kato-meta.json`` sidecar; legacy / shared clones live
    elsewhere on disk and don't carry the sidecar. We use this signal
    to keep per-task clones on the task branch across publish ops
    (the "restore to master after push" behavior is for shared clones).
    """
    local_path = str(getattr(repository, 'local_path', '') or '').strip()
    if not local_path:
        return False
    try:
        return (Path(local_path).parent / '.kato-meta.json').is_file()
    except OSError:
        return False


class RepositoryHasNoChangesError(RuntimeError):
    """Raised when a task branch has nothing to publish in a given repo.

    A typed exception (rather than a string-matched RuntimeError) lets
    the publisher tell ``"the work was a no-op for this repo"`` apart
    from genuine publish failures. The former is a normal outcome for
    multi-repo tasks where a repository is tagged for context but the
    agent didn't change any of its files; the latter blocks the task.
    """


class RepositoryService(GitClientMixin, RepositoryInventoryService):
    """Manage repository worktree preparation, branch publication, and cleanup."""

    def __init__(self, repositories_config, max_retries: int) -> None:
        super().__init__(repositories_config, max_retries)
        self._publication_service = RepositoryPublicationService(self, max_retries)

    def _build_git_http_auth_header(self, repository) -> str:
        return git_http_auth_header(
            repository,
            bitbucket_username_attr=RepositoryFields.BITBUCKET_USERNAME,
        )

    def prepare_task_repositories(self, repositories: list[object]) -> list[object]:
        self._validate_git_executable()
        return [
            self._prepare_task_repository(repository)
            for repository in repositories
        ]

    def ensure_clone(self, repository, target_path) -> None:
        """Clone the repo's remote into ``target_path`` if it isn't already.

        Idempotent: if ``target_path/.git`` exists we trust it and skip
        the clone (the rest of the pipeline will fetch / reset / check out
        the task branch). Used by per-task workspace mode — each ticket
        gets its own clone-set so parallel tasks don't share branch state.
        """
        self._validate_git_executable()
        target = Path(str(target_path))
        if (target / '.git').is_dir():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        remote_url = normalized_text(text_from_attr(repository, 'remote_url'))
        if not remote_url:
            raise ValueError(
                f'cannot clone repository {repository.id}: no remote_url configured'
            )
        # ``git -C <parent> clone <url> <name>`` keeps the call shape the
        # rest of this service uses. Auth is whatever the user has set
        # up on their host (ssh-agent, git credential helper, or token
        # baked into the URL); kato doesn't manage credentials at the
        # transport layer.
        self._run_git(
            str(target.parent),
            ['clone', remote_url, target.name],
            f'failed to clone {repository.id} from {remote_url} into {target}',
            repository,
        )

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
        from kato_core_lib.validation.branch_publishability import (
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
        from kato_core_lib.validation.branch_push import (
            TaskBranchPushValidator,
        )

        TaskBranchPushValidator(self).validate(
            repositories,
            repository_branches,
        )
        return repositories

    def get_repository(self, repository_id: str):
        # ``_repositories`` is lazy-initialized via ``_ensure_repositories``
        # — iterating it directly trips on ``None`` when nothing has
        # warmed the inventory yet (e.g. the planning UI's publish-state
        # poll firing before the first scan). Use the ensure-helper so
        # the load is idempotent and the iteration is always safe.
        for repository in self._ensure_repositories():
            if repository.id == repository_id:
                return repository
        # Direct folder lookup fallback — same fast path used by
        # _resolve_repository_for_tag, so a repo resolved by tag during
        # task setup is always findable here even if the warm-up walk
        # missed it (e.g. timing, walk error, or Windows path edge case).
        direct = self._discover_repository_at_named_folder(repository_id)
        if direct is not None:
            return direct
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

    def update_source_to_task_branch(
        self,
        repository,
        branch_name: str,
    ) -> dict[str, object]:
        """Switch the source-folder clone of ``repository`` to ``branch_name``.

        For the planning UI's "Update source" button: after a per-task
        clone has pushed its branch to the remote, the operator's
        live / running system (which lives at ``repository.local_path``
        in the inventory, NOT in the per-task workspace) needs to be
        on that branch and up-to-date so it can be tested end-to-end.

        Steps:
          1. ``git fetch origin --prune``
          2. If the working tree has uncommitted changes, ``git stash
             push --include-untracked`` so the switch+pull doesn't
             collide with them. The operator's work is preserved —
             we never silently throw it away.
          3. ``git checkout <branch_name>`` (auto-creates a tracking
             branch from ``origin/<branch_name>`` when needed).
          4. ``git pull --ff-only origin <branch_name>``.
          5. If we stashed in step 2, ``git stash pop`` to put the
             operator's changes back on top of the new branch. A
             pop conflict is **not** a failure — the operator gets
             conflict markers in the working tree to resolve when
             they're ready, and the warning surfaces in the
             returned status dict.

        Returns a status dict:
            {
              'updated': True,
              'stashed': bool,
              'stash_reapplied': bool,
              'stash_conflict': bool,
              'warning': str,  # operator-readable note, may be empty
            }

        Raises ``RuntimeError`` only on truly catastrophic failures
        (missing local_path, not a git repo, fetch / checkout / pull
        failed against origin). Dirty tree is **not** a failure.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            raise RuntimeError(
                f'repository {repository.id} has no local_path set; '
                'cannot update source folder',
            )
        if not (Path(local_path) / '.git').is_dir():
            raise RuntimeError(
                f'source folder for repository {repository.id} at '
                f'{local_path} is not a git repository',
            )
        # Inspect the tree. Dirty → stash so the upcoming switch+pull
        # doesn't collide with the operator's in-progress work.
        try:
            status_output = self._working_tree_status(local_path)
        except Exception as exc:
            raise RuntimeError(
                f'failed to inspect source folder for {repository.id}: {exc}',
            ) from exc
        stashed = bool(status_output.strip())
        if stashed:
            self._run_git(
                local_path,
                [
                    'stash', 'push',
                    '--include-untracked',
                    '--message', f'kato: pre-update-source {branch_name}',
                ],
                f'failed to stash uncommitted changes in {repository.id} '
                f'source folder before switching branches',
            )
        # Step 1-4: fetch / checkout / pull.
        self._run_git(
            local_path,
            ['fetch', 'origin', '--prune'],
            f'failed to fetch origin for {repository.id} source folder',
        )
        self._run_git(
            local_path,
            ['checkout', branch_name],
            f'failed to checkout branch {branch_name} in {repository.id} '
            f'source folder',
        )
        self._run_git(
            local_path,
            ['pull', '--ff-only', 'origin', branch_name],
            f'failed to fast-forward {branch_name} in {repository.id} '
            f'source folder',
        )
        stash_reapplied = False
        stash_conflict = False
        warning = ''
        if stashed:
            # Pop is best-effort: a conflict leaves the operator's
            # changes in the working tree as conflict markers, which
            # is exactly what we want — visible, fixable, no data
            # loss. We do NOT raise.
            try:
                self._run_git(
                    local_path,
                    ['stash', 'pop'],
                    'stash pop failed',
                )
                stash_reapplied = True
                warning = (
                    f'switched to {branch_name} in {repository.id} source '
                    f'folder and reapplied your uncommitted changes via stash'
                )
            except RuntimeError as exc:
                stash_conflict = True
                warning = (
                    f'switched to {branch_name} in {repository.id} source '
                    f'folder; your uncommitted changes are in the stash but '
                    f'reapplying produced conflicts. Resolve them in '
                    f'{local_path} (or run ``git stash drop`` to discard). '
                    f'Detail: {exc}'
                )
                self.logger.warning(
                    'update-source: stash pop in %s failed (%s); '
                    'changes preserved in the stash for manual resolution',
                    local_path, exc,
                )
        return {
            'updated': True,
            'stashed': stashed,
            'stash_reapplied': stash_reapplied,
            'stash_conflict': stash_conflict,
            'warning': warning,
        }

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

    def find_pull_requests(
        self,
        repository,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        return self._publication_service.find_pull_requests(
            repository,
            source_branch=source_branch,
            title_prefix=title_prefix,
        )

    def branch_needs_push(self, repository, branch_name: str) -> bool:
        """True when ``Push`` would actually publish something.

        The on-demand push path (``publish_review_fix``) refuses to
        proceed unless three preconditions hold; this check mirrors all
        three so the planning UI doesn't enable a button whose click
        would error:

        1. The workspace is currently checked out on ``branch_name`` —
           ``_assert_branch_checked_out`` rejects everything else.
        2. There would be at least one commit ahead of the destination
           branch after committing any dirty tree —
           ``_ensure_branch_is_publishable`` raises
           ``RepositoryHasNoChangesError`` when the branch is in sync.
        3. The push would actually send work to the remote — ``origin/
           <branch>`` is missing or behind, OR the working tree is dirty
           (the new commit will move local past origin).

        Best-effort: any git failure returns ``False`` so the button
        stays disabled rather than promising a push that won't work.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        normalized_branch = (branch_name or '').strip()
        if not local_path or not normalized_branch:
            return False
        try:
            if not (Path(local_path) / '.git').is_dir():
                return False
        except OSError:
            return False
        try:
            current_branch = self._current_branch(local_path)
        except Exception:
            return False
        # Precondition 1 — publish_review_fix asserts the workspace is
        # checked out on the task branch. If it isn't (e.g. workspace
        # was reset to master after a prior publish), there's nothing
        # the Push button can do without first checking out, so disable.
        if current_branch != normalized_branch:
            return False
        try:
            is_dirty = bool(self._working_tree_status(local_path).strip())
        except Exception:
            return False
        # Precondition 2 — branch must be (or become, after committing
        # dirty tree) ahead of the destination branch.
        try:
            destination_branch = self.destination_branch(repository)
            comparison_reference = self._comparison_reference(
                local_path, destination_branch,
            )
        except Exception:
            return False
        try:
            ahead_destination = self._ahead_count(
                local_path, comparison_reference, normalized_branch,
            )
        except Exception:
            return False
        if ahead_destination == 0 and not is_dirty:
            return False
        # Precondition 3 — push must send something the remote doesn't
        # already have. Dirty tree → upcoming commit will exceed origin.
        if is_dirty:
            return True
        remote_reference = f'origin/{normalized_branch}'
        try:
            remote_branch_exists = self._git_reference_exists(
                local_path, remote_reference,
            )
        except Exception:
            return False
        if not remote_branch_exists:
            return True
        try:
            ahead_remote, _behind = self._left_right_commit_counts(
                local_path, normalized_branch, remote_reference,
            )
        except Exception:
            return False
        return ahead_remote > 0

    def workspace_has_task_changes(self, repository, branch_name: str) -> bool:
        """True when the workspace clone has commits on the task branch.

        Drives the "Update source" skip path: ``update_source_to_task_branch``
        only does fetch / checkout / pull — it never commits. So the only
        thing that can propagate from the workspace to the operator's
        source folder is a commit that already lives on the task branch.
        Untracked artifacts left behind by the agent's test runs (npm
        install caches, .pytest_cache, build outputs, etc.) are
        deliberately NOT a "change" signal: counting them would falsely
        flip every repo to "changed" and pull the operator off their
        current branch for nothing.

        Returns ``True`` on any inspection failure so unexpected git
        states fall through to the update path rather than silently
        swallow real work the operator was expecting.

        Skip rules (return ``False``):
        - Workspace HEAD is not on the task branch — the agent never
          moved off master, so there is nothing branch-shaped to ship.
        - Workspace IS on the task branch but has zero commits ahead of
          the destination branch — branch exists but is empty of work.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        normalized_branch = (branch_name or '').strip()
        if not local_path or not normalized_branch:
            return True
        try:
            if not (Path(local_path) / '.git').is_dir():
                return True
        except OSError:
            return True
        try:
            current_branch = self._current_branch(local_path)
        except Exception:
            return True
        if current_branch != normalized_branch:
            return False
        try:
            destination_branch = self.destination_branch(repository)
            comparison_reference = self._comparison_reference(
                local_path, destination_branch,
            )
            ahead = self._ahead_count(
                local_path, comparison_reference, normalized_branch,
            )
        except Exception:
            return True
        return ahead > 0

    def pull_workspace_clone(
        self,
        repository,
        branch_name: str,
    ) -> dict[str, object]:
        """Fast-forward the per-task workspace clone of ``repository`` to
        ``origin/<branch_name>``.

        Operator-driven. Drives the planning UI's ``Pull`` button —
        symmetric to ``Push``. Refuses cleanly (does NOT auto-stash)
        when the working tree is dirty: pulling would risk colliding
        with in-progress agent edits, and the safer move is to let
        the operator commit / discard those first. ``update_source``
        is the place where we DO auto-stash, because it's targeting
        the operator's own checkout, not kato's working clone.

        Returns a status dict with one of:
            {'pulled': True,  'updated': bool, 'commits_pulled': int}
            {'pulled': False, 'reason': '<short>', 'detail': '<long>'}
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        normalized_branch = (branch_name or '').strip()
        if not local_path:
            return {
                'pulled': False, 'reason': 'no_local_path',
                'detail': f'repository {repository.id} has no local_path set',
            }
        if not (Path(local_path) / '.git').is_dir():
            return {
                'pulled': False, 'reason': 'not_a_git_repo',
                'detail': f'workspace clone for {repository.id} at '
                          f'{local_path} is not a git repository',
            }
        if not normalized_branch:
            return {
                'pulled': False, 'reason': 'no_branch',
                'detail': f'no task branch for {repository.id}',
            }
        try:
            current = self._current_branch(local_path)
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'branch_lookup_failed',
                'detail': str(exc),
            }
        # The branch the workspace is on must be the task branch we
        # are pulling into; otherwise a fast-forward would land in
        # the wrong place. Operator-fixable (checkout the task
        # branch first), so we surface a clear reason.
        if current != normalized_branch:
            return {
                'pulled': False, 'reason': 'wrong_branch_checked_out',
                'detail': f'workspace is on {current!r}, expected '
                          f'{normalized_branch!r} — checkout first',
            }
        try:
            dirty = bool(self._working_tree_status(local_path).strip())
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'status_check_failed',
                'detail': str(exc),
            }
        if dirty:
            return {
                'pulled': False, 'reason': 'dirty_working_tree',
                'detail': 'workspace has uncommitted changes; commit or '
                          'discard them before pulling',
            }
        try:
            self._run_git(
                local_path, ['fetch', 'origin', '--prune'],
                f'failed to fetch origin for {repository.id} workspace',
                repository,
            )
        except RuntimeError as exc:
            return {'pulled': False, 'reason': 'fetch_failed', 'detail': str(exc)}
        remote_reference = f'origin/{normalized_branch}'
        try:
            remote_exists = self._git_reference_exists(local_path, remote_reference)
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'remote_lookup_failed',
                'detail': str(exc),
            }
        if not remote_exists:
            # No remote branch to pull from. Common right after a
            # fresh task before anything was pushed; not an error,
            # just a no-op.
            return {
                'pulled': True, 'updated': False, 'commits_pulled': 0,
                'reason': 'remote_branch_missing',
            }
        try:
            _ahead, behind = self._left_right_commit_counts(
                local_path, normalized_branch, remote_reference,
            )
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'commit_count_failed',
                'detail': str(exc),
            }
        if behind == 0:
            return {'pulled': True, 'updated': False, 'commits_pulled': 0}
        try:
            self._run_git(
                local_path,
                ['pull', '--ff-only', 'origin', normalized_branch],
                f'failed to fast-forward {repository.id} workspace from origin',
                repository,
            )
        except RuntimeError as exc:
            return {'pulled': False, 'reason': 'pull_failed', 'detail': str(exc)}
        return {'pulled': True, 'updated': True, 'commits_pulled': int(behind)}

    def _merge_preflight(
        self,
        repository,
        local_path: str,
        normalized_branch: str,
    ) -> dict[str, object]:
        """Validate the clone is safe to merge into.

        Returns ``{'error': <status dict>}`` on any refusal, or
        ``{'default_branch': <name>}`` when the clone is on the task
        branch with a clean tree and the default branch is known.
        """
        def fail(reason: str, detail: str) -> dict[str, object]:
            return {'error': {
                'merged': False, 'reason': reason, 'detail': detail,
            }}

        if not local_path:
            return fail(
                'no_local_path',
                f'repository {repository.id} has no local_path set',
            )
        if not (Path(local_path) / '.git').is_dir():
            return fail(
                'not_a_git_repo',
                f'workspace clone for {repository.id} at {local_path} '
                f'is not a git repository',
            )
        if not normalized_branch:
            return fail('no_branch', f'no task branch for {repository.id}')
        try:
            current = self._current_branch(local_path)
        except Exception as exc:
            return fail('branch_lookup_failed', str(exc))
        if current != normalized_branch:
            return fail(
                'wrong_branch_checked_out',
                f'workspace is on {current!r}, expected '
                f'{normalized_branch!r} — checkout first',
            )
        try:
            dirty = bool(self._working_tree_status(local_path).strip())
        except Exception as exc:
            return fail('status_check_failed', str(exc))
        if dirty:
            # A merge into a dirty tree is git-unsafe and would also
            # tangle the agent's in-progress edits with the merge.
            # Push (which commits) first, then Merge.
            return fail(
                'dirty_working_tree',
                'workspace has uncommitted changes; push or discard '
                'them before merging the default branch',
            )
        try:
            return {'default_branch': self.destination_branch(repository)}
        except ValueError as exc:
            return fail('default_branch_unknown', str(exc))

    def merge_default_branch_into_clone(
        self,
        repository,
        branch_name: str,
    ) -> dict[str, object]:
        """Fetch + merge the repo's default branch into the task branch.

        Drives the planning UI's ``Merge master`` button. The agent's
        per-task clone is intentionally blocked from running git, so
        when the task branch falls behind ``origin/<default>`` and
        develops conflicts the agent has no way to pull + merge
        itself. This does the git plumbing on the operator's behalf
        and — crucially — when the merge conflicts it does NOT abort:
        the conflict markers + ``MERGE_HEAD`` are left in the working
        tree so the agent can resolve them by editing files, and
        kato's normal commit/push flow finalises the merge.

        Returns one of:
            {'merged': True,  'updated': bool, 'default_branch': str,
             'commits_merged': int}
            {'merged': False, 'conflicts': True, 'default_branch': str,
             'conflicted_files': [str, ...]}
            {'merged': False, 'reason': '<short>', 'detail': '<long>'}
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        normalized_branch = (branch_name or '').strip()
        preflight = self._merge_preflight(
            repository, local_path, normalized_branch,
        )
        if preflight.get('error'):
            return preflight['error']
        default_branch = preflight['default_branch']
        try:
            self._run_git(
                local_path, ['fetch', 'origin', '--prune'],
                f'failed to fetch origin for {repository.id} workspace',
                repository,
            )
        except RuntimeError as exc:
            return {'merged': False, 'reason': 'fetch_failed', 'detail': str(exc)}
        remote_reference = f'origin/{default_branch}'
        try:
            remote_exists = self._git_reference_exists(
                local_path, remote_reference,
            )
        except Exception as exc:
            return {
                'merged': False, 'reason': 'remote_lookup_failed',
                'detail': str(exc),
            }
        if not remote_exists:
            return {
                'merged': False, 'reason': 'remote_default_missing',
                'detail': f'{remote_reference} does not exist on origin',
            }
        try:
            _ahead, behind = self._left_right_commit_counts(
                local_path, normalized_branch, remote_reference,
            )
        except Exception as exc:
            return {
                'merged': False, 'reason': 'commit_count_failed',
                'detail': str(exc),
            }
        if behind == 0:
            # Task branch already contains every commit from the
            # default branch — nothing to merge.
            return {
                'merged': True, 'updated': False, 'commits_merged': 0,
                'default_branch': default_branch,
            }
        # ``_run_git_subprocess`` (not ``_run_git``) — a merge
        # conflict is a non-zero exit we EXPECT and want to handle,
        # not raise on.
        merge_result = self._run_git_subprocess(
            local_path,
            ['merge', '--no-edit', remote_reference],
            repository,
        )
        if merge_result.returncode == 0:
            return {
                'merged': True, 'updated': True,
                'commits_merged': int(behind),
                'default_branch': default_branch,
            }
        conflicted = self._unmerged_paths(local_path)
        if conflicted:
            # Leave the conflict markers + MERGE_HEAD in place — the
            # agent resolves them by editing files; kato's normal
            # commit/push finalises the merge.
            return {
                'merged': False, 'conflicts': True,
                'default_branch': default_branch,
                'conflicted_files': conflicted,
            }
        # Non-zero exit but no unmerged paths → some other merge
        # failure (e.g. refusing for an unrelated reason). Abort so
        # the tree is left clean rather than half-merged.
        self._run_git_subprocess(local_path, ['merge', '--abort'], repository)
        detail = (
            merge_result.stderr.strip()
            or merge_result.stdout.strip()
            or 'git merge failed'
        )
        return {'merged': False, 'reason': 'merge_failed', 'detail': detail}

    def _unmerged_paths(self, local_path: str) -> list[str]:
        """Repo-relative paths with conflict (unmerged) index entries."""
        result = self._run_git_subprocess(
            local_path,
            ['diff', '--name-only', '--diff-filter=U'],
        )
        if result.returncode != 0:
            return []
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def resolve_review_comment(self, repository, comment) -> None:
        self._publication_service.resolve_review_comment(repository, comment)

    def reply_to_review_comment(self, repository, comment, body: str) -> None:
        self._publication_service.reply_to_review_comment(repository, comment, body)

    def destination_branch(self, repository) -> str:
        configured_branch = text_from_attr(repository, 'destination_branch')
        if configured_branch:
            return configured_branch
        self._validate_local_path(repository)
        try:
            inferred_branch = self._infer_default_branch(repository.local_path)
        except ValueError as exc:
            raise ValueError(
                f'unable to determine destination branch for repository {repository.id}'
            ) from exc
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
            if dirty_worktree and force:
                self._make_git_ready_for_work(
                    repository.local_path,
                    destination_branch,
                    repository,
                )
            else:
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
            repository,
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
        self._run_git(local_path, ['add', '-A'], f'failed to stage changes for branch {branch_name}')
        self._unstage_and_discard_generated_artifacts(local_path, branch_name, status_output)
        validation_report_descriptions = self._unstage_and_read_validation_reports(
            local_path, branch_name, status_output
        )
        self._run_git(local_path, ['add', '-A'], f'failed to restage cleanup changes for branch {branch_name}')
        self._run_git(local_path, ['commit', '-m', commit_message], f'failed to commit changes for branch {branch_name}')
        self._ensure_clean_worktree(local_path, branch_name)
        return '\n\n'.join(validation_report_descriptions).strip()

    def _unstage_and_discard_generated_artifacts(
        self,
        local_path: str,
        branch_name: str,
        status_output: str,
    ) -> None:
        for artifact_path in self._generated_artifact_paths_from_status(status_output):
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', artifact_path],
                f'failed to exclude generated artifact path {artifact_path} from branch {branch_name}',
            )
            self._run_git(
                local_path,
                ['clean', '-fd', '--', artifact_path],
                f'failed to clean generated artifact path {artifact_path} from branch {branch_name}',
            )

    def _unstage_and_read_validation_reports(
        self,
        local_path: str,
        branch_name: str,
        status_output: str,
    ) -> list[str]:
        descriptions: list[str] = []
        for validation_report_path in self._validation_report_paths_from_status(status_output):
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', validation_report_path],
                f'failed to exclude validation report file {validation_report_path} from branch {branch_name}',
            )
            # The report is published as a task comment, not as a committed file.
            full_path = os.path.join(local_path, validation_report_path)
            description = self._validation_report_text(full_path)
            if description is None:
                self.logger.warning(
                    'validation report file was reported by git status but missing at %s', full_path
                )
            elif not description:
                self.logger.warning('validation report file was empty at %s', full_path)
            else:
                descriptions.append(description)
            self._run_git(
                local_path,
                ['clean', '-fd', '--', validation_report_path],
                f'failed to clean validation report file {validation_report_path} from branch {branch_name}',
            )
        return descriptions

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
        # Zero commits ahead has two very different causes, and the
        # operator must be able to tell them apart from the one-line
        # skip message — otherwise an already-shipped task reads like
        # "create pull request failed".
        #
        # The discriminator is the BEHIND count (commits in the
        # comparison ref the branch lacks). ``_ahead_count`` with the
        # refs swapped is exactly that, so the black-box git lib needs
        # no new method:
        #   - behind >= 1: the comparison ref advanced PAST the
        #     branch while the branch holds nothing new — the branch's
        #     commits are already contained in it, i.e. this task's
        #     pull request was already merged upstream. A completed
        #     task, not a failure or an empty-handed agent run.
        #   - behind == 0: the branch is level with the comparison
        #     ref — the agent genuinely produced no commits here.
        #
        # (An ancestor check can't discriminate: ahead == 0 already
        # implies the branch tip is contained in the comparison ref,
        # so ``--is-ancestor`` is unconditionally true here.)
        behind_count = self._ahead_count(local_path, branch_name, comparison_ref)
        if behind_count >= 1:
            raise RepositoryHasNoChangesError(
                f'branch {branch_name} is already merged into {comparison_ref} '
                f'— nothing new to open a pull request for'
            )
        raise RepositoryHasNoChangesError(
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
            # Per-task workspace clones (``~/.kato/workspaces/<task_id>/<repo>/``)
            # are owned exclusively by one task — they must STAY on the
            # task branch across publish operations so the next push /
            # PR / Files-tab open finds the correct HEAD. Without this
            # guard, the on-demand "Push" UI button would push and then
            # restore to master; the subsequent "Pull request" click
            # would then fail with "expected branch X but found master".
            if restore_workspace and not _is_per_task_workspace_clone(repository):
                self._prepare_workspace_for_task(local_path, destination_branch, repository)
        return validation_report_description

    def _prepare_workspace_for_task(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> None:
        current_branch = self._current_branch(local_path)
        if self._working_tree_status(local_path):
            current_branch = self._make_git_ready_for_work(
                local_path,
                destination_branch,
                repository,
            )
        current_branch = self._ensure_destination_branch_checked_out(
            local_path,
            destination_branch,
            current_branch,
        )
        self._validate_destination_branch_tracking_state(local_path, destination_branch)
        if self._uses_remote_destination_sync(repository):
            self._pull_destination_branch(local_path, destination_branch, repository)
        current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)

    def _prepare_workspace_for_branch(
        self,
        local_path: str,
        destination_branch: str,
        branch_name: str,
        repository=None,
    ) -> None:
        current_branch = self._current_branch(local_path)
        if self._working_tree_status(local_path):
            current_branch = self._make_git_ready_for_work(
                local_path,
                destination_branch,
                repository,
            )
        self._validate_destination_branch_tracking_state(local_path, destination_branch)
        if self._uses_remote_destination_sync(repository):
            self._fetch_origin_for_branch_preparation(local_path, repository)
        current_branch, should_sync_task_branch = self._ensure_task_branch_checked_out(
            local_path,
            destination_branch,
            branch_name,
            current_branch,
            repository=repository,
        )
        self._assert_current_branch(local_path, branch_name, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)
        if should_sync_task_branch and self._uses_remote_destination_sync(repository):
            if self._sync_checked_out_task_branch(local_path, branch_name, repository):
                current_branch = self._current_branch(local_path)
                self._assert_current_branch(local_path, branch_name, current_branch)
                self._ensure_clean_worktree(local_path, current_branch)

    def _ensure_clean_worktree(self, local_path: str, current_branch: str = '') -> None:
        status_output = self._working_tree_status(local_path)
        if not status_output:
            return
        if self._discard_only_generated_artifacts(local_path, status_output, current_branch):
            status_output = self._working_tree_status(local_path)
            if not status_output:
                return
        status_details = status_output.strip()
        self.logger.warning(
            'repository at %s still has uncommitted changes on branch %s:\n%s',
            local_path,
            current_branch or '<unknown>',
            status_details,
        )
        raise RuntimeError(
            f'repository at {local_path} has uncommitted changes on branch '
            f'{current_branch or "<unknown>"}; refusing to start a new task\n'
            f'{status_details}'
        )

    def _discard_only_generated_artifacts(
        self,
        local_path: str,
        status_output: str,
        current_branch: str,
    ) -> bool:
        generated_artifact_paths = self._generated_artifact_paths_from_status(status_output)
        validation_report_paths = self._validation_report_paths_from_status(status_output)
        removable_paths = [*generated_artifact_paths, *validation_report_paths]
        if not removable_paths:
            return False
        if not self._status_contains_only_removable_artifacts(
            status_output,
            generated_artifact_paths,
            validation_report_paths,
        ):
            return False
        if not current_branch:
            return False
        self.logger.warning(
            'discarding generated artifacts on branch %s before continuing:\n%s',
            current_branch,
            status_output.strip(),
        )
        self._run_git(
            local_path,
            ['checkout', '-f', current_branch],
            (
                f'failed to discard generated artifacts while resetting branch '
                f'{current_branch} at {local_path}'
            ),
        )
        self._run_git(
            local_path,
            ['clean', '-fd'],
            f'failed to remove generated artifacts while cleaning branch {current_branch}',
        )
        return True

    def _make_git_ready_for_work(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> str:
        include_remote_sync = self._uses_remote_destination_sync(repository)
        self.logger.info(
            'making git ready before starting work at %s: %s',
            local_path,
            git_ready_command_summary(
                destination_branch,
                include_remote_sync=include_remote_sync,
            ),
        )
        if include_remote_sync:
            self._run_git(
                local_path,
                ['fetch', 'origin'],
                f'failed to fetch origin for repository at {local_path}',
                repository,
            )
        self._run_git(
            local_path,
            ['checkout', '-f', destination_branch],
            f'failed to switch repository at {local_path} to {destination_branch}',
            repository,
        )
        if include_remote_sync:
            self._run_git(
                local_path,
                ['reset', '--hard', f'origin/{destination_branch}'],
                (
                    f'failed to reset repository at {local_path} to '
                    f'origin/{destination_branch}'
                ),
                repository,
            )
        self._run_git(
            local_path,
            ['clean', '-fd'],
            f'failed to remove untracked files while cleaning repository at {local_path}',
            repository,
        )
        current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)
        return current_branch

    @staticmethod
    def _uses_remote_destination_sync(repository) -> bool:
        return bool(
            repository is not None
            and (
                normalized_text(text_from_attr(repository, 'remote_url'))
                or normalized_text(text_from_attr(repository, 'repo_slug'))
            )
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
        repository=None,
    ) -> tuple[str, bool]:
        if current_branch == branch_name:
            return current_branch, True
        restored_branch, should_sync_task_branch = self._checkout_existing_task_branch(
            local_path,
            branch_name,
        )
        if restored_branch:
            return restored_branch, should_sync_task_branch
        current_branch = self._ensure_destination_branch_checked_out(
            local_path,
            destination_branch,
            current_branch,
        )
        # Fresh task-branch path: fast-forward the destination branch to
        # origin/<destination> before forking. Without this, a local
        # ``master`` that's behind the remote (typical immediately after
        # the previous task's PR was merged) would seed the new task
        # branch with stale code, and the agent's first commit would
        # silently re-introduce the just-merged changes on top.
        if self._uses_remote_destination_sync(repository):
            self._sync_destination_branch_to_origin(
                local_path, destination_branch, repository,
            )
        self._create_task_branch(local_path, branch_name, destination_branch)
        return self._current_branch(local_path), False

    def _sync_destination_branch_to_origin(
        self,
        local_path: str,
        destination_branch: str,
        repository,
    ) -> None:
        """Reset the local destination branch to ``origin/<destination>``.

        Idempotent and safe to call when the local branch is already at
        the remote head (the reset is a no-op). Loud failure if the
        remote ref is missing — the caller relies on a synced base.
        """
        self._run_git(
            local_path,
            ['reset', '--hard', f'origin/{destination_branch}'],
            (
                f'failed to fast-forward {destination_branch} to '
                f'origin/{destination_branch} at {local_path}'
            ),
            repository,
        )

    def _checkout_existing_task_branch(
        self,
        local_path: str,
        branch_name: str,
    ) -> tuple[str, bool]:
        local_branch_ref = f'refs/heads/{branch_name}'
        remote_branch_ref = f'refs/remotes/origin/{branch_name}'
        if self._git_reference_exists(local_path, local_branch_ref):
            self._run_git(
                local_path,
                ['checkout', branch_name],
                f'failed to switch repository at {local_path} to {branch_name}',
            )
            return self._current_branch(local_path), True
        if not self._git_reference_exists(local_path, remote_branch_ref):
            return '', False
        self._run_git(
            local_path,
            ['checkout', '-b', branch_name, f'origin/{branch_name}'],
            f'failed to restore branch {branch_name} from origin/{branch_name}',
        )
        return self._current_branch(local_path), False

    def _fetch_origin_for_branch_preparation(
        self,
        local_path: str,
        repository=None,
    ) -> None:
        self._run_git(
            local_path,
            ['fetch', 'origin'],
            f'failed to fetch origin before preparing branch at {local_path}',
            repository,
        )

    def _sync_checked_out_task_branch(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
    ) -> bool:
        remote_branch = f'origin/{branch_name}'
        if not self._git_reference_exists(local_path, remote_branch):
            return False
        self.logger.info(
            'syncing branch %s with %s before starting work',
            branch_name,
            remote_branch,
        )
        self._rebase_branch_onto_remote(local_path, branch_name, remote_branch, repository)
        return True

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

    def current_head_sha(self, repository) -> str:
        """Return ``HEAD`` SHA of ``repository``'s checkout (empty on failure).

        Public so the review-fix path can snapshot HEAD before
        spawning the agent and compare after to verify the agent
        actually committed something. Without this check, an agent
        that ran but produced no edits would still get its reply
        posted and the comment resolved if the task branch had any
        prior commits ahead of base — leading to the "kato pushed a
        follow-up update" lie even when nothing was pushed.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            return ''
        try:
            return self._git_stdout(
                local_path,
                ['rev-parse', 'HEAD'],
                f'failed to read HEAD sha for {local_path}',
            ).strip()
        except Exception:
            return ''

    def has_dirty_working_tree(self, repository) -> bool:
        """True when the repository has uncommitted edits (tracked or untracked).

        Used alongside ``current_head_sha`` for the "did the agent do
        anything?" check. A clean tree + an unmoved HEAD is the
        unambiguous "nothing happened" signal.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            return False
        try:
            return bool(self._working_tree_status(local_path).strip())
        except Exception:
            return False

    @staticmethod
    def _validation_report_paths_from_status(status_output: str) -> list[str]:
        return validation_report_paths_from_status(status_output)

    @staticmethod
    def _validation_report_text(validation_report_full_path: str) -> str | None:
        if not os.path.exists(validation_report_full_path):
            return None
        return Path(validation_report_full_path).read_text(encoding='utf-8').strip()

    @staticmethod
    def _generated_artifact_paths_from_status(status_output: str) -> list[str]:
        return generated_artifact_paths_from_status(status_output)

    @classmethod
    def _status_contains_only_removable_artifacts(
        cls,
        status_output: str,
        generated_artifact_paths: list[str],
        validation_report_paths: list[str],
    ) -> bool:
        return status_contains_only_removable_artifacts(
            status_output,
            generated_artifact_paths,
            validation_report_paths,
        )

