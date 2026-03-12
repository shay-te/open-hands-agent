import types
import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.fields import PullRequestFields
from utils import build_task, build_test_cfg


class RepositoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_rejects_duplicate_repository_ids(self) -> None:
        repositories = [
            types.SimpleNamespace(
                id='client',
                display_name='Client',
                local_path='.',
                provider_base_url='https://bitbucket.example',
                token='token',
                owner='workspace',
                repo_slug='repo',
                destination_branch='main',
                aliases=['frontend'],
            ),
            types.SimpleNamespace(
                id='client',
                display_name='Client 2',
                local_path='.',
                provider_base_url='https://github.example/api/v3',
                token='token',
                owner='workspace',
                repo_slug='repo-2',
                destination_branch='main',
                aliases=['ui'],
            ),
        ]

        with self.assertRaisesRegex(ValueError, 'duplicate repository id'):
            RepositoryService(repositories, 3)

    def test_rejects_duplicate_aliases(self) -> None:
        repositories = [
            types.SimpleNamespace(
                id='client',
                display_name='Client',
                local_path='.',
                provider_base_url='https://bitbucket.example',
                token='token',
                owner='workspace',
                repo_slug='repo',
                destination_branch='main',
                aliases=['shared'],
            ),
            types.SimpleNamespace(
                id='backend',
                display_name='Backend',
                local_path='.',
                provider_base_url='https://github.example/api/v3',
                token='token',
                owner='workspace',
                repo_slug='backend',
                destination_branch='main',
                aliases=['shared'],
            ),
        ]

        with self.assertRaisesRegex(ValueError, 'duplicate repository alias'):
            RepositoryService(repositories, 3)

    def test_resolves_multiple_repositories_from_task_text(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(description='Update client and api endpoints')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client', 'backend'])

    def test_raises_when_no_repository_matches_task_text(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(description='Update mobile application')

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(task)

    def test_prefers_configured_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        self.assertEqual(service.destination_branch(self.backend_repo), 'main')

    @property
    def backend_repo(self):
        return self.cfg.openhands_agent.repositories[1]

    def test_infers_destination_branch_from_local_git_default_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        repository = self.cfg.openhands_agent.repositories[0]

        with patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='refs/remotes/origin/master\n'),
        ):
            self.assertEqual(service.destination_branch(repository), 'master')

    def test_create_pull_request_includes_repository_id_and_inferred_branch(self) -> None:
        repository = self.cfg.openhands_agent.repositories[0]
        pull_request = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
        }

        with patch(
            'openhands_agent.data_layers.service.repository_service.build_pull_request_client'
        ) as mock_build_client, patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='refs/remotes/origin/master\n'),
        ):
            mock_build_client.return_value.create_pull_request.return_value = pull_request
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            result = service.create_pull_request(
                repository,
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1/client',
                description='Ready',
            )

        self.assertEqual(result[PullRequestFields.REPOSITORY_ID], 'client')
        self.assertEqual(result[PullRequestFields.DESTINATION_BRANCH], 'master')

    def test_validate_connections_checks_local_paths(self) -> None:
        with patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            with self.assertRaisesRegex(ValueError, 'missing local repository path'):
                service.validate_connections()
