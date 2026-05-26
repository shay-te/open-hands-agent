"""Defensive coverage for ``RepositoryInventoryService``.

Targets uncovered branches: tag-resolution edge cases, SSH-remote
validation, pull-request API validation, review URL fallbacks, etc.
"""

from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
    RepositoryIgnoredByConfigError,
)


def _make_service(repositories_config=None):
    config = repositories_config or SimpleNamespace(repositories=[])
    return RepositoryInventoryService(config, 3)


class ResolveTaskRepositoriesIgnoredTagTests(unittest.TestCase):
    def test_raises_when_tag_in_ignore_list(self) -> None:
        # Line 173-179: RepositoryIgnoredByConfigError.
        service = _make_service(SimpleNamespace(
            repositories=[],
            ignored_repository_folders='secret-lib',
        ))
        task = SimpleNamespace(
            id='PROJ-1',
            tags=['kato:repo:secret-lib'],
            description='',
        )
        with self.assertRaises(RepositoryIgnoredByConfigError):
            service.resolve_task_repositories(task)


class ResolveRepositoryForTagTests(unittest.TestCase):
    def test_returns_none_for_blank_tag(self) -> None:
        # Lines 202-203.
        service = _make_service()
        self.assertIsNone(service._resolve_repository_for_tag(''))
        self.assertIsNone(service._resolve_repository_for_tag('   '))


class DiscoverRepositoryAtNamedFolderTests(unittest.TestCase):
    def test_rejects_tag_with_path_separator(self) -> None:
        # Lines 225-226.
        service = _make_service()
        self.assertIsNone(
            service._discover_repository_at_named_folder('weird/tag'),
        )

    def test_returns_none_when_root_path_blank(self) -> None:
        # Lines 228-229.
        service = _make_service(SimpleNamespace(repositories=[]))
        self.assertIsNone(
            service._discover_repository_at_named_folder('valid-tag'),
        )

    def test_returns_none_when_candidate_is_not_dir(self) -> None:
        # Lines 231-232.
        with tempfile.TemporaryDirectory() as td:
            service = _make_service(SimpleNamespace(
                repositories=[],
                repository_root_path=td,
            ))
            # tag references a non-existent folder.
            self.assertIsNone(
                service._discover_repository_at_named_folder('nope'),
            )

    def test_returns_none_when_no_git_dir(self) -> None:
        # Lines 233-234.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'no-git-here').mkdir()
            service = _make_service(SimpleNamespace(
                repositories=[],
                repository_root_path=td,
            ))
            self.assertIsNone(
                service._discover_repository_at_named_folder('no-git-here'),
            )

    def test_returns_none_when_folder_in_ignore_list(self) -> None:
        # Lines 239-240.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'secret' / '.git').mkdir(parents=True)
            service = _make_service(SimpleNamespace(
                repositories=[],
                repository_root_path=td,
                ignored_repository_folders='secret',
            ))
            self.assertIsNone(
                service._discover_repository_at_named_folder('secret'),
            )

    def test_returns_none_on_oserror(self) -> None:
        # Lines 243-244.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'real' / '.git').mkdir(parents=True)
            service = _make_service(SimpleNamespace(
                repositories=[],
                repository_root_path=td,
            ))
            with patch(
                'kato_core_lib.data_layers.service.repository_inventory_service.'
                'build_discovered_repository',
                side_effect=OSError('cannot read'),
            ):
                self.assertIsNone(
                    service._discover_repository_at_named_folder('real'),
                )


class NormalizedRepositoriesTests(unittest.TestCase):
    def test_returns_empty_for_none(self) -> None:
        # Lines 290-291.
        self.assertEqual(
            RepositoryInventoryService._normalized_repositories(None), [],
        )

    def test_returns_list_input_as_is(self) -> None:
        # Line 293.
        repos = [SimpleNamespace(id='a'), SimpleNamespace(id='b')]
        self.assertEqual(
            RepositoryInventoryService._normalized_repositories(repos),
            repos,
        )

    def test_handles_iterable_typeerror(self) -> None:
        # Lines 297-299: ``iter`` raises → wrap in list of one.

        class _BadIter:
            def __iter__(self):
                raise TypeError('cannot iterate')

        obj = _BadIter()
        # Falls through to "[repository_source]".
        result = RepositoryInventoryService._normalized_repositories(obj)
        self.assertEqual(result, [obj])

    def test_handles_single_object_input(self) -> None:
        # Line 299: single (non-iterable) object becomes a 1-list.
        obj = SimpleNamespace(id='one')
        self.assertEqual(
            RepositoryInventoryService._normalized_repositories(obj),
            [obj],
        )


