"""End-to-end flow tests for repository_core_lib.

Each test exercises a realistic multi-step scenario using only objects
internal to this lib plus injected mock provider clients — no other
core-libs are imported.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from omegaconf import OmegaConf

from repository_core_lib.repository_core_lib.platform import Platform
from repository_core_lib.repository_core_lib.repository_core_lib import RepositoryCoreLib


def _mock_provider_client():
    client = Mock()
    client.validate_connection.return_value = None
    client.create_pull_request.return_value = {'id': '99', 'title': 'fix it already', 'url': 'https://example.com/pr/99'}
    client.find_pull_requests.return_value = [{'id': '99', 'title': 'fix it already', 'url': ''}]
    client.list_pull_request_comments.return_value = []
    client.reply_to_review_comment.return_value = None
    client.resolve_review_comment.return_value = None
    return client


class GitHubCreateAndFindFlowTests(unittest.TestCase):
    def setUp(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'gh-token',
            'owner': 'octo',
            'repo_slug': 'repo',
        })
        self.provider_client = _mock_provider_client()
        self.core_lib = RepositoryCoreLib(
            cfg, 3,
            github_client_factory=lambda _: self.provider_client,
        )

    def test_validate_connection_then_create_pr(self):
        service = self.core_lib.pull_request

        service.validate_connection(Platform.GITHUB, repo_owner='octo', repo_slug='repo')
        pr = service.create_pull_request(
            Platform.GITHUB,
            title='PROJ-1: fix it already',
            source_branch='feature/proj-1',
            repo_owner='octo',
            repo_slug='repo',
            destination_branch='main',
            description='Fixes the bug',
        )

        self.assertEqual(pr['id'], '99')
        self.provider_client.validate_connection.assert_called_once()
        self.provider_client.create_pull_request.assert_called_once_with(
            title='PROJ-1: fix it already',
            source_branch='feature/proj-1',
            repo_owner='octo',
            repo_slug='repo',
            destination_branch='main',
            description='Fixes the bug',
        )

    def test_create_pr_then_find_pr(self):
        service = self.core_lib.pull_request

        service.create_pull_request(
            Platform.GITHUB,
            title='PROJ-2: New feature',
            source_branch='feature/proj-2',
            repo_owner='octo',
            repo_slug='repo',
        )
        prs = service.find_pull_requests(
            Platform.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            source_branch='feature/proj-2',
        )

        self.assertIsInstance(prs, list)
        self.provider_client.create_pull_request.assert_called_once()
        self.provider_client.find_pull_requests.assert_called_once_with(
            repo_owner='octo',
            repo_slug='repo',
            source_branch='feature/proj-2',
            title_prefix='',
        )


class ReviewCommentLifecycleFlowTests(unittest.TestCase):
    def setUp(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.bitbucket.org/2.0',
            'token': 'bb-token',
            'username': 'bb-user',
            'workspace': 'workspace',
            'repo_slug': 'repo',
        })
        self.provider_client = _mock_provider_client()
        self.core_lib = RepositoryCoreLib(
            cfg, 3,
            bitbucket_client_factory=lambda _: self.provider_client,
        )

    def test_list_comments_then_reply_then_resolve(self):
        comment = Mock()
        self.provider_client.list_pull_request_comments.return_value = [comment]
        service = self.core_lib.pull_request

        comments = service.list_pull_request_comments(
            Platform.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
            pull_request_id='42',
        )
        for c in comments:
            service.reply_to_review_comment(
                Platform.BITBUCKET,
                repo_owner='workspace',
                repo_slug='repo',
                comment=c,
                body='Addressed in latest commit.',
            )
            service.resolve_review_comment(
                Platform.BITBUCKET,
                repo_owner='workspace',
                repo_slug='repo',
                comment=c,
            )

        self.provider_client.list_pull_request_comments.assert_called_once()
        self.provider_client.reply_to_review_comment.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
            body='Addressed in latest commit.',
        )
        self.provider_client.resolve_review_comment.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
        )


class MultiPlatformRoutingFlowTests(unittest.TestCase):
    def setUp(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'token',
        })
        self.github_client = _mock_provider_client()
        self.gitlab_client = _mock_provider_client()
        self.bitbucket_client = _mock_provider_client()
        self.core_lib = RepositoryCoreLib(
            cfg, 3,
            github_client_factory=lambda _: self.github_client,
            gitlab_client_factory=lambda _: self.gitlab_client,
            bitbucket_client_factory=lambda _: self.bitbucket_client,
        )

    def test_each_platform_routes_to_correct_client(self):
        service = self.core_lib.pull_request

        service.validate_connection(Platform.GITHUB, repo_owner='octo', repo_slug='repo')
        service.validate_connection(Platform.GITLAB, repo_owner='group', repo_slug='repo')
        service.validate_connection(Platform.BITBUCKET, repo_owner='workspace', repo_slug='repo')

        self.github_client.validate_connection.assert_called_once()
        self.gitlab_client.validate_connection.assert_called_once()
        self.bitbucket_client.validate_connection.assert_called_once()

    def test_operations_on_different_platforms_do_not_interfere(self):
        service = self.core_lib.pull_request

        self.github_client.create_pull_request.return_value = {'id': 'gh-1', 'title': 'GH PR', 'url': ''}
        self.gitlab_client.create_pull_request.return_value = {'id': 'gl-1', 'title': 'GL MR', 'url': ''}

        gh_pr = service.create_pull_request(
            Platform.GITHUB,
            title='GH PR',
            source_branch='feature/a',
            repo_owner='octo',
            repo_slug='repo',
        )
        gl_mr = service.create_pull_request(
            Platform.GITLAB,
            title='GL MR',
            source_branch='feature/b',
            repo_owner='group',
            repo_slug='repo',
        )

        self.assertEqual(gh_pr['id'], 'gh-1')
        self.assertEqual(gl_mr['id'], 'gl-1')
        self.gitlab_client.create_pull_request.assert_called_once()
        self.bitbucket_client.create_pull_request.assert_not_called()


class PlatformDetectionToServiceFlowTests(unittest.TestCase):
    def test_detect_github_url_then_validate_connection(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'token',
        })
        provider_client = _mock_provider_client()
        core_lib = RepositoryCoreLib(
            cfg, 3,
            github_client_factory=lambda _: provider_client,
        )

        platform = Platform.from_base_url(cfg.base_url)
        core_lib.pull_request.validate_connection(
            platform,
            repo_owner='octo',
            repo_slug='repo',
        )

        self.assertEqual(platform, Platform.GITHUB)
        provider_client.validate_connection.assert_called_once_with(
            repo_owner='octo',
            repo_slug='repo',
        )

    def test_detect_bitbucket_url_then_create_pr(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.bitbucket.org/2.0',
            'token': 'token',
            'username': 'user',
            'workspace': 'ws',
            'repo_slug': 'repo',
        })
        provider_client = _mock_provider_client()
        provider_client.create_pull_request.return_value = {'id': 'bb-5', 'title': 'Fix', 'url': ''}
        core_lib = RepositoryCoreLib(
            cfg, 3,
            bitbucket_client_factory=lambda _: provider_client,
        )

        platform = Platform.from_base_url(cfg.base_url)
        pr = core_lib.pull_request.create_pull_request(
            platform,
            title='Fix',
            source_branch='feature/fix',
            repo_owner='ws',
            repo_slug='repo',
        )

        self.assertEqual(platform, Platform.BITBUCKET)
        self.assertEqual(pr['id'], 'bb-5')

    def test_detect_gitlab_url_then_full_pr_workflow(self):
        cfg = OmegaConf.create({
            'base_url': 'https://gitlab.example/api/v4',
            'token': 'token',
        })
        comment = Mock()
        provider_client = _mock_provider_client()
        provider_client.create_pull_request.return_value = {'id': 'gl-7', 'title': 'feat', 'url': ''}
        provider_client.list_pull_request_comments.return_value = [comment]
        core_lib = RepositoryCoreLib(
            cfg, 3,
            gitlab_client_factory=lambda _: provider_client,
        )

        platform = Platform.from_base_url(cfg.base_url)
        service = core_lib.pull_request

        service.validate_connection(platform, repo_owner='group', repo_slug='repo')
        pr = service.create_pull_request(
            platform,
            title='feat: add x',
            source_branch='feature/x',
            repo_owner='group',
            repo_slug='repo',
        )
        comments = service.list_pull_request_comments(
            platform,
            repo_owner='group',
            repo_slug='repo',
            pull_request_id=pr['id'],
        )
        for c in comments:
            service.reply_to_review_comment(
                platform, repo_owner='group', repo_slug='repo', comment=c, body='Done.',
            )
            service.resolve_review_comment(
                platform, repo_owner='group', repo_slug='repo', comment=c,
            )

        self.assertEqual(platform, Platform.GITLAB)
        self.assertEqual(pr['id'], 'gl-7')
        provider_client.validate_connection.assert_called_once()
        provider_client.create_pull_request.assert_called_once()
        provider_client.list_pull_request_comments.assert_called_once()
        provider_client.reply_to_review_comment.assert_called_once()
        provider_client.resolve_review_comment.assert_called_once()
