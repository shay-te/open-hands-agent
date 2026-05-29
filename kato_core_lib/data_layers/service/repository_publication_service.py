from __future__ import annotations

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.text_utils import normalized_text


class RepositoryPublicationService(Service):
    """Publish repository branch updates, pull requests, and review-fix pushes."""
    def __init__(self, repository_service, max_retries: int, logger=None) -> None:
        self._repository_service = repository_service
        self._max_retries = max_retries
        self.logger = logger or configure_logger(self.__class__.__name__)
        # Repos that have already failed pull-request API setup are remembered
        # here so the per-scan polling loop doesn't re-log the same skip
        # message every cycle. Cleared only on process restart, which is fine
        # — operator config changes need a restart anyway.
        self._pr_api_skipped_repos: set[str] = set()

    def create_pull_request(
        self,
        repository,
        title: str,
        source_branch: str,
        description: str = '',
        commit_message: str = '',
    ) -> dict[str, str]:
        destination_branch = self._repository_service.destination_branch(repository)
        try:
            validation_report_description = self._repository_service._publish_branch_updates(
                repository.local_path,
                source_branch,
                destination_branch,
                normalized_text(commit_message) or f'Implement {source_branch}',
                repository,
                restore_workspace=False,
            )
            normalized_validation_report_description = normalized_text(
                validation_report_description
            )
            pull_request_description = (
                normalized_validation_report_description or normalized_text(description)
            )
            if normalized_validation_report_description:
                self.logger.info(
                    'using validation report as pull request description for repository %s',
                    repository.id,
                )
            elif normalized_text(description):
                self.logger.warning(
                    'validation report was missing or empty for repository %s; '
                    'falling back to structured pull request description',
                    repository.id,
                )
            pull_request = self._repository_service._pull_request_data_access(
                repository,
            ).create_pull_request(
                title=title,
                source_branch=source_branch,
                destination_branch=destination_branch,
                description=pull_request_description,
            )
            return {
                PullRequestFields.REPOSITORY_ID: repository.id,
                PullRequestFields.ID: str(pull_request.get(PullRequestFields.ID, '') or ''),
                PullRequestFields.TITLE: str(
                    pull_request.get(PullRequestFields.TITLE, '') or title
                ),
                PullRequestFields.URL: str(
                    pull_request.get(PullRequestFields.URL, '')
                    or self._repository_service._review_url(
                        repository,
                        source_branch,
                        destination_branch,
                    )
                ),
                PullRequestFields.SOURCE_BRANCH: source_branch,
                PullRequestFields.DESTINATION_BRANCH: destination_branch,
                PullRequestFields.DESCRIPTION: pull_request_description,
            }
        finally:
            self._restore_workspace_after_publication(repository, destination_branch)

    def publish_review_fix(
        self,
        repository,
        branch_name: str,
        commit_message: str = '',
    ) -> None:
        self._repository_service._publish_repository_branch(
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
        if not self._ensure_pr_api_or_log_once(repository, 'comment polling'):
            return []
        return self._repository_service._pull_request_data_access(
            repository,
        ).list_pull_request_comments(pull_request_id)

    def find_pull_requests(
        self,
        repository,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        if not self._ensure_pr_api_or_log_once(repository, 'lookup'):
            return []
        return self._repository_service._pull_request_data_access(
            repository,
        ).find_pull_requests(
            source_branch=source_branch,
            title_prefix=title_prefix,
        )

    def _ensure_pr_api_or_log_once(self, repository, operation: str) -> bool:
        """Prepare the PR API for ``repository`` or log+remember the skip.

        Returns True on success, False if the API can't be prepared. The skip
        is logged at most once per repository per process — repeated failures
        on subsequent scan ticks stay silent so a workspace with many repos
        that don't have PR tokens (e.g. dependency repos like email-core-lib)
        doesn't flood the logs every poll cycle.
        """
        try:
            self._repository_service._prepare_pull_request_api(repository)
        except Exception as exc:
            if repository.id not in self._pr_api_skipped_repos:
                self._pr_api_skipped_repos.add(repository.id)
                self.logger.info(
                    'skipping pull request %s for repository %s: %s',
                    operation,
                    repository.id,
                    exc,
                )
            return False
        return True

    def resolve_review_comment(self, repository, comment) -> None:
        self._repository_service._prepare_pull_request_api(repository)
        self._repository_service._pull_request_data_access(repository).resolve_review_comment(
            comment
        )

    def reply_to_review_comment(self, repository, comment, body: str) -> None:
        self._repository_service._prepare_pull_request_api(repository)
        self._repository_service._pull_request_data_access(repository).reply_to_review_comment(
            comment,
            body,
        )

    def _restore_workspace_after_publication(self, repository, destination_branch: str) -> None:
        if self._is_per_task_workspace_clone(repository):
            return
        try:
            self._repository_service.restore_task_repositories([repository], force=True)
        except Exception:
            self.logger.exception(
                'failed to restore repository %s to %s after publication',
                repository.id,
                destination_branch,
            )

    @staticmethod
    def _is_per_task_workspace_clone(repository) -> bool:
        # Lazy import to avoid the module-level circular dependency:
        # repository_service imports RepositoryPublicationService from this
        # module, so this module can't import from repository_service at
        # load time. The shared definition lives there.
        from kato_core_lib.data_layers.service.repository_service import (
            _is_per_task_workspace_clone,
        )
        return _is_per_task_workspace_clone(repository)
