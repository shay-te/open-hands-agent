import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from openhands_agent.data_layers.data.fields import RepositoryFields
from openhands_agent.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from utils import build_task


class RepositoryInventoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client_repo = types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='/workspace/client',
            provider_base_url='https://bitbucket.example',
            repo_slug='client',
            aliases=['frontend'],
        )
        self.backend_repo = types.SimpleNamespace(
            id='backend',
            display_name='Backend',
            local_path='/workspace/backend',
            provider_base_url='https://github.example/api/v3',
            repo_slug='backend',
            aliases=['api'],
        )

    def test_provider_api_defaults_from_source_maps_credentials(self) -> None:
        source = types.SimpleNamespace(
            github_issues=types.SimpleNamespace(
                base_url='https://api.github.com',
                token='gh-token',
                username='gh-user',
                api_email='',
            ),
            gitlab_issues=types.SimpleNamespace(
                base_url='https://gitlab.example/api/v4',
                token='gl-token',
                username='gl-user',
                api_email='',
            ),
            bitbucket_issues=types.SimpleNamespace(
                base_url='https://api.bitbucket.org/2.0',
                token='bb-token',
                username='bb-user',
                api_email='bb-api@example.com',
            ),
        )

        defaults = RepositoryInventoryService._provider_api_defaults_from_source(source)

        self.assertEqual(defaults['github'][RepositoryFields.PROVIDER_BASE_URL], 'https://api.github.com')
        self.assertEqual(defaults['github']['token'], 'gh-token')
        self.assertEqual(defaults['github']['username'], 'gh-user')
        self.assertEqual(defaults['gitlab'][RepositoryFields.PROVIDER_BASE_URL], 'https://gitlab.example/api/v4')
        self.assertEqual(defaults['gitlab']['token'], 'gl-token')
        self.assertEqual(defaults['gitlab']['username'], 'gl-user')
        self.assertEqual(defaults['bitbucket'][RepositoryFields.PROVIDER_BASE_URL], 'https://api.bitbucket.org/2.0')
        self.assertEqual(defaults['bitbucket']['token'], 'bb-token')
        self.assertEqual(defaults['bitbucket']['username'], 'bb-user')
        self.assertEqual(defaults['bitbucket']['api_email'], 'bb-api@example.com')

    def test_validate_connections_rejects_duplicate_repository_ids(self) -> None:
        repositories = [
            types.SimpleNamespace(
                id='client',
                display_name='Client',
                local_path='.',
                repo_slug='client',
                aliases=['frontend'],
            ),
            types.SimpleNamespace(
                id='client',
                display_name='Client 2',
                local_path='.',
                repo_slug='client-2',
                aliases=['ui'],
            ),
        ]

        service = RepositoryInventoryService(repositories)

        with self.assertRaisesRegex(ValueError, 'duplicate repository id'):
            service.validate_connections()

    def test_validate_connections_rejects_duplicate_aliases(self) -> None:
        repositories = [
            types.SimpleNamespace(
                id='client',
                display_name='Client',
                local_path='.',
                repo_slug='client',
                aliases=['shared'],
            ),
            types.SimpleNamespace(
                id='backend',
                display_name='Backend',
                local_path='.',
                repo_slug='backend',
                aliases=['shared'],
            ),
        ]

        service = RepositoryInventoryService(repositories)

        with self.assertRaisesRegex(ValueError, 'duplicate repository alias'):
            service.validate_connections()

    def test_resolve_task_repositories_matches_multiple_repositories_from_task_text(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])
        task = build_task(description='Update client and backend endpoints')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client', 'backend'])

    def test_resolve_task_repositories_uses_repo_tags_before_task_text(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])
        task = build_task(
            summary='Update client and backend endpoints',
            description='This should not drive selection when tags are present.',
            tags=[f'{RepositoryFields.REPOSITORY_TAG_PREFIX}backend'],
        )

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['backend'])

    def test_resolve_task_repositories_matches_multiple_repo_tags(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])
        task = build_task(
            tags=[
                f'{RepositoryFields.REPOSITORY_TAG_PREFIX}client',
                f'{RepositoryFields.REPOSITORY_TAG_PREFIX}backend',
            ]
        )

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client', 'backend'])

    def test_resolve_task_repositories_rejects_unmatched_repo_tags(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])

        with self.assertRaisesRegex(
            ValueError,
            'no configured repository matched repo tags on task PROJ-1',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{RepositoryFields.REPOSITORY_TAG_PREFIX}missing'])
            )

    def test_resolve_task_repositories_rejects_partial_substrings(self) -> None:
        repository = types.SimpleNamespace(
            id='myrepo',
            display_name='My Repository',
            local_path='/workspace/myrepo',
            repo_slug='myrepo',
            aliases=['myrepo'],
        )
        service = RepositoryInventoryService([repository])

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(build_task(description='myrepo-extra needs changes'))

    def test_get_repository_returns_known_repository_and_rejects_unknown(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])

        self.assertIs(service.get_repository('backend'), self.backend_repo)
        with self.assertRaisesRegex(ValueError, 'unknown repository id: missing'):
            service.get_repository('missing')

    def test_discovers_repositories_from_root_and_ignores_configured_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            generic_repo = projects_root / 'project'
            ignored_repo = projects_root / 'ignored-repo'
            self._create_git_repository(
                generic_repo,
                'git@bitbucket.org:acme/ob-love-admin-client.git',
            )
            self._create_git_repository(
                ignored_repo,
                'git@bitbucket.org:acme/ignored.git',
            )

            service = RepositoryInventoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    ignored_repository_folders='ignored-repo',
                ),
            )

        self.assertEqual([repository.id for repository in service.repositories], ['ob-love-admin-client'])
        self.assertEqual(service.repositories[0].display_name, 'Ob Love Admin Client')
        self.assertEqual(service.repositories[0].repo_slug, 'ob-love-admin-client')
        self.assertEqual(service.repositories[0].aliases, ['project', 'ob-love-admin-client'])

    @staticmethod
    def _create_git_repository(path: Path, remote_url: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['git', 'init', '-q'],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ['git', 'remote', 'add', 'origin', remote_url],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
