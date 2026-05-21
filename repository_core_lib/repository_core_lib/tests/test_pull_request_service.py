from __future__ import annotations

import unittest
from unittest.mock import Mock

from repository_core_lib.repository_core_lib.pull_request_service import PullRequestService
from repository_core_lib.repository_core_lib.platform import Platform


class PullRequestServiceAttributeTests(unittest.TestCase):
    def test_provider_name_is_repository(self):
        self.assertEqual(PullRequestService.provider_name, 'repository')


class PullRequestServiceValidateConnectionTests(unittest.TestCase):
    def test_routes_to_github_client(self):
        service, factory, client = _service_with_client()
        service.validate_connection(Platform.GITHUB, repo_owner='octo', repo_slug='repo')
        factory.get.assert_called_once_with(Platform.GITHUB)
        client.validate_connection.assert_called_once_with(repo_owner='octo', repo_slug='repo')

    def test_routes_to_gitlab_client(self):
        service, factory, client = _service_with_client()
        service.validate_connection(Platform.GITLAB, repo_owner='group', repo_slug='repo')
        factory.get.assert_called_once_with(Platform.GITLAB)
        client.validate_connection.assert_called_once_with(repo_owner='group', repo_slug='repo')

    def test_routes_to_bitbucket_client(self):
        service, factory, client = _service_with_client()
        service.validate_connection(Platform.BITBUCKET, repo_owner='workspace', repo_slug='repo')
        factory.get.assert_called_once_with(Platform.BITBUCKET)
        client.validate_connection.assert_called_once_with(repo_owner='workspace', repo_slug='repo')


class PullRequestServiceCreatePullRequestTests(unittest.TestCase):
    def test_routes_to_github_client_with_all_args(self):
        service, factory, client = _service_with_client()
        client.create_pull_request.return_value = {'id': '17'}

        result = service.create_pull_request(
            Platform.GITHUB,
            title='PROJ-1: fix it already',
            source_branch='feature/proj-1',
            repo_owner='octo',
            repo_slug='repo',
            destination_branch='main',
            description='Ready for review',
        )

        self.assertEqual(result, {'id': '17'})
        factory.get.assert_called_once_with(Platform.GITHUB)
        client.create_pull_request.assert_called_once_with(
            title='PROJ-1: fix it already',
            source_branch='feature/proj-1',
            repo_owner='octo',
            repo_slug='repo',
            destination_branch='main',
            description='Ready for review',
        )

    def test_default_destination_branch_is_none(self):
        service, _, client = _service_with_client()
        client.create_pull_request.return_value = {}
        service.create_pull_request(
            Platform.GITHUB,
            title='Fix',
            source_branch='feature/fix',
            repo_owner='octo',
            repo_slug='repo',
        )
        _, kwargs = client.create_pull_request.call_args
        self.assertIsNone(kwargs['destination_branch'])

    def test_default_description_is_empty_string(self):
        service, _, client = _service_with_client()
        client.create_pull_request.return_value = {}
        service.create_pull_request(
            Platform.GITHUB,
            title='Fix',
            source_branch='feature/fix',
            repo_owner='octo',
            repo_slug='repo',
        )
        _, kwargs = client.create_pull_request.call_args
        self.assertEqual(kwargs['description'], '')

    def test_routes_to_gitlab_client(self):
        service, factory, client = _service_with_client()
        client.create_pull_request.return_value = {'id': '5'}
        service.create_pull_request(
            Platform.GITLAB,
            title='MR title',
            source_branch='feature/branch',
            repo_owner='group',
            repo_slug='repo',
        )
        factory.get.assert_called_once_with(Platform.GITLAB)

    def test_routes_to_bitbucket_client(self):
        service, factory, client = _service_with_client()
        client.create_pull_request.return_value = {'id': '3'}
        service.create_pull_request(
            Platform.BITBUCKET,
            title='PR title',
            source_branch='feature/branch',
            repo_owner='workspace',
            repo_slug='repo',
        )
        factory.get.assert_called_once_with(Platform.BITBUCKET)


