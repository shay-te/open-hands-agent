from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.fields import PullRequestFields
from utils import build_task, build_test_cfg


class RepositoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_validate_connections_rejects_duplicate_repository_ids(self) -> None:
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

        service = RepositoryService(repositories, 3)

        with self.assertRaisesRegex(ValueError, 'duplicate repository id'):
            service.validate_connections()

    def test_validate_connections_rejects_duplicate_aliases(self) -> None:
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

        service = RepositoryService(repositories, 3)

        with self.assertRaisesRegex(ValueError, 'duplicate repository alias'):
            service.validate_connections()

    def test_resolves_multiple_repositories_from_task_text(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(description='Update client and api endpoints')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client', 'backend'])

    def test_discovers_repositories_from_root_and_matches_task_folder_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            client_repo = projects_root / 'client-app'
            backend_repo = projects_root / 'backend-service'
            self._create_git_repository(
                client_repo,
                'git@github.com:acme/client.git',
            )
            self._create_git_repository(
                backend_repo,
                'git@github.com:acme/backend.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                ),
                3,
            )

            repositories = service.resolve_task_repositories(
                build_task(description='Work in backend-service only')
            )

        self.assertEqual([repository.id for repository in repositories], ['backend-service'])
        self.assertEqual(repositories[0].repo_slug, 'backend')

    def test_discovers_repositories_from_root_ignoring_configured_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            dev_repo = projects_root / 'ob-love-admin-client'
            ignored_repo = projects_root / 'ob-love-admin-client-new'
            self._create_git_repository(
                dev_repo,
                'git@bitbucket.org:acme/ob-love-admin-client.git',
            )
            self._create_git_repository(
                ignored_repo,
                'git@bitbucket.org:acme/ob-love-admin-client.git',
            )

            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    ignored_repository_folders='ob-love-admin-client-new',
                ),
                3,
            )

        self.assertEqual([repository.id for repository in service.repositories], ['ob-love-admin-client'])

    def test_raises_when_no_repository_matches_task_text(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(description='Update mobile application')

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(task)

    def test_prepare_task_repositories_sets_resolved_destination_branch(self) -> None:
        repository = self.cfg.openhands_agent.repositories[0]
        repository.destination_branch = ''
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='refs/remotes/origin/master\n'),
        ):
            prepared_repositories = service.prepare_task_repositories([repository])

        self.assertEqual(prepared_repositories[0].destination_branch, 'master')


    def test_does_not_match_repository_alias_inside_hyphenated_word(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(description='Improve non-client rendering flow')

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(task)

    def test_matches_repository_alias_surrounded_by_punctuation(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(description='Please update (backend), then circle back.')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['backend'])

    def test_matches_repository_by_display_name_from_summary(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        task = build_task(summary='Client polish pass', description='Tighten UX copy.')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client'])

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

    def test_destination_branch_raises_when_git_cannot_infer_default_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        repository = self.cfg.openhands_agent.repositories[0]

        with patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=1, stdout=''),
        ):
            with self.assertRaisesRegex(
                ValueError,
                'unable to determine destination branch for repository client',
            ):
                service.destination_branch(repository)

    def test_build_branch_name_uses_task_id(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        branch_name = service.build_branch_name(build_task(task_id='UNA-222'), self.backend_repo)

        self.assertEqual(branch_name, 'UNA-222')

    def test_create_pull_request_includes_repository_id_and_inferred_branch(self) -> None:
        repository = self.cfg.openhands_agent.repositories[0]

        with patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='refs/remotes/origin/master\n'),
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            result = service.create_pull_request(
                repository,
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1/client',
                description='Ready',
            )

        self.assertEqual(result[PullRequestFields.REPOSITORY_ID], 'client')
        self.assertEqual(result[PullRequestFields.ID], 'feature/proj-1/client')
        self.assertEqual(result[PullRequestFields.DESTINATION_BRANCH], 'master')
        self.assertIn('/pull-requests/new?', result[PullRequestFields.URL])
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/client')

    def test_validate_connections_checks_local_paths(self) -> None:
        with patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            with self.assertRaisesRegex(ValueError, 'missing local repository path'):
                service.validate_connections()

    def test_prepare_task_repositories_raises_when_local_path_is_missing(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            with self.assertRaisesRegex(ValueError, 'missing local repository path'):
                service.prepare_task_repositories([self.cfg.openhands_agent.repositories[0]])

    def test_validate_connections_requires_at_least_one_repository(self) -> None:
        service = RepositoryService([], 3)

        with self.assertRaisesRegex(ValueError, 'at least one repository must be configured'):
            service.validate_connections()

    def test_list_pull_request_comments_returns_empty_without_provider_api(self) -> None:
        repository = self.cfg.openhands_agent.repositories[0]
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        comments = service.list_pull_request_comments(repository, '17')

        self.assertEqual(comments, [])

    @staticmethod
    def _create_git_repository(path: Path, remote_url: str) -> None:
        git_dir = path / '.git'
        git_dir.mkdir(parents=True)
        (git_dir / 'config').write_text(
            '[core]\n'
            '\trepositoryformatversion = 0\n'
            '[remote "origin"]\n'
            f'\turl = {remote_url}\n',
            encoding='utf-8',
        )
