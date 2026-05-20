import inspect
import unittest

from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib
from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_client import BitbucketClient
from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_issues_client import (
    BitbucketIssuesClient,
)
from gitlab_core_lib.gitlab_core_lib.client.gitlab_client import GitLabClient
from gitlab_core_lib.gitlab_core_lib.client.gitlab_issues_client import GitLabIssuesClient
from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib
from github_core_lib.github_core_lib.client.github_client import GitHubClient
from github_core_lib.github_core_lib.client.github_issues_client import GitHubIssuesClient
from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib
from omegaconf import OmegaConf
from vcs_provider_contracts.vcs_provider_contracts.issue import Issue
from vcs_provider_contracts.vcs_provider_contracts.issue_provider import IssueProvider
from vcs_provider_contracts.vcs_provider_contracts.pull_request import PullRequest
from vcs_provider_contracts.vcs_provider_contracts.pull_request_provider import PullRequestProvider
from vcs_provider_contracts.vcs_provider_contracts.review_comment import ReviewComment


class ContractPullRequestProvider(object):
    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        return None

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> PullRequest:
        return PullRequest(id='1', title=title, url='https://example.com/pr/1')

    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        return [ReviewComment(pull_request_id=pull_request_id)]

    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[PullRequest]:
        return [PullRequest(id='1', title=title_prefix)]

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        return None

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        return None


class ContractIssueProvider(object):
    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        return None

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Issue]:
        return [Issue(id='ISSUE-1', title='Example')]

    def add_comment(self, issue_id: str, comment: str) -> None:
        return None

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        return None

    def add_tag(self, issue_id: str, label_name: str) -> None:
        return None

    def remove_tag(self, issue_id: str, label_name: str) -> None:
        return None