class LoadRepositoriesTests(unittest.TestCase):
    def test_returns_empty_when_settings_have_no_repos_and_no_root(self) -> None:
        # Line 313: ``return []`` when both branches empty.
        service = _make_service()
        result = service._load_repositories(SimpleNamespace(repositories=[]))
        self.assertEqual(result, [])

    def test_returns_normalized_for_non_settings_source(self) -> None:
        # Lines 314 / 307 — non-settings input takes the bottom branch.
        service = _make_service()
        repos = [SimpleNamespace(id='r')]
        result = service._load_repositories(repos)
        self.assertEqual(result, repos)


class DiscoverRepositoriesFromRootTests(unittest.TestCase):
    def test_returns_empty_when_no_root_path(self) -> None:
        # Line 348.
        service = _make_service()
        self.assertEqual(
            service._discover_repositories_from_root(SimpleNamespace()),
            [],
        )


class KeywordMatchesTests(unittest.TestCase):
    def test_returns_false_for_blank_keyword(self) -> None:
        # Lines 401-402.
        self.assertFalse(
            RepositoryInventoryService._keyword_matches('text', ''),
        )


class RepositoryAliasesTests(unittest.TestCase):
    def test_excludes_dot_or_blank_local_path_alias(self) -> None:
        # Lines 408-410: ``local_path_alias in {'', '.'} -> blank``.
        result = RepositoryInventoryService._repository_aliases(
            SimpleNamespace(local_path='.'),
        )
        # No '.' entry in aliases.
        self.assertNotIn('.', result)

    def test_includes_extra_aliases(self) -> None:
        # Line 417-418: ``aliases`` attribute on repository is also normalized.
        result = RepositoryInventoryService._repository_aliases(
            SimpleNamespace(
                id='r',
                aliases=['EXTRA-Alias'],
            ),
        )
        self.assertIn('extra-alias', result)


class RepositoryTagsTests(unittest.TestCase):
    def test_accepts_string_tag(self) -> None:
        # Line 425.
        result = RepositoryInventoryService._repository_tags(
            SimpleNamespace(tags='kato:repo:client'),
        )
        self.assertEqual(result, ['client'])

    def test_accepts_dict_tag_entries(self) -> None:
        # Line 429.
        result = RepositoryInventoryService._repository_tags(
            SimpleNamespace(tags=[{'name': 'kato:repo:backend'}]),
        )
        self.assertEqual(result, ['backend'])

    def test_skips_tag_with_empty_value_after_prefix(self) -> None:
        # Branch 448->438: ``if repository_tag:`` false branch — the
        # tag has the kato:repo: prefix but nothing after it, so the
        # extracted value is blank and the entry must be skipped.
        result = RepositoryInventoryService._repository_tags(
            SimpleNamespace(tags=[
                'kato:repo:',  # empty after prefix → skip
                'kato:repo:client',  # valid → include
            ]),
        )
        self.assertEqual(result, ['client'])


class ValidateLocalPathTests(unittest.TestCase):
    def test_raises_when_missing(self) -> None:
        # Line 444.
        with self.assertRaisesRegex(ValueError, 'missing local repository'):
            RepositoryInventoryService._validate_local_path(
                SimpleNamespace(id='r', local_path=''),
            )


