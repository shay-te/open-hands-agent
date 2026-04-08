import types
import unittest
from unittest.mock import Mock

from kato.data_layers.data.fields import PullRequestFields, RepositoryFields
from kato.data_layers.service.repository_publication_service import (
    RepositoryPublicationService,
)


class RepositoryPublicationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_service = Mock()
        self.repository_service.destination_branch.return_value = 'main'
        self.repository_service._publish_branch_updates.return_value = (
            'Validation report:\nAll good'
        )
        self.repository_service.restore_task_repositories = Mock()
        self.repository_service._pull_request_data_access.return_value = Mock()
        self.repository_service._pull_request_data_access.return_value.create_pull_request.return_value = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PR title',
            PullRequestFields.URL: 'https://example.com/pr/17',
        }
        self.service = RepositoryPublicationService(self.repository_service, 3)

    def test_create_pull_request_uses_validation_report_as_description(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='/workspace/client',
            provider_base_url='https://bitbucket.org',
            owner='workspace',
            repo_slug='client',
            token='token',
            destination_branch='main',
            **{RepositoryFields.BITBUCKET_API_EMAIL: 'shay.te@gmail.com'},
        )

        pull_request = self.service.create_pull_request(
            repository,
            'client: update flow',
            'feature/client',
            description='fallback description',
            commit_message='Implement client',
        )

        self.repository_service._publish_branch_updates.assert_called_once_with(
            '/workspace/client',
            'feature/client',
            'main',
            'Implement client',
            repository,
            restore_workspace=False,
        )
        self.repository_service.restore_task_repositories.assert_called_once_with(
            [repository],
            force=True,
        )
        self.repository_service._pull_request_data_access.assert_called_once_with(repository)
        self.assertEqual(
            pull_request[PullRequestFields.DESCRIPTION],
            'Validation report:\nAll good',
        )

    def test_create_pull_request_keeps_success_even_when_restore_warnings_occur(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='/workspace/client',
            provider_base_url='https://bitbucket.org',
            owner='workspace',
            repo_slug='client',
            token='token',
            destination_branch='main',
            **{RepositoryFields.BITBUCKET_API_EMAIL: 'shay.te@gmail.com'},
        )
        self.repository_service.restore_task_repositories.side_effect = RuntimeError(
            'dirty worktree'
        )

        pull_request = self.service.create_pull_request(
            repository,
            'client: update flow',
            'feature/client',
            description='fallback description',
            commit_message='Implement client',
        )

        self.assertEqual(pull_request[PullRequestFields.ID], '17')
        self.repository_service.restore_task_repositories.assert_called_once_with(
            [repository],
            force=True,
        )

    def test_publish_review_fix_delegates_to_repository_service(self) -> None:
        repository = types.SimpleNamespace(id='client')

        self.service.publish_review_fix(repository, 'feature/client', commit_message='Fix')

        self.repository_service._publish_repository_branch.assert_called_once_with(
            repository,
            'feature/client',
            commit_message='Fix',
            default_commit_message='Address review comments',
        )