class PullRequestServiceListCommentsTests(unittest.TestCase):
    def test_routes_to_client(self):
        service, factory, client = _service_with_client()
        client.list_pull_request_comments.return_value = ['comment']

        result = service.list_pull_request_comments(
            Platform.GITLAB,
            repo_owner='group',
            repo_slug='repo',
            pull_request_id='17',
        )

        self.assertEqual(result, ['comment'])
        factory.get.assert_called_once_with(Platform.GITLAB)
        client.list_pull_request_comments.assert_called_once_with(
            repo_owner='group',
            repo_slug='repo',
            pull_request_id='17',
        )

    def test_returns_empty_list_when_client_returns_empty(self):
        service, _, client = _service_with_client()
        client.list_pull_request_comments.return_value = []
        result = service.list_pull_request_comments(
            Platform.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            pull_request_id='1',
        )
        self.assertEqual(result, [])

    def test_routes_bitbucket(self):
        service, factory, client = _service_with_client()
        client.list_pull_request_comments.return_value = []
        service.list_pull_request_comments(
            Platform.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
            pull_request_id='42',
        )
        factory.get.assert_called_once_with(Platform.BITBUCKET)


class PullRequestServiceFindPullRequestsTests(unittest.TestCase):
    def test_routes_to_client_with_all_args(self):
        service, factory, client = _service_with_client()
        client.find_pull_requests.return_value = ['pr']

        result = service.find_pull_requests(
            Platform.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
            source_branch='feature/proj-1',
            title_prefix='PROJ-1',
        )

        self.assertEqual(result, ['pr'])
        factory.get.assert_called_once_with(Platform.BITBUCKET)
        client.find_pull_requests.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            source_branch='feature/proj-1',
            title_prefix='PROJ-1',
        )

    def test_default_source_branch_is_empty_string(self):
        service, _, client = _service_with_client()
        client.find_pull_requests.return_value = []
        service.find_pull_requests(
            Platform.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            title_prefix='FIX',
        )
        _, kwargs = client.find_pull_requests.call_args
        self.assertEqual(kwargs['source_branch'], '')

    def test_default_title_prefix_is_empty_string(self):
        service, _, client = _service_with_client()
        client.find_pull_requests.return_value = []
        service.find_pull_requests(
            Platform.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            source_branch='feature/branch',
        )
        _, kwargs = client.find_pull_requests.call_args
        self.assertEqual(kwargs['title_prefix'], '')

    def test_routes_gitlab(self):
        service, factory, client = _service_with_client()
        client.find_pull_requests.return_value = []
        service.find_pull_requests(Platform.GITLAB, repo_owner='group', repo_slug='repo')
        factory.get.assert_called_once_with(Platform.GITLAB)


class PullRequestServiceReviewCommentTests(unittest.TestCase):
    def test_reply_to_review_comment_routes_to_client(self):
        service, factory, client = _service_with_client()
        comment = Mock()
        service.reply_to_review_comment(
            Platform.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            comment=comment,
            body='Done.',
        )
        factory.get.assert_called_once_with(Platform.GITHUB)
        client.reply_to_review_comment.assert_called_once_with(
            repo_owner='octo',
            repo_slug='repo',
            comment=comment,
            body='Done.',
        )

    def test_resolve_review_comment_routes_to_client(self):
        service, factory, client = _service_with_client()
        comment = Mock()
        service.resolve_review_comment(
            Platform.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
        )
        factory.get.assert_called_once_with(Platform.BITBUCKET)
        client.resolve_review_comment.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
        )

    def test_reply_routes_gitlab(self):
        service, factory, client = _service_with_client()
        service.reply_to_review_comment(
            Platform.GITLAB,
            repo_owner='group',
            repo_slug='repo',
            comment=Mock(),
            body='LGTM.',
        )
        factory.get.assert_called_once_with(Platform.GITLAB)

    def test_resolve_routes_github(self):
        service, factory, client = _service_with_client()
        service.resolve_review_comment(
            Platform.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            comment=Mock(),
        )
        factory.get.assert_called_once_with(Platform.GITHUB)


class PullRequestServiceFactoryCallTests(unittest.TestCase):
    def test_factory_called_once_per_operation(self):
        service, factory, _ = _service_with_client()
        service.validate_connection(Platform.GITHUB, repo_owner='o', repo_slug='r')
        self.assertEqual(factory.get.call_count, 1)

    def test_each_operation_calls_factory_separately(self):
        service, factory, client = _service_with_client()
        bitbucket_client = Mock()
        factory.get.side_effect = [client, bitbucket_client]

        service.validate_connection(Platform.GITHUB, repo_owner='octo', repo_slug='repo')
        service.validate_connection(Platform.BITBUCKET, repo_owner='octo', repo_slug='repo')

        factory.get.assert_any_call(Platform.GITHUB)
        factory.get.assert_any_call(Platform.BITBUCKET)
        self.assertEqual(factory.get.call_count, 2)


def _service_with_client():
    factory = Mock()
    client = Mock()
    factory.get.return_value = client
    return PullRequestService(factory), factory, client