class ValidateGitRemoteAuthTests(unittest.TestCase):
    def test_returns_silently_for_https_remote(self) -> None:
        RepositoryInventoryService._validate_git_remote_auth(
            SimpleNamespace(id='r', remote_url='https://github.com/o/r.git'),
        )

    def test_raises_when_ssh_executable_missing(self) -> None:
        # Line 454-457.
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.shutil.which',
            return_value=None,
        ):
            with self.assertRaisesRegex(ValueError, 'ssh executable'):
                RepositoryInventoryService._validate_git_remote_auth(
                    SimpleNamespace(id='r', remote_url='git@github.com:o/r.git'),
                )

    def test_returns_silently_on_windows(self) -> None:
        # Lines 464-465.
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.shutil.which',
            return_value='/usr/bin/ssh',
        ), patch.object(os, 'name', 'nt'):
            RepositoryInventoryService._validate_git_remote_auth(
                SimpleNamespace(id='r', remote_url='git@github.com:o/r.git'),
            )

    def test_raises_when_ssh_auth_sock_missing(self) -> None:
        # Lines 467-470.
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.shutil.which',
            return_value='/usr/bin/ssh',
        ), patch.object(os, 'name', 'posix'), \
           patch.dict(os.environ, {'SSH_AUTH_SOCK': ''}, clear=False):
            with self.assertRaisesRegex(ValueError, 'SSH_AUTH_SOCK is not configured'):
                RepositoryInventoryService._validate_git_remote_auth(
                    SimpleNamespace(id='r', remote_url='git@github.com:o/r.git'),
                )

    def test_raises_when_ssh_auth_sock_does_not_exist(self) -> None:
        # Lines 471-475.
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.shutil.which',
            return_value='/usr/bin/ssh',
        ), patch.object(os, 'name', 'posix'), \
           patch.dict(os.environ,
                      {'SSH_AUTH_SOCK': '/nonexistent/sock'}, clear=False), \
           patch('os.path.exists', return_value=False):
            with self.assertRaisesRegex(ValueError, 'SSH_AUTH_SOCK does not exist'):
                RepositoryInventoryService._validate_git_remote_auth(
                    SimpleNamespace(id='r', remote_url='git@github.com:o/r.git'),
                )

    def test_returns_silently_when_ssh_auth_sock_exists(self) -> None:
        # Branch 482->exit: ``if not os.path.exists(ssh_auth_sock):``
        # false branch — SSH_AUTH_SOCK points to a real path, so the
        # method must exit normally (no ValueError).
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.shutil.which',
            return_value='/usr/bin/ssh',
        ), patch.object(os, 'name', 'posix'), \
           patch.dict(os.environ,
                      {'SSH_AUTH_SOCK': '/tmp/ssh-agent.sock'}, clear=False), \
           patch('os.path.exists', return_value=True):
            # No exception.
            RepositoryInventoryService._validate_git_remote_auth(
                SimpleNamespace(id='r', remote_url='git@github.com:o/r.git'),
            )


class ValidateRepositoryGitAccessTests(unittest.TestCase):
    def test_translates_auth_failure(self) -> None:
        # Line 511-514: 'could not read Password' → 'missing git permissions'.
        # _run_git lives on the GitClientMixin used by RepositoryService;
        # for the inventory-only base class we attach a stub via setattr.
        service = _make_service()
        service._run_git = MagicMock(
            side_effect=RuntimeError(
                'git failed: could not read Password for url',
            ),
        )
        with self.assertRaisesRegex(RuntimeError, 'missing git permissions'):
            service._validate_repository_git_access(
                SimpleNamespace(id='r', local_path='/x'),
            )

    def test_translates_generic_failure(self) -> None:
        # Line 515-517.
        service = _make_service()
        service._run_git = MagicMock(
            side_effect=RuntimeError('git failed: protocol error'),
        )
        with self.assertRaisesRegex(RuntimeError, 'git validation failed'):
            service._validate_repository_git_access(
                SimpleNamespace(id='r', local_path='/x'),
            )


class ResolvedPullRequestProviderTests(unittest.TestCase):
    def test_raises_when_provider_unknown(self) -> None:
        # Line 554.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', provider='', provider_base_url='', remote_url='',
        )
        with self.assertRaisesRegex(ValueError, 'unable to determine'):
            service._resolved_pull_request_provider(repo)

    def test_uses_provider_base_url_when_provider_blank(self) -> None:
        # Lines 547-550.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', provider='',
            provider_base_url='https://github.com',
            remote_url='',
        )
        self.assertEqual(
            service._resolved_pull_request_provider(repo),
            'github',
        )

    def test_falls_back_to_remote_url(self) -> None:
        # Lines 551-553.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', provider='', provider_base_url='',
            remote_url='git@github.com:o/r.git',
        )
        self.assertEqual(
            service._resolved_pull_request_provider(repo),
            'github',
        )