class VcsProviderContractsTests(unittest.TestCase):
    def test_pull_request_contract_runtime_check_accepts_matching_provider(self) -> None:
        self.assertIsInstance(ContractPullRequestProvider(), PullRequestProvider)
        self.assertIsInstance(BitbucketClient('https://api.bitbucket.org/2.0', 'bb-token'), PullRequestProvider)
        self.assertIsInstance(GitHubClient('https://api.github.com', 'gh-token'), PullRequestProvider)
        self.assertIsInstance(GitLabClient('https://gitlab.example/api/v4', 'gl-token'), PullRequestProvider)

    def test_issue_contract_runtime_check_accepts_matching_provider(self) -> None:
        self.assertIsInstance(ContractIssueProvider(), IssueProvider)
        self.assertIsInstance(
            BitbucketIssuesClient('https://api.bitbucket.org/2.0', 'bb-token', 'workspace', 'repo'),
            IssueProvider,
        )
        self.assertIsInstance(
            GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo'),
            IssueProvider,
        )
        self.assertIsInstance(
            GitLabIssuesClient('https://gitlab.example/api/v4', 'gl-token', 'group/repo'),
            IssueProvider,
        )

    def test_pull_request_provider_signature_names_are_stable(self) -> None:
        self.assertEqual(
            list(inspect.signature(PullRequestProvider.create_pull_request).parameters),
            [
                'self',
                'title',
                'source_branch',
                'repo_owner',
                'repo_slug',
                'destination_branch',
                'description',
            ],
        )
        self.assertEqual(
            list(inspect.signature(PullRequestProvider.find_pull_requests).parameters),
            ['self', 'repo_owner', 'repo_slug', 'source_branch', 'title_prefix'],
        )

    def test_issue_provider_signature_names_are_stable(self) -> None:
        self.assertEqual(
            list(inspect.signature(IssueProvider.get_assigned_tasks).parameters),
            ['self', 'project', 'assignee', 'states'],
        )
        self.assertEqual(
            list(inspect.signature(IssueProvider.move_issue_to_state).parameters),
            ['self', 'issue_id', 'field_name', 'state_name'],
        )

    def test_contract_records_are_provider_neutral(self) -> None:
        pull_request = PullRequest(id='17', title='Fix', url='https://example.com/pr/17')
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please fix',
            resolution_target_id='thread-1',
            resolution_target_type='thread',
            resolvable=True,
        )
        issue = Issue(id='ISSUE-1', title='Task', body='Body', state='open', labels=('bug',))

        self.assertEqual(pull_request.id, '17')
        self.assertEqual(comment.resolution_target_id, 'thread-1')
        self.assertEqual(issue.labels, ('bug',))

    def test_github_core_lib_composes_both_clients(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'github_core_lib': {
                        'base_url': 'https://api.github.com',
                        'token': 'gh-token',
                        'owner': 'octo',
                        'repo': 'repo',
                        'max_retries': 3,
                    },
                },
            }
        )
        github = GitHubCoreLib(cfg)

        self.assertIsInstance(github.pull_request, GitHubClient)
        self.assertIsInstance(github.issue, GitHubIssuesClient)
        self.assertEqual(github.pull_request.max_retries, 3)
        self.assertEqual(github.issue.max_retries, 3)

    def test_github_core_lib_accepts_repository_config_slug_name(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'github_core_lib': {
                        'base_url': 'https://api.github.com',
                        'token': 'gh-token',
                        'owner': 'octo',
                        'repo_slug': 'repo',
                        'max_retries': 4,
                    },
                },
            }
        )
        github = GitHubCoreLib(cfg)

        self.assertIsInstance(github.pull_request, GitHubClient)
        self.assertIsInstance(github.issue, GitHubIssuesClient)
        self.assertEqual(github.issue.max_retries, 4)

    def test_gitlab_core_lib_composes_both_clients(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'gitlab_core_lib': {
                        'base_url': 'https://gitlab.example/api/v4',
                        'token': 'gl-token',
                        'project': 'group/repo',
                        'max_retries': 3,
                    },
                },
            }
        )
        gitlab = GitLabCoreLib(cfg)

        self.assertIsInstance(gitlab.pull_request, GitLabClient)
        self.assertIsInstance(gitlab.issue, GitLabIssuesClient)
        self.assertEqual(gitlab.pull_request.max_retries, 3)
        self.assertEqual(gitlab.issue.max_retries, 3)

    def test_gitlab_core_lib_uses_project_config(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'gitlab_core_lib': {
                        'base_url': 'https://gitlab.example/api/v4',
                        'token': 'gl-token',
                        'project': 'group/subgroup/repo',
                        'max_retries': 4,
                    },
                },
            }
        )
        gitlab = GitLabCoreLib(cfg)

        self.assertIsInstance(gitlab.pull_request, GitLabClient)
        self.assertIsInstance(gitlab.issue, GitLabIssuesClient)
        self.assertEqual(gitlab.issue.max_retries, 4)

    def test_bitbucket_core_lib_composes_both_clients(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'bitbucket_core_lib': {
                        'base_url': 'https://api.bitbucket.org/2.0',
                        'token': 'bb-token',
                        'username': 'bb-user',
                        'api_email': 'bb-api@example.com',
                        'workspace': 'workspace',
                        'repo_slug': 'repo',
                        'max_retries': 3,
                    },
                },
            }
        )
        bitbucket = BitbucketCoreLib(cfg)

        self.assertIsInstance(bitbucket.pull_request, BitbucketClient)
        self.assertIsInstance(bitbucket.issue, BitbucketIssuesClient)
        self.assertEqual(bitbucket.pull_request.max_retries, 3)
        self.assertEqual(bitbucket.issue.max_retries, 3)

    def test_bitbucket_core_lib_accepts_repository_config_slug_name(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'bitbucket_core_lib': {
                        'base_url': 'https://api.bitbucket.org/2.0',
                        'token': 'bb-token',
                        'username': 'bb-user',
                        'api_email': 'bb-api@example.com',
                        'workspace': 'workspace',
                        'repo_slug': 'repo',
                        'max_retries': 4,
                    },
                },
            }
        )
        bitbucket = BitbucketCoreLib(cfg)

        self.assertIsInstance(bitbucket.pull_request, BitbucketClient)
        self.assertIsInstance(bitbucket.issue, BitbucketIssuesClient)
        self.assertEqual(bitbucket.issue.max_retries, 4)


