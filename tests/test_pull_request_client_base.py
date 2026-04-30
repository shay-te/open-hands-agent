import unittest


from kato.client.bitbucket.client import BitbucketClient
from kato.client.github.client import GitHubClient
from kato.client.gitlab.client import GitLabClient
from kato.client.pull_request_client_base import PullRequestClientBase
from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields


class PullRequestClientBaseTests(unittest.TestCase):
    def test_cannot_instantiate_abstract_base_directly(self) -> None:
        with self.assertRaises(TypeError):
            PullRequestClientBase('https://example.com', 'token', timeout=30)

    def test_all_repository_clients_implement_shared_base_contract(self) -> None:
        self.assertTrue(issubclass(BitbucketClient, PullRequestClientBase))
        self.assertTrue(issubclass(GitHubClient, PullRequestClientBase))
        self.assertTrue(issubclass(GitLabClient, PullRequestClientBase))

    def test_normalized_pull_request_helper_returns_expected_shape(self) -> None:
        self.assertEqual(
            PullRequestClientBase._normalized_pull_request(
                {
                    'number': 17,
                    PullRequestFields.TITLE: ' PROJ-1: Fix bug ',
                },
                id_key='number',
                url=' https://example.com/pr/17 ',
            ),
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://example.com/pr/17',
            },
        )

    def test_normalized_pull_request_helper_rejects_missing_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
            PullRequestClientBase._normalized_pull_request(
                {PullRequestFields.TITLE: 'missing id'},
                id_key='number',
            )

    def test_review_comment_helper_sets_resolution_metadata(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id=' 17 ',
            comment_id=' 99 ',
            author=' reviewer ',
            body=' Please rename this variable. ',
            resolution_target_id=' thread-1 ',
            resolution_target_type='thread',
        )

        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'reviewer')
        self.assertEqual(comment.body, 'Please rename this variable.')
        self.assertEqual(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID),
            'thread-1',
        )
        self.assertEqual(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE),
            'thread',
        )
        self.assertTrue(getattr(comment, ReviewCommentFields.RESOLVABLE))