class ValidatePullRequestApiValuesTests(unittest.TestCase):
    def test_raises_when_base_url_missing(self) -> None:
        # Lines 603-606.
        service = _make_service()
        with self.assertRaisesRegex(ValueError, 'missing pull request API base URL'):
            service._validate_pull_request_api_values(
                repository_id='r', provider='github',
                provider_base_url='', token='abc',
            )

    def test_raises_when_bitbucket_missing_api_email(self) -> None:
        # Lines 608-612.
        service = _make_service()
        with self.assertRaisesRegex(ValueError, 'missing Bitbucket API email'):
            service._validate_pull_request_api_values(
                repository_id='r', provider='bitbucket',
                provider_base_url='https://bitbucket.org',
                token='abc', api_email='',
            )

    def test_raises_when_token_missing(self) -> None:
        # Lines 613-615.
        service = _make_service()
        with self.assertRaises(ValueError):
            service._validate_pull_request_api_values(
                repository_id='r', provider='github',
                provider_base_url='https://api.github.com',
                token='',
            )


class PullRequestDataAccessIncompleteTests(unittest.TestCase):
    def test_raises_on_incomplete_config(self) -> None:
        # Lines 639-642.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', provider='github', provider_base_url='',
            owner='', repo_slug='', token='',
            remote_url='',
        )
        with self.assertRaisesRegex(ValueError, 'incomplete pull request configuration'):
            service._pull_request_data_access(repo)

    def test_raises_when_bitbucket_missing_api_email(self) -> None:
        # Lines 643-645.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', provider='bitbucket',
            provider_base_url='https://bitbucket.example/api',
            owner='w', repo_slug='r', token='abc',
            bitbucket_api_email='',
            destination_branch='main',
            remote_url='',
        )
        with self.assertRaisesRegex(ValueError, 'missing Bitbucket API email'):
            service._pull_request_data_access(repo)


class ReviewUrlTests(unittest.TestCase):
    def test_returns_empty_when_no_web_base_url(self) -> None:
        # Lines 677-678: cannot determine fallback URL → ''.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', remote_url='', provider='',
            owner='', repo_slug='',
            provider_base_url='',
        )
        self.assertEqual(service._review_url(repo, 'feat/x', 'main'), '')

    def test_uses_review_url_when_full_data_present(self) -> None:
        # Lines 666-674: success path with full data.
        service = _make_service()
        repo = SimpleNamespace(
            id='r',
            remote_url='https://github.com/o/r.git',
            provider='github',
            owner='o', repo_slug='r',
            provider_base_url='https://github.com',
        )
        url = service._review_url(repo, 'feat/x', 'main')
        self.assertTrue(url.startswith('https://github.com/'))


class ResolveTaskRepositoriesDedupTests(unittest.TestCase):
    def test_dedups_repositories_by_id_when_two_tags_resolve_to_same(
        self,
    ) -> None:
        # Line 170: ``if repo_id in seen_ids: continue``.
        repo = SimpleNamespace(
            id='client', display_name='client', repo_slug='client',
            aliases=['client', 'client-alias'], local_path='/x',
        )
        service = _make_service(SimpleNamespace(repositories=[repo]))
        task = SimpleNamespace(
            id='PROJ-1',
            tags=['kato:repo:client', 'kato:repo:client-alias'],
            description='',
        )
        result = service.resolve_task_repositories(task)
        # Both tags resolve to the same repo → only one entry returned.
        self.assertEqual(len(result), 1)


class LoadRepositoriesConfiguredOverridesDiscoveryTests(unittest.TestCase):
    def test_configured_repositories_short_circuit_discovery(self) -> None:
        # Line 307: ``if configured_repositories: return``.
        configured = [SimpleNamespace(id='r')]
        service = _make_service()
        result = service._load_repositories(
            SimpleNamespace(repositories=configured),
        )
        self.assertEqual(result, configured)


class LooksLikeRepositorySettingsTests(unittest.TestCase):
    def test_returns_false_for_none(self) -> None:
        # Lines 335-336.
        self.assertFalse(
            RepositoryInventoryService._looks_like_repository_settings(None),
        )


