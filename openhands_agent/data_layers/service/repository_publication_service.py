from __future__ import annotations

from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data.fields import PullRequestFields
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.text_utils import normalized_text


class RepositoryPublicationService(Service):
    def __init__(self, repository_service, max_retries: int, logger=None) -> None:
        self._repository_service = repository_service
        self._max_retries = max_retries
        self.logger = logger or configure_logger(self.__class__.__name__)

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
            self._repository_service._prepare_workspace_for_task(
                repository.local_path,
                destination_branch,
                repository,
            )

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
        try:
            self._repository_service._prepare_pull_request_api(repository)
        except Exception as exc:
            self.logger.info(
                'skipping pull request comment polling for repository %s: %s',
                repository.id,
                exc,
            )
            return []
        return self._repository_service._pull_request_data_access(
            repository,
        ).list_pull_request_comments(pull_request_id)

    def resolve_review_comment(self, repository, comment) -> None:
        self._repository_service._prepare_pull_request_api(repository)
        self._repository_service._pull_request_data_access(repository).resolve_review_comment(
            comment
        )
