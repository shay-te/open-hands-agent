from pathlib import Path
import tempfile
import types
import unittest
import base64
from unittest.mock import Mock, patch


from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.fields import PullRequestFields, ReviewCommentFields
from utils import build_review_comment, build_task, build_test_cfg


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

    def test_discovers_repository_from_generic_mount_folder_using_repo_slug_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            mounted_repo = projects_root / 'project'
            self._create_git_repository(
                mounted_repo,
                'git@bitbucket.org:acme/ob-love-admin-client.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                ),
                3,
            )

        self.assertEqual([repository.id for repository in service.repositories], ['ob-love-admin-client'])
        self.assertEqual(service.repositories[0].display_name, 'Ob Love Admin Client')
        self.assertEqual(service.repositories[0].repo_slug, 'ob-love-admin-client')
        self.assertEqual(service.repositories[0].aliases, ['project', 'ob-love-admin-client'])

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
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='refs/remotes/origin/master\n', stderr=''),
                Mock(returncode=0, stdout='master\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='master\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ):
            prepared_repositories = service.prepare_task_repositories([repository])

        self.assertEqual(prepared_repositories[0].destination_branch, 'master')

    def test_prepare_task_repositories_switches_clean_repository_to_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            prepared_repositories = service.prepare_task_repositories([self.backend_repo])

        self.assertEqual(prepared_repositories[0].destination_branch, 'main')
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'pull', '--ff-only', 'origin', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_repositories_pulls_latest_destination_branch_before_next_task(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            service.prepare_task_repositories([self.backend_repo])

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'pull', '--ff-only', 'origin', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_restore_task_repositories_switches_clean_repository_back_to_destination_branch(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='.',
            destination_branch='master',
        )
        service = RepositoryService([], 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/client\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            restored_repositories = service.restore_task_repositories([repository])

        self.assertEqual(restored_repositories, [repository])
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'master'],
            ],
        )

    def test_restore_task_repositories_skips_dirty_repository(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='.',
            destination_branch='master',
        )
        service = RepositoryService([], 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/client\n', stderr=''),
                Mock(returncode=0, stdout=' M app.py\n', stderr=''),
            ],
        ) as mock_run:
            restored_repositories = service.restore_task_repositories([repository])

        self.assertEqual(restored_repositories, [repository])
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_creates_new_task_branch_from_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ) as mock_validate_destination, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            return_value=False,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='UNA-2398\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            service.prepare_task_branches([self.backend_repo], {'backend': 'UNA-2398'})

        mock_validate_destination.assert_called_once_with('.', 'main')
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', '-b', 'UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_checks_out_existing_local_task_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        def reference_exists(_local_path: str, reference: str) -> bool:
            return reference == 'refs/heads/UNA-2398'

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            side_effect=reference_exists,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='UNA-2398\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            service.prepare_task_branches([self.backend_repo], {'backend': 'UNA-2398'})

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_restores_branch_from_origin_when_local_branch_is_missing(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        def reference_exists(_local_path: str, reference: str) -> bool:
            return reference == 'refs/remotes/origin/UNA-2398'

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            side_effect=reference_exists,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='UNA-2398\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            service.prepare_task_branches([self.backend_repo], {'backend': 'UNA-2398'})

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', '-b', 'UNA-2398', 'origin/UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_repositories_raises_when_checkout_does_not_leave_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            ],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'repository at \\. is on branch feature/proj-1/backend instead of main',
            ):
                service.prepare_task_repositories([self.backend_repo])

    def test_prepare_task_repositories_rejects_dirty_repository_before_next_task(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
                Mock(returncode=0, stdout=' M app.py\n', stderr=''),
            ],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'repository at \\. has uncommitted changes on branch feature/proj-1/backend; '
                'refusing to start a new task',
            ):
                service.prepare_task_repositories([self.backend_repo])

    def test_prepare_task_repositories_rejects_destination_branch_with_local_only_commits(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='2 0\n', stderr=''),
            ],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'destination branch main at \\. has 2 local commit\\(s\\) not on origin/main; '
                'refusing to start a new task',
            ):
                service.prepare_task_repositories([self.backend_repo])

    def test_prepare_task_repositories_allows_destination_branch_behind_remote(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='0 3\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ):
            prepared_repositories = service.prepare_task_repositories([self.backend_repo])

        self.assertEqual(prepared_repositories[0].destination_branch, 'main')

    def test_prepare_task_repositories_enriches_discovered_repository_with_provider_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            repo_path = projects_root / 'ob-love-admin-client'
            self._create_git_repository(
                repo_path,
                'git@bitbucket.org:shacoshe/ob-love-admin-client.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    github_issues=types.SimpleNamespace(base_url='', token=''),
                    gitlab_issues=types.SimpleNamespace(base_url='', token=''),
                    bitbucket_issues=types.SimpleNamespace(
                        base_url='https://api.bitbucket.org/2.0',
                        token='bb-token',
                    ),
                ),
                3,
            )

            with patch(
                'openhands_agent.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
            ), patch(
                'openhands_agent.data_layers.service.repository_service.subprocess.run',
                side_effect=[
                    Mock(returncode=0, stdout='refs/remotes/origin/main\n', stderr=''),
                    Mock(returncode=0, stdout='main\n', stderr=''),
                    Mock(returncode=0, stdout='', stderr=''),
                    Mock(returncode=0, stdout='', stderr=''),
                    Mock(returncode=0, stdout='main\n', stderr=''),
                    Mock(returncode=0, stdout='', stderr=''),
                ],
            ):
                prepared_repositories = service.prepare_task_repositories([service.repositories[0]])

        prepared_repository = prepared_repositories[0]
        self.assertEqual(prepared_repository.provider_base_url, 'https://api.bitbucket.org/2.0')
        self.assertEqual(prepared_repository.token, 'bb-token')
        self.assertEqual(prepared_repository.destination_branch, 'main')

    def test_prepare_task_repositories_raises_when_pull_request_api_token_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            repo_path = projects_root / 'ob-love-admin-client'
            self._create_git_repository(
                repo_path,
                'git@bitbucket.org:shacoshe/ob-love-admin-client.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    github_issues=types.SimpleNamespace(base_url='', token=''),
                    gitlab_issues=types.SimpleNamespace(base_url='', token=''),
                    bitbucket_issues=types.SimpleNamespace(
                        base_url='https://api.bitbucket.org/2.0',
                        token='',
                    ),
                ),
                3,
            )

            with patch(
                'openhands_agent.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    'missing pull request API token for repository ob-love-admin-client',
                ):
                    service.prepare_task_repositories([service.repositories[0]])


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
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='refs/remotes/origin/master\n'),
        ):
            self.assertEqual(service.destination_branch(repository), 'master')

    def test_destination_branch_raises_when_git_cannot_infer_default_branch(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
        repository = self.cfg.openhands_agent.repositories[0]

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
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

    def test_create_pull_request_uses_provider_api_and_includes_repository_metadata(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        data_access.create_pull_request.return_value = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://bitbucket.org/workspace/repo/pull-requests/17',
        }

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
        ) as mock_prepare_branch, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_workspace_for_task',
        ) as mock_prepare_workspace, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
            return_value=data_access,
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            result = service.create_pull_request(
                repository,
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1/client',
                description='Ready',
                commit_message='Implement PROJ-1',
            )

        self.assertEqual(result[PullRequestFields.REPOSITORY_ID], 'backend')
        self.assertEqual(result[PullRequestFields.ID], '17')
        self.assertEqual(result[PullRequestFields.DESTINATION_BRANCH], 'main')
        self.assertEqual(
            result[PullRequestFields.URL],
            'https://bitbucket.org/workspace/repo/pull-requests/17',
        )
        data_access.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/client',
            destination_branch='main',
            description='Ready',
        )
        mock_prepare_branch.assert_called_once_with(
            '.',
            'feature/proj-1/client',
            'main',
            'Implement PROJ-1',
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/client', repository)
        mock_prepare_workspace.assert_called_once_with('.', 'main', repository)

    def test_create_pull_request_returns_to_destination_branch_even_when_pr_creation_fails(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        data_access.create_pull_request.side_effect = RuntimeError('provider down')

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_workspace_for_task',
        ) as mock_prepare_workspace, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
            return_value=data_access,
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            with self.assertRaisesRegex(RuntimeError, 'provider down'):
                service.create_pull_request(
                    repository,
                    title='PROJ-1: Fix bug',
                    source_branch='feature/proj-1/client',
                    description='Ready',
                    commit_message='Implement PROJ-1',
                )

        mock_prepare_workspace.assert_called_once_with('.', 'main', repository)

    def test_create_pull_request_returns_to_destination_branch_even_when_push_fails(self) -> None:
        repository = self.backend_repo

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_workspace_for_task',
        ) as mock_prepare_workspace, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
            side_effect=RuntimeError('push failed'),
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            with self.assertRaisesRegex(RuntimeError, 'push failed'):
                service.create_pull_request(
                    repository,
                    title='PROJ-1: Fix bug',
                    source_branch='feature/proj-1/client',
                    description='Ready',
                    commit_message='Implement PROJ-1',
                )

        mock_prepare_workspace.assert_called_once_with('.', 'main', repository)

    def test_create_pull_request_commits_remaining_changes_before_push(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        data_access.create_pull_request.return_value = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://github.example/pull/17',
        }
        subprocess_results = [
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(returncode=0, stdout=' M app.py\n?? tests/test_app.py\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='[feature/proj-1/backend abc123] Implement PROJ-1\n', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='1\n', stderr=''),
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
        ]

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.exists',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.remove',
        ) as mock_remove, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
            return_value=data_access,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_results,
        ) as mock_run:
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            service.create_pull_request(
                repository,
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1/backend',
                description='Ready',
                commit_message='Implement PROJ-1',
            )

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'add', '-A'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'commit', '-m', 'Implement PROJ-1'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--verify', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-list', '--count', 'main..feature/proj-1/backend'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'pull', '--ff-only', 'origin', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/backend', repository)

    def test_create_pull_request_excludes_validation_report_from_commit(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        data_access.create_pull_request.return_value = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://github.example/pull/17',
        }
        subprocess_results = [
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(
                returncode=0,
                stdout=' M app.py\n?? validation_report.md\n?? tests/test_app.py\n',
                stderr='',
            ),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='[feature/proj-1/backend abc123] Implement PROJ-1\n', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='1\n', stderr=''),
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
        ]

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
            return_value=data_access,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_results,
        ) as mock_run:
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            service.create_pull_request(
                repository,
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1/backend',
                description='Ready',
                commit_message='Implement PROJ-1',
            )

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'add', '-A'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'reset', 'HEAD', '--', 'validation_report.md'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'commit', '-m', 'Implement PROJ-1'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--verify', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-list', '--count', 'main..feature/proj-1/backend'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'pull', '--ff-only', 'origin', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/backend', repository)
        mock_remove.assert_called_once_with('./validation_report.md')

    def test_create_pull_request_rejects_branch_without_committed_changes(self) -> None:
        repository = self.backend_repo
        subprocess_results = [
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='0\n', stderr=''),
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
        ]

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_results,
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            with self.assertRaisesRegex(
                RuntimeError,
                'branch feature/proj-1/backend has no committed changes ahead of main',
            ):
                service.create_pull_request(
                    repository,
                    title='PROJ-1: Fix bug',
                    source_branch='feature/proj-1/backend',
                    description='Ready',
                    commit_message='Implement PROJ-1',
                )

    def test_validate_connections_checks_local_paths(self) -> None:
        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            with self.assertRaisesRegex(ValueError, 'missing local repository path'):
                service.validate_connections()

    def test_validate_connections_checks_git_access_for_single_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            repo_path = projects_root / 'project'
            self._create_git_repository(
                repo_path,
                'https://bitbucket.org/workspace/project.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    github_issues=types.SimpleNamespace(base_url='', token=''),
                    gitlab_issues=types.SimpleNamespace(base_url='', token=''),
                    bitbucket_issues=types.SimpleNamespace(
                        base_url='https://api.bitbucket.org/2.0',
                        token='bb-token',
                    ),
                ),
                3,
            )
            expected_header = 'Authorization: Basic ' + base64.b64encode(
                b'workspace:bb-token'
            ).decode('ascii')

            with patch(
                'openhands_agent.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'openhands_agent.data_layers.service.repository_service.subprocess.run',
                return_value=Mock(returncode=0, stdout='refs/heads/main\n', stderr=''),
            ) as mock_run:
                service.validate_connections()

        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
                '-c',
                f'http.extraHeader={expected_header}',
                '-c',
                f'safe.directory={repo_path.resolve()}',
                '-C',
                str(repo_path.resolve()),
                'ls-remote',
                '--heads',
                'origin',
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_validate_connections_checks_git_access_for_each_repository_in_parent_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            client_repo = projects_root / 'client-app'
            backend_repo = projects_root / 'backend-service'
            self._create_git_repository(
                client_repo,
                'https://bitbucket.org/workspace/client-app.git',
            )
            self._create_git_repository(
                backend_repo,
                'https://bitbucket.org/workspace/backend-service.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    github_issues=types.SimpleNamespace(base_url='', token=''),
                    gitlab_issues=types.SimpleNamespace(base_url='', token=''),
                    bitbucket_issues=types.SimpleNamespace(
                        base_url='https://api.bitbucket.org/2.0',
                        token='bb-token',
                    ),
                ),
                3,
            )
            expected_header = 'Authorization: Basic ' + base64.b64encode(
                b'workspace:bb-token'
            ).decode('ascii')

            with patch(
                'openhands_agent.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'openhands_agent.data_layers.service.repository_service.subprocess.run',
                side_effect=[
                    Mock(returncode=0, stdout='refs/heads/main\n', stderr=''),
                    Mock(returncode=0, stdout='refs/heads/main\n', stderr=''),
                ],
            ) as mock_run:
                service.validate_connections()

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                [
                    'git',
                    '-c',
                    f'http.extraHeader={expected_header}',
                    '-c',
                    f'safe.directory={backend_repo.resolve()}',
                    '-C',
                    str(backend_repo.resolve()),
                    'ls-remote',
                    '--heads',
                    'origin',
                ],
                [
                    'git',
                    '-c',
                    f'http.extraHeader={expected_header}',
                    '-c',
                    f'safe.directory={client_repo.resolve()}',
                    '-C',
                    str(client_repo.resolve()),
                    'ls-remote',
                    '--heads',
                    'origin',
                ],
            ],
        )
        self.assertEqual(
            [call.kwargs['env']['GIT_TERMINAL_PROMPT'] for call in mock_run.call_args_list],
            ['0', '0'],
        )

    def test_git_http_auth_header_falls_back_to_x_token_auth_for_bitbucket_without_owner(self) -> None:
        repository = types.SimpleNamespace(
            provider='bitbucket',
            owner='',
            remote_url='https://bitbucket.org/workspace/project.git',
            token='bb-token',
        )

        header = RepositoryService._git_http_auth_header(repository)

        expected_header = 'Authorization: Basic ' + base64.b64encode(
            b'x-token-auth:bb-token'
        ).decode('ascii')
        self.assertEqual(header, expected_header)

    def test_validate_connections_stops_when_git_permissions_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            repo_path = projects_root / 'project'
            self._create_git_repository(
                repo_path,
                'https://x-token-auth@bitbucket.org/workspace/project.git',
            )
            service = RepositoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    github_issues=types.SimpleNamespace(base_url='', token=''),
                    gitlab_issues=types.SimpleNamespace(base_url='', token=''),
                    bitbucket_issues=types.SimpleNamespace(
                        base_url='https://api.bitbucket.org/2.0',
                        token='bb-token',
                    ),
                ),
                3,
            )

            with patch(
                'openhands_agent.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'openhands_agent.data_layers.service.repository_service.subprocess.run',
                return_value=Mock(
                    returncode=128,
                    stdout='',
                    stderr=(
                        "fatal: could not read Password for "
                        "'https://x-token-auth@bitbucket.org': terminal prompts disabled"
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"\[Error\] .*/project missing git permissions\. cannot work\. fatal: could not read Password for 'https://x-token-auth@bitbucket.org': terminal prompts disabled",
                ):
                    service.validate_connections()

    def test_prepare_task_repositories_raises_when_local_path_is_missing(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            with self.assertRaisesRegex(ValueError, 'missing local repository path'):
                service.prepare_task_repositories([self.cfg.openhands_agent.repositories[0]])

    def test_validate_connections_requires_git_executable(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value=None,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'git executable is required but was not found on PATH',
            ):
                service.validate_connections()

    def test_validate_connections_requires_ssh_auth_sock_for_ssh_remote(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='.',
            provider='bitbucket',
            provider_base_url='https://api.bitbucket.org/2.0',
            token='token',
            owner='workspace',
            repo_slug='repo',
            remote_url='git@bitbucket.org:workspace/repo.git',
            destination_branch='main',
            aliases=['frontend'],
        )
        service = RepositoryService([repository], 3)

        with patch.dict(
            'openhands_agent.data_layers.service.repository_service.os.environ',
            {},
            clear=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ):
            with self.assertRaisesRegex(
                ValueError,
                'repository client uses an SSH git remote but SSH_AUTH_SOCK is not configured',
            ):
                service.validate_connections()

    def test_validate_connections_requires_existing_ssh_auth_sock_path_for_ssh_remote(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='.',
            provider='bitbucket',
            provider_base_url='https://api.bitbucket.org/2.0',
            token='token',
            owner='workspace',
            repo_slug='repo',
            remote_url='git@bitbucket.org:workspace/repo.git',
            destination_branch='main',
            aliases=['frontend'],
        )
        service = RepositoryService([repository], 3)

        with patch.dict(
            'openhands_agent.data_layers.service.repository_service.os.environ',
            {'SSH_AUTH_SOCK': '/ssh-agent'},
            clear=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.exists',
            return_value=False,
        ):
            with self.assertRaisesRegex(
                ValueError,
                'repository client uses an SSH git remote but SSH_AUTH_SOCK does not exist: /ssh-agent',
            ):
                service.validate_connections()

    def test_pull_destination_branch_uses_provider_token_for_https_remote(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='.',
            provider='bitbucket',
            token='bb-token',
            remote_url='https://shay@bitbucket.org/workspace/repo.git',
        )
        service = RepositoryService([], 3)
        expected_header = 'Authorization: Basic ' + base64.b64encode(
            b'shay:bb-token'
        ).decode('ascii')

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='', stderr=''),
        ) as mock_run:
            service._pull_destination_branch('.', 'main', repository)

        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
                '-c',
                f'http.extraHeader={expected_header}',
                '-c',
                'safe.directory=.',
                '-C',
                '.',
                'pull',
                '--ff-only',
                'origin',
                'main',
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_push_branch_uses_provider_default_https_username_when_remote_has_no_username(self) -> None:
        repository = types.SimpleNamespace(
            id='backend',
            local_path='.',
            provider='github',
            token='gh-token',
            remote_url='https://github.com/workspace/backend.git',
        )
        service = RepositoryService([], 3)
        expected_header = 'Authorization: Basic ' + base64.b64encode(
            b'x-access-token:gh-token'
        ).decode('ascii')

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='', stderr=''),
        ) as mock_run:
            service._push_branch('.', 'feature/proj-1/backend', repository)

        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
                '-c',
                f'http.extraHeader={expected_header}',
                '-c',
                'safe.directory=.',
                '-C',
                '.',
                'push',
                '-u',
                'origin',
                'feature/proj-1/backend',
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_prepare_task_repositories_requires_git_executable(self) -> None:
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value=None,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'git executable is required but was not found on PATH',
            ):
                service.prepare_task_repositories([self.cfg.openhands_agent.repositories[0]])

    def test_validate_connections_requires_at_least_one_repository(self) -> None:
        service = RepositoryService([], 3)

        with self.assertRaisesRegex(ValueError, 'at least one repository must be configured'):
            service.validate_connections()

    def test_list_pull_request_comments_uses_provider_api_when_configured(self) -> None:
        repository = self.cfg.openhands_agent.repositories[0]
        data_access = Mock()
        data_access.list_pull_request_comments.return_value = ['comment']
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
            return_value=data_access,
        ):
            comments = service.list_pull_request_comments(repository, '17')

        self.assertEqual(comments, ['comment'])
        data_access.list_pull_request_comments.assert_called_once_with('17')

    def test_list_pull_request_comments_returns_empty_without_provider_api(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='.',
            remote_url='git@bitbucket.org:workspace/repo.git',
            provider='bitbucket',
            owner='workspace',
            repo_slug='repo',
        )
        service = RepositoryService([], 3)

        comments = service.list_pull_request_comments(repository, '17')

        self.assertEqual(comments, [])

    def test_publish_review_fix_commits_pushes_and_returns_to_destination_branch(self) -> None:
        repository = self.backend_repo

        with patch(
            'openhands_agent.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._publish_branch_updates',
        ) as mock_publish_branch_updates, patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService.destination_branch',
            return_value='main',
        ):
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)
            service.publish_review_fix(
                repository,
                branch_name='feature/proj-1/backend',
                commit_message='Address review comments',
            )

        mock_publish_branch_updates.assert_called_once_with(
            '.',
            'feature/proj-1/backend',
            'main',
            'Address review comments',
            repository,
        )

    def test_resolve_review_comment_uses_provider_api(self) -> None:
        repository = self.cfg.openhands_agent.repositories[0]
        data_access = Mock()
        comment = build_review_comment(
            resolution_target_id='99',
            resolution_target_type='comment',
            resolvable=True,
        )
        service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

        with patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._pull_request_data_access',
            return_value=data_access,
        ):
            service.resolve_review_comment(repository, comment)

        data_access.resolve_review_comment.assert_called_once_with(comment)

    def test_publish_branch_updates_returns_to_destination_branch_when_push_fails(self) -> None:
        with patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._push_branch',
            side_effect=RuntimeError('push failed'),
        ), patch(
            'openhands_agent.data_layers.service.repository_service.RepositoryService._prepare_workspace_for_task',
        ) as mock_prepare_workspace:
            service = RepositoryService(self.cfg.openhands_agent.repositories, 3)

            with self.assertRaisesRegex(RuntimeError, 'push failed'):
                service._publish_branch_updates(
                    '.',
                    'feature/proj-1/backend',
                    'main',
                    'Address review comments',
                )

        mock_prepare_workspace.assert_called_once_with('.', 'main', None)

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