class PrepareRepositoryGitAuthTests(unittest.TestCase):
    def test_sets_bitbucket_username_when_resolved(self) -> None:
        # Line 493.
        from kato_core_lib.data_layers.data.fields import RepositoryFields
        service = _make_service(SimpleNamespace(
            repositories=[],
            bitbucket_issues=SimpleNamespace(
                base_url='', token='', username='alice', api_email='',
            ),
        ))
        repo = SimpleNamespace(
            id='r', provider='bitbucket', username='', local_path='/x',
        )
        service._prepare_repository_git_auth(repo)
        self.assertEqual(
            getattr(repo, RepositoryFields.BITBUCKET_USERNAME), 'alice',
        )


class ResolvedBitbucketUsernameTests(unittest.TestCase):
    def test_returns_repo_username_when_set(self) -> None:
        # Line 582-583: ``if username: return username``.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', username='alice', bitbucket_username='',
        )
        self.assertEqual(
            service._resolved_bitbucket_username(repo),
            'alice',
        )

    def test_falls_back_to_provider_defaults(self) -> None:
        # Line 583-584.
        service = _make_service(SimpleNamespace(
            repositories=[],
            bitbucket_issues=SimpleNamespace(
                base_url='', token='',
                username='default-user', api_email='',
            ),
        ))
        # Repo without username — fallback to defaults.
        repo = SimpleNamespace(
            id='r', provider='bitbucket', username='',
            bitbucket_username='',
        )
        self.assertEqual(
            service._resolved_bitbucket_username(repo),
            'default-user',
        )


class ReviewUrlFallbackProviderTests(unittest.TestCase):
    def test_uses_fallback_web_base_with_provider_inferred_from_base_url(
        self,
    ) -> None:
        # Lines 679-690: web_base_url + provider inferred from
        # provider_base_url → review_url_for_remote.
        service = _make_service()
        # Need a repo with: no remote_url, no provider attribute, owner+repo_slug set,
        # AND a provider_base_url that infers a provider.
        repo = SimpleNamespace(
            id='r', remote_url='', provider='',
            owner='o', repo_slug='r',
            provider_base_url='https://github.example',
        )
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.'
            'fallback_web_base_url',
            return_value='https://web.example',
        ):
            url = service._review_url(repo, 'feat/x', 'main')
        # Hits the review_url_for_remote branch on line 683.
        self.assertTrue(url)

    def test_falls_back_to_repository_path_when_provider_unknown(self) -> None:
        # Lines 691-692: provider can't be inferred → raw path fallback.
        service = _make_service()
        repo = SimpleNamespace(
            id='r', remote_url='', provider='',
            owner='o', repo_slug='r',
            provider_base_url='',
        )
        with patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.'
            'fallback_web_base_url',
            return_value='https://web.example',
        ), patch(
            'kato_core_lib.data_layers.service.repository_inventory_service.'
            'provider_from_url_string',
            return_value='',
        ):
            url = service._review_url(repo, 'feat/x', 'main')
        self.assertEqual(url, 'https://web.example/o/r')


class GetRepositoryDirectFolderFallbackTests(unittest.TestCase):
    """Cover the fallback in ``get_repository`` (line 278): when the
    inventory walk doesn't include the id, try
    ``_discover_repository_at_named_folder`` directly. This is the
    fix we added for the Windows operator case where the inventory
    walk found the repo via the approvals UI but the strict-id
    lookup missed it."""

    def test_falls_back_to_direct_folder_lookup_when_inventory_missed_repo(self) -> None:
        service = _make_service()
        # The inventory has no repos, so the strict id-match loop
        # finishes without a return. The fallback discovers the
        # named folder.
        stub_repo = SimpleNamespace(
            id='ob-love-admin-client',
            local_path='/some/path/ob-love-admin-client',
        )
        with patch.object(
            service, '_ensure_repositories', return_value=[],
        ), patch.object(
            service, '_discover_repository_at_named_folder',
            return_value=stub_repo,
        ) as discover:
            result = service.get_repository('ob-love-admin-client')
        discover.assert_called_once_with('ob-love-admin-client')
        self.assertIs(result, stub_repo)


if __name__ == '__main__':
    unittest.main()
