from pathlib import Path
import re
import subprocess
import tempfile
import types
import unittest
import base64
from unittest.mock import Mock, patch


from kato.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from kato.data_layers.service.repository_service import RepositoryService
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields
from utils import build_review_comment, build_task, build_test_cfg


class RepositoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

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

    def test_raises_when_no_repository_matches_task_text(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)
        task = build_task(description='Update mobile application')

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(task)

    def test_repository_matches_does_not_match_partial_substrings(self) -> None:
        repository = types.SimpleNamespace(
            id='myrepo',
            display_name='My Repository',
            local_path='/workspace/myrepo',
            repo_slug='myrepo',
            aliases=['myrepo'],
        )
        service = RepositoryService([repository], 3)

        self.assertFalse(service._repository_matches('myrepo-extra needs changes', repository))
        self.assertTrue(service._repository_matches('work in myrepo please', repository))

    def test_prepare_task_repositories_sets_resolved_destination_branch(self) -> None:
        repository = self.cfg.kato.repositories[0]
        repository.destination_branch = ''
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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

    def test_restore_task_repositories_forces_dirty_repository_back_to_destination_branch(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='.',
            destination_branch='master',
        )
        service = RepositoryService([], 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/client\n', stderr=''),
                Mock(returncode=0, stdout=' M app.py\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            restored_repositories = service.restore_task_repositories([repository], force=True)

        self.assertEqual(restored_repositories, [repository])
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', '-f', 'master'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'clean', '-fd'],
            ],
        )

    def test_restore_task_repositories_forces_dirty_real_git_repository_back_to_destination_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            self._create_real_git_repository(repo_path)
            self._run_git(repo_path, ['checkout', '-b', 'feature/client'])
            (repo_path / 'README.md').write_text('dirty\n', encoding='utf-8')

            repository = types.SimpleNamespace(
                id='client',
                local_path=str(repo_path),
                destination_branch='main',
            )
            service = RepositoryService([], 3)

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ):
                restored_repositories = service.restore_task_repositories(
                    [repository],
                    force=True,
                )

            self.assertEqual(restored_repositories, [repository])
            self.assertEqual(
                self._git_stdout(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD']),
                'main',
            )
            self.assertEqual(self._git_stdout(repo_path, ['status', '--porcelain']), '')

    def test_restore_task_repositories_forces_dirty_real_git_repository_with_untracked_build_output_back_to_destination_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            self._create_real_git_repository(repo_path)
            self._run_git(repo_path, ['checkout', '-b', 'feature/client'])
            (repo_path / 'README.md').write_text('dirty\n', encoding='utf-8')
            build_dir = repo_path / 'build'
            build_dir.mkdir()
            (build_dir / 'main.js').write_text('compiled\n', encoding='utf-8')

            repository = types.SimpleNamespace(
                id='client',
                local_path=str(repo_path),
                destination_branch='main',
            )
            service = RepositoryService([], 3)

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ):
                restored_repositories = service.restore_task_repositories(
                    [repository],
                    force=True,
                )

            self.assertEqual(restored_repositories, [repository])
            self.assertEqual(
                self._git_stdout(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD']),
                'main',
            )
            self.assertEqual(self._git_stdout(repo_path, ['status', '--porcelain']), '')
            self.assertFalse(build_dir.exists())

    def test_restore_task_repositories_recovers_from_stale_git_index_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            self._create_real_git_repository(repo_path)
            self._run_git(repo_path, ['checkout', '-b', 'feature/client'])
            (repo_path / 'README.md').write_text('dirty\n', encoding='utf-8')
            build_dir = repo_path / 'build'
            build_dir.mkdir()
            (build_dir / 'main.js').write_text('compiled\n', encoding='utf-8')
            lock_path = repo_path / '.git' / 'index.lock'
            lock_path.write_text('stale lock\n', encoding='utf-8')

            repository = types.SimpleNamespace(
                id='client',
                local_path=str(repo_path),
                destination_branch='main',
            )
            service = RepositoryService([], 3)

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ):
                restored_repositories = service.restore_task_repositories(
                    [repository],
                    force=True,
                )

            self.assertEqual(restored_repositories, [repository])
            self.assertEqual(
                self._git_stdout(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD']),
                'main',
            )
            self.assertEqual(self._git_stdout(repo_path, ['status', '--porcelain']), '')
            self.assertFalse(build_dir.exists())
            self.assertFalse(lock_path.exists())

    def test_clear_stale_git_index_lock_keeps_lock_when_git_process_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            git_dir = repo_path / '.git'
            git_dir.mkdir(parents=True)
            lock_path = git_dir / 'index.lock'
            lock_path.write_text('active lock\n', encoding='utf-8')
            service = RepositoryService([], 3)

            with patch.object(
                RepositoryService,
                '_has_running_git_process',
                return_value=True,
            ):
                cleared = service._clear_stale_git_index_lock(str(repo_path))

            self.assertFalse(cleared)
            self.assertTrue(lock_path.exists())

    def test_clear_stale_git_index_lock_returns_false_when_lock_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            (repo_path / '.git').mkdir(parents=True)
            service = RepositoryService([], 3)

            with patch.object(
                RepositoryService,
                '_has_running_git_process',
                return_value=False,
            ):
                cleared = service._clear_stale_git_index_lock(str(repo_path))

            self.assertFalse(cleared)

    def test_run_git_subprocess_uses_env_based_http_auth_and_timeout(self) -> None:
        service = RepositoryService([], 3)
        repository = types.SimpleNamespace(
            provider='github',
            remote_url='https://github.example/acme/repo.git',
            token='secret-token',
        )

        with patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='', stderr=''),
        ) as mock_run:
            service._run_git_subprocess('.', ['status'], repository)

        command = mock_run.call_args.args[0]
        kwargs = mock_run.call_args.kwargs
        self.assertNotIn('http.extraHeader', command)
        self.assertEqual(kwargs['timeout'], RepositoryService.GIT_SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(kwargs['env']['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(kwargs['env']['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(kwargs['env']['GIT_CONFIG_KEY_0'], 'http.extraHeader')
        self.assertTrue(kwargs['env']['GIT_CONFIG_VALUE_0'].startswith('Authorization: '))

    def test_prepare_task_branches_creates_new_task_branch_from_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ) as mock_validate_destination, patch(
            'kato.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            return_value=False,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),  # rev-parse HEAD
                Mock(returncode=0, stdout='', stderr=''),         # status --porcelain
                Mock(returncode=0, stdout='', stderr=''),         # fetch origin
                Mock(returncode=0, stdout='', stderr=''),         # reset --hard origin/main (new)
                Mock(returncode=0, stdout='', stderr=''),         # checkout -b UNA-2398
                Mock(returncode=0, stdout='UNA-2398\n', stderr=''),  # rev-parse HEAD (verify)
                Mock(returncode=0, stdout='', stderr=''),         # status --porcelain (verify)
            ],
        ) as mock_run:
            service.prepare_task_branches([self.backend_repo], {'backend': 'UNA-2398'})

        mock_validate_destination.assert_called_once_with('.', 'main')
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'fetch', 'origin'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'reset', '--hard', 'origin/main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', '-b', 'UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_checks_out_existing_local_task_branch(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        def reference_exists(_local_path: str, reference: str) -> bool:
            return reference == 'refs/heads/UNA-2398'

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            side_effect=reference_exists,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
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
                ['git', '-c', 'safe.directory=.', '-C', '.', 'fetch', 'origin'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_rebases_existing_local_branch_before_starting_work(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        def reference_exists(_local_path: str, reference: str) -> bool:
            return reference in {'refs/heads/UNA-2398', 'origin/UNA-2398'}

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            side_effect=reference_exists,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='UNA-2398\n', stderr=''),
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
                ['git', '-c', 'safe.directory=.', '-C', '.', 'fetch', 'origin'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', 'UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rebase', 'origin/UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_restores_branch_from_origin_when_local_branch_is_missing(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        def reference_exists(_local_path: str, reference: str) -> bool:
            return reference == 'refs/remotes/origin/UNA-2398'

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._git_reference_exists',
            side_effect=reference_exists,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
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
                ['git', '-c', 'safe.directory=.', '-C', '.', 'fetch', 'origin'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'checkout', '-b', 'UNA-2398', 'origin/UNA-2398'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--abbrev-ref', 'HEAD'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            ],
        )

    def test_prepare_task_branches_cleans_untracked_build_output_before_reusing_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            self._create_real_git_repository(repo_path)
            self._run_git(repo_path, ['checkout', '-b', 'UNA-2298'])
            build_dir = repo_path / 'build'
            build_dir.mkdir()
            (build_dir / 'main.js').write_text('compiled\n', encoding='utf-8')

            repository = types.SimpleNamespace(
                id='client',
                local_path=str(repo_path),
                destination_branch='main',
            )
            service = RepositoryService([], 3)

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ):
                prepared_repositories = service.prepare_task_branches(
                    [repository],
                    {'client': 'UNA-2298'},
                )

            self.assertEqual(prepared_repositories, [repository])
            self.assertEqual(
                self._git_stdout(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD']),
                'UNA-2298',
            )
            self.assertEqual(self._git_stdout(repo_path, ['status', '--porcelain']), '')
            self.assertFalse(build_dir.exists())

    def test_prepare_task_repositories_raises_when_checkout_does_not_leave_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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

    def test_prepare_task_repositories_makes_git_ready_when_dirty_before_next_task(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch.object(
            RepositoryService,
            '_make_git_ready_for_work',
            return_value='main',
        ) as mock_make_git_ready, patch.object(
            RepositoryService,
            '_validate_destination_branch_tracking_state',
        ), patch.object(
            RepositoryService,
            '_pull_destination_branch',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
                Mock(returncode=0, stdout=' M app.py\n', stderr=''),
                Mock(returncode=0, stdout='main\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ):
            prepared_repositories = service.prepare_task_repositories([self.backend_repo])

        self.assertEqual(prepared_repositories[0].destination_branch, 'main')
        mock_make_git_ready.assert_called_once_with('.', 'main', self.backend_repo)

    def test_prepare_task_repositories_cleans_dirty_real_git_repository_before_next_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'client'
            self._create_real_git_repository(repo_path)
            (repo_path / 'README.md').write_text('dirty\n', encoding='utf-8')

            repository = types.SimpleNamespace(
                id='client',
                local_path=str(repo_path),
                destination_branch='main',
            )
            service = RepositoryService([], 3)

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch.object(
                RepositoryService,
                '_prepare_repository_access',
            ):
                prepared_repositories = service.prepare_task_repositories([repository])

            self.assertEqual(prepared_repositories, [repository])
            self.assertEqual(
                self._git_stdout(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD']),
                'main',
            )
            self.assertEqual(self._git_stdout(repo_path, ['status', '--porcelain']), '')

    def test_prepare_task_repositories_rejects_destination_branch_with_local_only_commits(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
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
                        api_email='bb-api@example.com',
                    ),
                ),
                3,
            )

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
            ), patch(
                'kato.data_layers.service.repository_service.subprocess.run',
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
                        api_email='bb-api@example.com',
                    ),
                ),
                3,
            )

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    'missing pull request API token for repository ob-love-admin-client',
                ):
                    service.prepare_task_repositories([service.repositories[0]])


    def test_does_not_match_repository_alias_inside_hyphenated_word(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)
        task = build_task(description='Improve non-client rendering flow')

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(task)

    def test_matches_repository_alias_surrounded_by_punctuation(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)
        task = build_task(description='Please update (backend), then circle back.')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['backend'])

    def test_matches_repository_by_display_name_from_summary(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)
        task = build_task(summary='Client polish pass', description='Tighten UX copy.')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client'])

    def test_prefers_configured_destination_branch(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        self.assertEqual(service.destination_branch(self.backend_repo), 'main')

    @property
    def backend_repo(self):
        return self.cfg.kato.repositories[1]

    def test_infers_destination_branch_from_local_git_default_branch(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)
        repository = self.cfg.kato.repositories[0]

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='refs/remotes/origin/master\n'),
        ):
            self.assertEqual(service.destination_branch(repository), 'master')

    def test_destination_branch_raises_when_git_cannot_infer_default_branch(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)
        repository = self.cfg.kato.repositories[0]

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=1, stdout=''),
        ):
            with self.assertRaisesRegex(
                ValueError,
                'unable to determine destination branch for repository client',
            ):
                service.destination_branch(repository)

    def test_build_branch_name_uses_task_id(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
            return_value='',
        ) as mock_prepare_branch, patch.object(
            RepositoryService,
            'restore_task_repositories',
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
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
        mock_restore_repositories.assert_called_once_with([repository], force=True)

    def test_create_pull_request_returns_to_destination_branch_even_when_pr_creation_fails(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        data_access.create_pull_request.side_effect = RuntimeError('provider down')

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
            return_value='',
        ), patch.object(
            RepositoryService,
            'restore_task_repositories',
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
        ), patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
            with self.assertRaisesRegex(RuntimeError, 'provider down'):
                service.create_pull_request(
                    repository,
                    title='PROJ-1: Fix bug',
                    source_branch='feature/proj-1/client',
                    description='Ready',
                    commit_message='Implement PROJ-1',
                )

        mock_restore_repositories.assert_called_once_with([repository], force=True)

    def test_create_pull_request_creates_pr_before_restoring_workspace(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        pull_request_payload = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://github.example/pull/17',
        }
        call_order: list[str] = []

        def assert_workspace_restored_after_pr(repositories, *, force: bool) -> None:
            self.assertEqual(data_access.create_pull_request.call_count, 1)
            self.assertEqual(repositories, [repository])
            self.assertTrue(force)
            call_order.append('restore')

        def record_push(local_path: str, branch_name: str, repository_arg) -> None:
            call_order.append('push')
            self.assertEqual(local_path, '.')
            self.assertEqual(branch_name, 'feature/proj-1/backend')
            self.assertIs(repository_arg, repository)

        def record_create_pull_request(**kwargs):
            call_order.append('create_pull_request')
            return pull_request_payload

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
            return_value='',
        ) as mock_prepare_branch, patch.object(
            RepositoryService,
            'restore_task_repositories',
            side_effect=assert_workspace_restored_after_pr,
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
            side_effect=record_push,
        ) as mock_push_branch, patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ):
            data_access.create_pull_request.side_effect = record_create_pull_request
            service = RepositoryService(self.cfg.kato.repositories, 3)
            service.create_pull_request(
                repository,
                title='PROJ-1: Fix bug',
                source_branch='feature/proj-1/backend',
                description='Ready',
                commit_message='Implement PROJ-1',
            )

        mock_prepare_branch.assert_called_once_with(
            '.',
            'feature/proj-1/backend',
            'main',
            'Implement PROJ-1',
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/backend', repository)
        mock_restore_repositories.assert_called_once_with([repository], force=True)
        data_access.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/backend',
            destination_branch='main',
            description='Ready',
        )
        self.assertEqual(
            call_order,
            ['push', 'create_pull_request', 'restore'],
        )

    def test_create_pull_request_returns_to_destination_branch_even_when_push_fails(self) -> None:
        repository = self.backend_repo

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
            return_value='',
        ), patch.object(
            RepositoryService,
            'restore_task_repositories',
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
            side_effect=RuntimeError('push failed'),
        ), patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
            with self.assertRaisesRegex(RuntimeError, 'push failed'):
                service.create_pull_request(
                    repository,
                    title='PROJ-1: Fix bug',
                    source_branch='feature/proj-1/client',
                    description='Ready',
                    commit_message='Implement PROJ-1',
                )

        mock_restore_repositories.assert_called_once_with([repository], force=True)

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
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='[feature/proj-1/backend abc123] Implement PROJ-1\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.os.path.exists',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.os.remove',
        ) as mock_remove, patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validation_report_text',
            return_value='Validation report:\n- verified the task manually.',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch.object(
            RepositoryService,
            'restore_task_repositories',
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_results,
        ) as mock_run:
            service = RepositoryService(self.cfg.kato.repositories, 3)
            service.logger = Mock()
            service._publication_service.logger = Mock()
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
                ['git', '-c', 'safe.directory=.', '-C', '.', 'add', '-A'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'commit', '-m', 'Implement PROJ-1'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--verify', 'main'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-list', '--count', 'main..feature/proj-1/backend'],
            ],
        )
        data_access.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/backend',
            destination_branch='main',
            description='Ready',
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/backend', repository)
        mock_restore_repositories.assert_called_once_with([repository], force=True)
        service._publication_service.logger.warning.assert_called_once_with(
            'validation report was missing or empty for repository %s; '
            'falling back to structured pull request description',
            repository.id,
        )

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
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='[feature/proj-1/backend abc123] Implement PROJ-1\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='1\n', stderr=''),
            Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='main\n', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
            Mock(returncode=0, stdout='', stderr=''),
        ]

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validation_report_text',
            return_value='Validation report:\n- verified the task manually.',
        ), patch.object(
            RepositoryService,
            'restore_task_repositories',
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_results,
        ) as mock_run:
            service = RepositoryService(self.cfg.kato.repositories, 3)
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
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'clean', '-fd', '--', 'validation_report.md'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'add', '-A'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'commit', '-m', 'Implement PROJ-1'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--verify', 'main'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-list', '--count', 'main..feature/proj-1/backend'],
                ],
        )
        mock_restore_repositories.assert_called_once_with([repository], force=True)
        data_access.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/backend',
            destination_branch='main',
            description='Validation report:\n- verified the task manually.',
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/backend', repository)

    def test_create_pull_request_excludes_generated_build_artifacts_from_commit(self) -> None:
        repository = self.backend_repo
        data_access = Mock()
        data_access.create_pull_request.return_value = {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: Fix bug',
            PullRequestFields.URL: 'https://github.example/pull/17',
        }
        status_call_count = {'count': 0}

        def subprocess_side_effect(command, **kwargs):
            tail = tuple(command[5:])
            if tail == ('rev-parse', '--abbrev-ref', 'HEAD'):
                return Mock(returncode=0, stdout='feature/proj-1/backend\n', stderr='')
            if tail == ('status', '--porcelain'):
                status_call_count['count'] += 1
                if status_call_count['count'] == 1:
                    return Mock(
                        returncode=0,
                        stdout=' M app.py\n?? build/main.js\n?? validation_report.md\n',
                        stderr='',
                    )
                return Mock(returncode=0, stdout='', stderr='')
            if tail in {
                ('reset', 'HEAD', '--', 'build'),
                ('clean', '-fd', '--', 'build'),
                ('reset', 'HEAD', '--', 'validation_report.md'),
                ('clean', '-fd', '--', 'validation_report.md'),
                ('add', '-A'),
            }:
                return Mock(returncode=0, stdout='', stderr='')
            if tail == ('commit', '-m', 'Implement PROJ-1'):
                return Mock(
                    returncode=0,
                    stdout='[feature/proj-1/backend abc123] Implement PROJ-1\n',
                    stderr='',
                )
            if tail == ('rev-parse', '--abbrev-ref', 'HEAD'):
                return Mock(returncode=0, stdout='main\n', stderr='')
            if tail == ('rev-list', '--count', 'main..feature/proj-1/backend'):
                return Mock(returncode=0, stdout='1\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')

        def is_directory(path: str) -> bool:
            return path.endswith('/build') or path.endswith('\\build')

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            side_effect=is_directory,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._ensure_branch_is_publishable',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService.restore_task_repositories',
        ) as mock_restore_repositories, patch(
            'kato.data_layers.service.repository_service.RepositoryService._validation_report_text',
            return_value='Validation report:\n- verified the task manually.',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
        ) as mock_push_branch, patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_side_effect,
        ) as mock_run:
            service = RepositoryService(self.cfg.kato.repositories, 3)
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
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'reset', 'HEAD', '--', 'build'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'clean', '-fd', '--', 'build'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'reset', 'HEAD', '--', 'validation_report.md'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'clean', '-fd', '--', 'validation_report.md'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'add', '-A'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'commit', '-m', 'Implement PROJ-1'],
                    ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
                ],
            )
        mock_restore_repositories.assert_called_once_with([repository], force=True)
        data_access.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1/backend',
            destination_branch='main',
            description='Validation report:\n- verified the task manually.',
        )
        mock_push_branch.assert_called_once_with('.', 'feature/proj-1/backend', repository)

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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._validate_destination_branch_tracking_state',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=subprocess_results,
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
            with self.assertRaisesRegex(
                RuntimeError,
                'branch feature/proj-1/backend has no task changes ahead of main',
            ):
                service.create_pull_request(
                    repository,
                    title='PROJ-1: Fix bug',
                    source_branch='feature/proj-1/backend',
                    description='Ready',
                    commit_message='Implement PROJ-1',
                )

    def test_validate_task_branches_are_publishable_rejects_branch_without_committed_changes(self) -> None:
        repository = self.backend_repo

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch.object(
            RepositoryService,
            '_working_tree_status',
            return_value='',
        ), patch.object(
            RepositoryService,
            '_comparison_reference',
            return_value='main',
        ), patch.object(
            RepositoryService,
            '_ahead_count',
            return_value=0,
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
            with self.assertRaisesRegex(
                RuntimeError,
                'branch feature/proj-1/backend has no task changes ahead of main',
            ):
                service.validate_task_branches_are_publishable(
                    [repository],
                    {'backend': 'feature/proj-1/backend'},
                )

    def test_validate_task_branches_are_publishable_accepts_dirty_worktree_changes(self) -> None:
        repository = self.backend_repo

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout=' M src/file.py\n', stderr=''),
        ) as mock_run:
            service = RepositoryService(self.cfg.kato.repositories, 3)
            result = service.validate_task_branches_are_publishable(
                [repository],
                {'backend': 'feature/proj-1/backend'},
            )

        self.assertEqual(result, [repository])
        mock_run.assert_called_once_with(
            ['git', '-c', 'safe.directory=.', '-C', '.', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            check=False,
            env=unittest.mock.ANY,
            timeout=RepositoryService.GIT_SUBPROCESS_TIMEOUT_SECONDS,
        )

    def test_validate_task_branches_are_pushable_dry_runs_branch_publish(self) -> None:
        repository = self.backend_repo

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='', stderr=''),
        ) as mock_run:
            service = RepositoryService(self.cfg.kato.repositories, 3)
            result = service.validate_task_branches_are_pushable(
                [repository],
                {'backend': 'feature/proj-1/backend'},
            )

        self.assertEqual(result, [repository])
        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
                '-c',
                'safe.directory=.',
                '-C',
                '.',
                'push',
                '--dry-run',
                '-u',
                'origin',
                'feature/proj-1/backend',
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_validate_task_branches_are_pushable_rejects_missing_git_push_permissions(self) -> None:
        repository = self.backend_repo

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(
                returncode=128,
                stdout='',
                stderr=(
                    'remote: Your credentials lack one or more required privilege scopes.\n'
                    "fatal: unable to access 'https://bitbucket.org/workspace/project.git/': "
                    'The requested URL returned error: 403'
                ),
            ),
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
            with self.assertRaisesRegex(
                RuntimeError,
                r"\[Error\].*missing git push permissions\. cannot work\.\s+remote: Your credentials lack one or more required privilege scopes\.",
            ):
                service.validate_task_branches_are_pushable(
                    [repository],
                    {'backend': 'feature/proj-1/backend'},
                )

    def test_validate_connections_checks_local_paths(self) -> None:
        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
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
                        username='bb-user',
                        api_email='bb-api@example.com',
                    ),
                ),
                3,
            )
            expected_header = 'Authorization: Basic ' + base64.b64encode(
                b'bb-user:bb-token'
            ).decode('ascii')

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'kato.data_layers.service.repository_service.subprocess.run',
                return_value=Mock(returncode=0, stdout='refs/heads/main\n', stderr=''),
            ) as mock_run:
                service.validate_connections()

        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
                '-c',
                f'safe.directory={repo_path.resolve()}',
                '-C',
                str(repo_path.resolve()),
                'ls-remote',
                '--heads',
                'origin',
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_CONFIG_KEY_0'], 'http.extraHeader')
        self.assertEqual(
            mock_run.call_args.kwargs['env']['GIT_CONFIG_VALUE_0'],
            expected_header,
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
                        username='workspace',
                        api_email='bb-api@example.com',
                    ),
                ),
                3,
            )
            expected_header = 'Authorization: Basic ' + base64.b64encode(
                b'workspace:bb-token'
            ).decode('ascii')

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'kato.data_layers.service.repository_service.subprocess.run',
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
                    f'safe.directory={client_repo.resolve()}',
                    '-C',
                    str(client_repo.resolve()),
                    'ls-remote',
                    '--heads',
                    'origin',
                ],
            ],
        )
        for call in mock_run.call_args_list:
            self.assertEqual(call.kwargs['env']['GIT_CONFIG_COUNT'], '1')
            self.assertEqual(call.kwargs['env']['GIT_CONFIG_KEY_0'], 'http.extraHeader')
            self.assertEqual(call.kwargs['env']['GIT_CONFIG_VALUE_0'], expected_header)
        self.assertEqual(
            [call.kwargs['env']['GIT_TERMINAL_PROMPT'] for call in mock_run.call_args_list],
            ['0', '0'],
        )

    def test_git_http_auth_header_uses_configured_bitbucket_username(self) -> None:
        repository = types.SimpleNamespace(
            provider='bitbucket',
            owner='workspace',
            username='bb-user',
            remote_url='https://bitbucket.org/workspace/project.git',
            token='bb-token',
        )

        header = RepositoryService._git_http_auth_header(repository)

        expected_header = 'Authorization: Basic ' + base64.b64encode(
            b'bb-user:bb-token'
        ).decode('ascii')
        self.assertEqual(header, expected_header)

    def test_git_http_auth_header_falls_back_to_x_token_auth_for_bitbucket_without_username(self) -> None:
        repository = types.SimpleNamespace(
            provider='bitbucket',
            owner='workspace',
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
                        api_email='bb-api@example.com',
                    ),
                ),
                3,
            )

            with patch(
                'kato.data_layers.service.repository_service.shutil.which',
                return_value='/usr/bin/git',
            ), patch(
                'kato.data_layers.service.repository_service.subprocess.run',
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
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=False,
        ):
            with self.assertRaisesRegex(ValueError, 'missing local repository path'):
                service.prepare_task_repositories([self.cfg.kato.repositories[0]])

    def test_validate_connections_requires_git_executable(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
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
            'kato.data_layers.service.repository_service.os.environ',
            {},
            clear=True,
        ), patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
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
            'kato.data_layers.service.repository_service.os.environ',
            {'SSH_AUTH_SOCK': '/ssh-agent'},
            clear=True,
        ), patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.os.path.exists',
            return_value=False,
        ):
            with self.assertRaisesRegex(
                ValueError,
                'repository client uses an SSH git remote but SSH_AUTH_SOCK does not exist: /ssh-agent',
            ):
                service.validate_connections()

    def test_validate_connections_requires_ssh_executable_for_ssh_remote(self) -> None:
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
            'kato.data_layers.service.repository_service.os.environ',
            {'SSH_AUTH_SOCK': '/ssh-agent'},
            clear=True,
        ), patch(
            'kato.data_layers.service.repository_service.shutil.which',
            side_effect=lambda name: None if name == 'ssh' else '/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.os.path.exists',
            return_value=True,
        ):
            with self.assertRaisesRegex(
                ValueError,
                re.escape(
                    'repository client uses an SSH git remote but the ssh executable '
                    'is not available on PATH; install OpenSSH '
                    '(or rebuild the Kato image with openssh-client)'
                ),
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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='', stderr=''),
        ) as mock_run:
            service._pull_destination_branch('.', 'main', repository)

        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
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
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_CONFIG_KEY_0'], 'http.extraHeader')
        self.assertEqual(
            mock_run.call_args.kwargs['env']['GIT_CONFIG_VALUE_0'],
            expected_header,
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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            return_value=Mock(returncode=0, stdout='', stderr=''),
        ) as mock_run:
            service._push_branch('.', 'feature/proj-1/backend', repository)

        self.assertEqual(
            mock_run.call_args.args[0],
            [
                'git',
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
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_CONFIG_KEY_0'], 'http.extraHeader')
        self.assertEqual(
            mock_run.call_args.kwargs['env']['GIT_CONFIG_VALUE_0'],
            expected_header,
        )
        self.assertEqual(mock_run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_push_branch_fetches_rebases_and_retries_on_non_fast_forward_rejection(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            local_path='.',
            provider='bitbucket',
            token='bb-token',
            remote_url='https://bitbucket.org/workspace/repo.git',
        )
        service = RepositoryService([], 3)
        service.logger = Mock()

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.subprocess.run',
            side_effect=[
                Mock(
                    returncode=1,
                    stdout='',
                    stderr=(
                        ' ! [rejected] UNA-2265 -> UNA-2265 (fetch first)\n'
                        'Updates were rejected because the remote contains work '
                        'that you do not have locally.'
                    ),
                ),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='origin/UNA-2265\n', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
                Mock(returncode=0, stdout='', stderr=''),
            ],
        ) as mock_run:
            service._push_branch('.', 'UNA-2265', repository)

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                ['git', '-c', 'safe.directory=.', '-C', '.', 'push', '-u', 'origin', 'UNA-2265'],
                [
                    'git',
                    '-c',
                    'safe.directory=.',
                    '-C',
                    '.',
                    'fetch',
                    'origin',
                    'UNA-2265:refs/remotes/origin/UNA-2265',
                ],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rev-parse', '--verify', 'origin/UNA-2265'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'rebase', 'origin/UNA-2265'],
                ['git', '-c', 'safe.directory=.', '-C', '.', 'push', '-u', 'origin', 'UNA-2265'],
            ],
        )
        service.logger.warning.assert_called_once_with(
            'push for branch %s was rejected because origin has newer commits; '
            'fetching and rebasing before retrying',
            'UNA-2265',
        )

    def test_prepare_task_repositories_requires_git_executable(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_service.shutil.which',
            return_value=None,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'git executable is required but was not found on PATH',
            ):
                service.prepare_task_repositories([self.cfg.kato.repositories[0]])

    def test_validate_connections_requires_at_least_one_repository(self) -> None:
        service = RepositoryService([], 3)

        with self.assertRaisesRegex(ValueError, 'at least one repository must be configured'):
            service.validate_connections()

    def test_list_pull_request_comments_uses_provider_api_when_configured(self) -> None:
        repository = self.cfg.kato.repositories[0]
        data_access = Mock()
        data_access.list_pull_request_comments.return_value = ['comment']
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
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
            'kato.data_layers.service.repository_service.shutil.which',
            return_value='/usr/bin/git',
        ), patch(
            'kato.data_layers.service.repository_service.os.path.isdir',
            return_value=True,
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._publish_branch_updates',
        ) as mock_publish_branch_updates, patch(
            'kato.data_layers.service.repository_service.RepositoryService.destination_branch',
            return_value='main',
        ):
            service = RepositoryService(self.cfg.kato.repositories, 3)
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
        repository = self.cfg.kato.repositories[0]
        data_access = Mock()
        comment = build_review_comment(
            resolution_target_id='99',
            resolution_target_type='comment',
            resolvable=True,
        )
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch(
            'kato.data_layers.service.repository_inventory_service.RepositoryInventoryService._pull_request_data_access',
            return_value=data_access,
        ):
            service.resolve_review_comment(repository, comment)

        data_access.resolve_review_comment.assert_called_once_with(comment)

    def test_pull_request_data_access_uses_bearer_token_for_bitbucket_api(self) -> None:
        repository = types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='.',
            provider_base_url='https://api.bitbucket.org/2.0',
            token='bb-token',
            owner='workspace',
            repo_slug='repo',
            destination_branch='main',
            bitbucket_username='bb-user',
            bitbucket_api_email='bb-user@example.com',
            username='legacy-user',
        )
        service = RepositoryInventoryService([repository])

        with patch(
            'kato.data_layers.service.repository_inventory_service.build_pull_request_client',
            return_value=Mock(),
        ) as mock_build:
            service._pull_request_data_access(repository)

        config = mock_build.call_args.args[0]
        self.assertEqual(config.base_url, 'https://api.bitbucket.org/2.0')
        self.assertEqual(config.token, 'bb-token')
        self.assertEqual(config.get('api_email'), 'bb-user@example.com')
        self.assertIsNone(config.get('username'))

    def test_validation_report_text_reads_and_trims_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / 'validation_report.md'
            report_path.write_text('  Validation report:\n- checked the branch.  \n', encoding='utf-8')

            service = RepositoryService(self.cfg.kato.repositories, 3)

            self.assertEqual(
                service._validation_report_text(str(report_path)),
                'Validation report:\n- checked the branch.',
            )

    def test_validation_report_text_returns_none_when_file_is_missing(self) -> None:
        service = RepositoryService(self.cfg.kato.repositories, 3)

        self.assertIsNone(
            service._validation_report_text('/tmp/does-not-exist/validation_report.md'),
        )

    def test_commit_branch_changes_uses_validation_report_file_and_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / 'validation_report.md'
            report_path.write_text(
                'Validation report:\n- verified the task manually.\n',
                encoding='utf-8',
            )

            service = RepositoryService(self.cfg.kato.repositories, 3)

            with patch.object(
                service,
                '_working_tree_status',
                return_value=' M app.py\n?? validation_report.md\n',
            ), patch.object(
                service,
                '_ensure_clean_worktree',
            ) as mock_ensure_clean_worktree, patch.object(
                service,
                '_run_git',
            ) as mock_run_git:
                validation_report_description = service._commit_branch_changes_if_needed(
                    temp_dir,
                    'feature/proj-1/backend',
                    'Implement PROJ-1',
                )

            self.assertEqual(
                validation_report_description,
                'Validation report:\n- verified the task manually.',
            )
            self.assertEqual(
                [call.args[1] for call in mock_run_git.call_args_list],
                [
                    ['add', '-A'],
                    ['reset', 'HEAD', '--', 'validation_report.md'],
                    ['clean', '-fd', '--', 'validation_report.md'],
                    ['add', '-A'],
                    ['commit', '-m', 'Implement PROJ-1'],
                ],
            )
            mock_ensure_clean_worktree.assert_called_once_with(
                temp_dir,
                'feature/proj-1/backend',
            )

    def test_commit_branch_changes_excludes_generated_artifacts_restages_and_commits(self) -> None:
        """Generated artifacts must be reset+cleaned, then changes restaged, committed, and worktree verified."""
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch.object(
            service,
            '_working_tree_status',
            return_value=' M app.py\n?? build/main.js\n',
        ), patch.object(
            service,
            '_ensure_clean_worktree',
        ) as mock_ensure_clean_worktree, patch.object(
            service,
            '_run_git',
        ) as mock_run_git:
            result = service._commit_branch_changes_if_needed(
                '/repo',
                'feature/proj-1/backend',
                'Implement PROJ-1',
            )

        self.assertEqual(result, '')
        self.assertEqual(
            [call.args[1] for call in mock_run_git.call_args_list],
            [
                ['add', '-A'],
                ['reset', 'HEAD', '--', 'build'],
                ['clean', '-fd', '--', 'build'],
                ['add', '-A'],
                ['commit', '-m', 'Implement PROJ-1'],
            ],
        )
        mock_ensure_clean_worktree.assert_called_once_with('/repo', 'feature/proj-1/backend')

    def test_commit_branch_changes_with_no_artifacts_stages_and_commits(self) -> None:
        """Plain working-tree changes must be staged, committed, and worktree verified."""
        service = RepositoryService(self.cfg.kato.repositories, 3)

        with patch.object(
            service,
            '_working_tree_status',
            return_value=' M app.py\n',
        ), patch.object(
            service,
            '_ensure_clean_worktree',
        ) as mock_ensure_clean_worktree, patch.object(
            service,
            '_run_git',
        ) as mock_run_git:
            result = service._commit_branch_changes_if_needed(
                '/repo',
                'feature/proj-1/backend',
                'Implement PROJ-1',
            )

        self.assertEqual(result, '')
        self.assertEqual(
            [call.args[1] for call in mock_run_git.call_args_list],
            [
                ['add', '-A'],
                ['add', '-A'],
                ['commit', '-m', 'Implement PROJ-1'],
            ],
        )
        mock_ensure_clean_worktree.assert_called_once_with('/repo', 'feature/proj-1/backend')

    def test_validation_report_paths_from_status_handles_renamed_report(self) -> None:
        status_output = 'R  old-report.md -> validation_report.md\n?? app.py\n'

        self.assertEqual(
            RepositoryService._validation_report_paths_from_status(status_output),
            ['validation_report.md'],
        )

    def test_generated_artifact_paths_from_status_detects_known_roots_and_ignores_validation_report(self) -> None:
        status_output = ' M app.py\n?? build/main.js\n?? dist/app.js\n?? validation_report.md\n'

        self.assertEqual(
            RepositoryService._generated_artifact_paths_from_status(status_output),
            ['build', 'dist'],
        )

    def test_publish_branch_updates_returns_to_destination_branch_when_push_fails(self) -> None:
        with patch(
            'kato.data_layers.service.repository_service.RepositoryService._prepare_branch_for_publication',
            return_value='',
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._push_branch',
            side_effect=RuntimeError('push failed'),
        ), patch(
            'kato.data_layers.service.repository_service.RepositoryService._prepare_workspace_for_task',
        ) as mock_prepare_workspace:
            service = RepositoryService(self.cfg.kato.repositories, 3)

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

    @staticmethod
    def _create_real_git_repository(path: Path) -> None:
        path.mkdir(parents=True)
        RepositoryServiceTests._run_git(path, ['init'])
        RepositoryServiceTests._run_git(path, ['checkout', '-b', 'main'])
        RepositoryServiceTests._run_git(path, ['config', 'user.name', 'OpenHands Test'])
        RepositoryServiceTests._run_git(
            path,
            ['config', 'user.email', 'openhands@example.com'],
        )
        (path / 'README.md').write_text('initial\n', encoding='utf-8')
        RepositoryServiceTests._run_git(path, ['add', 'README.md'])
        RepositoryServiceTests._run_git(path, ['commit', '-m', 'initial commit'])

    @staticmethod
    def _run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ['git', '-C', str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _git_stdout(path: Path, args: list[str]) -> str:
        result = subprocess.run(
            ['git', '-C', str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
