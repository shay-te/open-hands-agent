"""Repository denylist tests.

Pins down two things:

1. The pure-data parser ``denied_ids`` correctly
   normalises the ``KATO_REPOSITORY_DENYLIST`` env var (case-folded,
   whitespace-trimmed, deduplicated, comma-separated).
2. ``RepositoryInventoryService`` filters denied entries out of the
   inventory at boot — both for explicit ``kato.repositories`` config
   and for root-walk discovery — and emits a WARNING log naming the
   filtered repo. Filtering is the operator's last-line "do not even
   clone this" policy, distinct from ``KATO_IGNORED_REPOSITORY_FOLDERS``
   which is folder-name based and only filters discovery walks.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from kato_core_lib.validation.repository_denylist import (
    REPOSITORY_DENYLIST_ENV_KEY,
    denied_ids,
)


def _create_git_repo(path: Path, remote_url: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ['git', 'init', '-q'], cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ['git', 'remote', 'add', 'origin', remote_url],
        cwd=path, check=True, capture_output=True,
    )


class DenylistParserTests(unittest.TestCase):
    """Parser contract: empty / blank / spaced / mixed-case / dup forms."""

    def test_unset_env_returns_empty_set(self) -> None:
        self.assertEqual(denied_ids(env={}), frozenset())

    def test_blank_env_returns_empty_set(self) -> None:
        self.assertEqual(
            denied_ids(env={REPOSITORY_DENYLIST_ENV_KEY: ''}),
            frozenset(),
        )

    def test_whitespace_only_env_returns_empty_set(self) -> None:
        self.assertEqual(
            denied_ids(env={REPOSITORY_DENYLIST_ENV_KEY: '   \t  '}),
            frozenset(),
        )

    def test_single_id_is_normalised_to_lowercase(self) -> None:
        self.assertEqual(
            denied_ids(env={REPOSITORY_DENYLIST_ENV_KEY: 'Secrets-Vault'}),
            frozenset({'secrets-vault'}),
        )

    def test_comma_separated_entries_are_each_trimmed(self) -> None:
        self.assertEqual(
            denied_ids(
                env={REPOSITORY_DENYLIST_ENV_KEY: ' secrets , bespoke ,fin '},
            ),
            frozenset({'secrets', 'bespoke', 'fin'}),
        )

    def test_duplicates_collapse_silently(self) -> None:
        self.assertEqual(
            denied_ids(
                env={REPOSITORY_DENYLIST_ENV_KEY: 'a, A , a , B'},
            ),
            frozenset({'a', 'b'}),
        )

    def test_trailing_and_leading_commas_are_skipped(self) -> None:
        self.assertEqual(
            denied_ids(env={REPOSITORY_DENYLIST_ENV_KEY: ',a,,b,'}),
            frozenset({'a', 'b'}),
        )

    def test_default_env_uses_os_environ(self) -> None:
        with patch.dict(
            os.environ,
            {REPOSITORY_DENYLIST_ENV_KEY: 'from-os'},
            clear=False,
        ):
            self.assertEqual(denied_ids(), frozenset({'from-os'}))


class InventoryFilterTests(unittest.TestCase):
    """Inventory service must drop denylisted repos at boot, with a warning."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Each test sets its own env; clear any stray host config so
        # tests are independent.
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        os.environ.pop(REPOSITORY_DENYLIST_ENV_KEY, None)

    def _build_service(
        self,
        *,
        explicit_repositories: list | None = None,
        repository_root_path: str | None = None,
    ) -> RepositoryInventoryService:
        return RepositoryInventoryService(
            types.SimpleNamespace(
                repositories=explicit_repositories or [],
                repository_root_path=repository_root_path or str(self.root),
                ignored_repository_folders=[],
            ),
        )

    def _explicit_repo(self, repo_id: str) -> object:
        return types.SimpleNamespace(
            id=repo_id,
            display_name=repo_id,
            local_path=str(self.root / repo_id),
            provider='github',
            remote_url=f'https://github.com/acme/{repo_id}.git',
            owner='acme',
            repo_slug=repo_id,
            aliases=[repo_id],
            token='x',
            provider_base_url='https://api.github.com',
        )

    # ----- explicit config path -----

    def test_denylist_filters_explicit_repository(self) -> None:
        os.environ[REPOSITORY_DENYLIST_ENV_KEY] = 'secrets-vault'
        service = self._build_service(
            explicit_repositories=[
                self._explicit_repo('public-app'),
                self._explicit_repo('secrets-vault'),
            ],
        )

        ids = [repo.id for repo in service.repositories]

        self.assertEqual(ids, ['public-app'])

    def test_denylist_match_is_case_insensitive(self) -> None:
        os.environ[REPOSITORY_DENYLIST_ENV_KEY] = 'Secrets-VAULT'
        service = self._build_service(
            explicit_repositories=[
                self._explicit_repo('public-app'),
                self._explicit_repo('secrets-vault'),
            ],
        )

        ids = [repo.id for repo in service.repositories]

        self.assertEqual(ids, ['public-app'])

    def test_denylist_supports_multiple_entries(self) -> None:
        os.environ[REPOSITORY_DENYLIST_ENV_KEY] = ' secrets-vault , bespoke '
        service = self._build_service(
            explicit_repositories=[
                self._explicit_repo('public-app'),
                self._explicit_repo('secrets-vault'),
                self._explicit_repo('bespoke'),
            ],
        )

        ids = [repo.id for repo in service.repositories]

        self.assertEqual(ids, ['public-app'])

    def test_empty_denylist_keeps_every_repository(self) -> None:
        os.environ.pop(REPOSITORY_DENYLIST_ENV_KEY, None)
        service = self._build_service(
            explicit_repositories=[
                self._explicit_repo('public-app'),
                self._explicit_repo('secrets-vault'),
            ],
        )

        ids = sorted(repo.id for repo in service.repositories)

        self.assertEqual(ids, ['public-app', 'secrets-vault'])

    def test_filtering_logs_warning_naming_repo(self) -> None:
        os.environ[REPOSITORY_DENYLIST_ENV_KEY] = 'secrets-vault'
        service = self._build_service(
            explicit_repositories=[
                self._explicit_repo('public-app'),
                self._explicit_repo('secrets-vault'),
            ],
        )

        with self.assertLogs(service.logger, level='WARNING') as captured:
            _ = service.repositories

        joined = '\n'.join(captured.output)
        self.assertIn('secrets-vault', joined)
        self.assertIn('KATO_REPOSITORY_DENYLIST', joined)

    # ----- discovery path -----

    def test_denylist_filters_repository_discovered_by_root_walk(self) -> None:
        _create_git_repo(
            self.root / 'public-app',
            'git@github.com:acme/public-app.git',
        )
        _create_git_repo(
            self.root / 'secrets-vault',
            'git@github.com:acme/secrets-vault.git',
        )
        os.environ[REPOSITORY_DENYLIST_ENV_KEY] = 'secrets-vault'
        service = self._build_service(repository_root_path=str(self.root))

        ids = [repo.id for repo in service.repositories]

        self.assertEqual(ids, ['public-app'])

    # ----- error edge: empty inventory after filtering -----

    def test_filtering_to_empty_inventory_raises_clear_error(self) -> None:
        # If denylist removes the only configured repo, the existing
        # validator's "at least one repository must be configured"
        # error still fires — we should not silently boot with no
        # inventory.
        os.environ[REPOSITORY_DENYLIST_ENV_KEY] = 'secrets-vault'
        service = self._build_service(
            explicit_repositories=[self._explicit_repo('secrets-vault')],
        )

        with self.assertRaisesRegex(
            ValueError, r'at least one repository must be configured',
        ):
            _ = service.repositories


if __name__ == '__main__':
    unittest.main()