# ---------------------------------------------------------------------------
# Cover the ``raise NotImplementedError`` bodies in the Protocol classes
# ---------------------------------------------------------------------------


class ProtocolMethodNotImplementedTests(unittest.TestCase):
    """The ``raise NotImplementedError`` bodies inside Protocol classes
    exist so a subclass that forgets to override gets a clear error
    rather than a silent no-op. Call each as an unbound method to
    cover the body — ``self`` doesn't have to be an instance since
    Python only enforces that at call sites that go through a
    bound-method descriptor."""

    def test_pull_request_provider_validate_connection(self) -> None:
        with self.assertRaises(NotImplementedError):
            PullRequestProvider.validate_connection(None, 'o', 'r')

    def test_pull_request_provider_create_pull_request(self) -> None:
        with self.assertRaises(NotImplementedError):
            PullRequestProvider.create_pull_request(
                None, 'title', 'src', 'o', 'r',
            )

    def test_pull_request_provider_list_pull_request_comments(self) -> None:
        with self.assertRaises(NotImplementedError):
            PullRequestProvider.list_pull_request_comments(None, 'o', 'r', '1')

    def test_pull_request_provider_find_pull_requests(self) -> None:
        with self.assertRaises(NotImplementedError):
            PullRequestProvider.find_pull_requests(None, 'o', 'r')

    def test_pull_request_provider_reply_to_review_comment(self) -> None:
        with self.assertRaises(NotImplementedError):
            PullRequestProvider.reply_to_review_comment(
                None, 'o', 'r', ReviewComment(
                    pull_request_id='1', comment_id='1', author='a', body='b',
                ), 'reply',
            )

    def test_pull_request_provider_resolve_review_comment(self) -> None:
        with self.assertRaises(NotImplementedError):
            PullRequestProvider.resolve_review_comment(
                None, 'o', 'r', ReviewComment(
                    pull_request_id='1', comment_id='1', author='a', body='b',
                ),
            )

    def test_issue_provider_validate_connection(self) -> None:
        with self.assertRaises(NotImplementedError):
            IssueProvider.validate_connection(None, 'p', 'a', ['open'])

    def test_issue_provider_get_assigned_tasks(self) -> None:
        with self.assertRaises(NotImplementedError):
            IssueProvider.get_assigned_tasks(None, 'p', 'a', ['open'])

    def test_issue_provider_add_comment(self) -> None:
        with self.assertRaises(NotImplementedError):
            IssueProvider.add_comment(None, 'issue-1', 'body')

    def test_issue_provider_move_issue_to_state(self) -> None:
        with self.assertRaises(NotImplementedError):
            IssueProvider.move_issue_to_state(None, 'issue-1', 'field', 'state')

    def test_issue_provider_add_tag(self) -> None:
        with self.assertRaises(NotImplementedError):
            IssueProvider.add_tag(None, 'issue-1', 'kato:ready')

    def test_issue_provider_remove_tag(self) -> None:
        with self.assertRaises(NotImplementedError):
            IssueProvider.remove_tag(None, 'issue-1', 'kato:ready')


class IssueCommentDataclassTests(unittest.TestCase):
    """``IssueComment`` is a simple frozen dataclass — touch it once
    to cover the module-level dataclass declaration."""

    def test_default_construction(self) -> None:
        from vcs_provider_contracts.vcs_provider_contracts.issue_comment import (
            IssueComment,
        )
        comment = IssueComment()
        self.assertEqual(comment.author, '')
        self.assertEqual(comment.body, '')

    def test_explicit_construction(self) -> None:
        from vcs_provider_contracts.vcs_provider_contracts.issue_comment import (
            IssueComment,
        )
        comment = IssueComment(author='reviewer', body='lgtm')
        self.assertEqual(comment.author, 'reviewer')
        self.assertEqual(comment.body, 'lgtm')
