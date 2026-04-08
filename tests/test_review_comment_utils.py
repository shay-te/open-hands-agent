import unittest

from kato.data_layers.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from kato.data_layers.data.review_comment import ReviewComment
from kato.helpers.review_comment_utils import (
    normalize_comment_context,
    review_comment_from_payload,
)


class ReviewCommentUtilsTests(unittest.TestCase):
    def test_review_comment_from_payload_builds_entity(self) -> None:
        comment = review_comment_from_payload(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'reviewer',
                ReviewCommentFields.BODY: 'Please rename this variable.',
            }
        )

        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'reviewer')
        self.assertEqual(comment.body, 'Please rename this variable.')
        self.assertEqual(getattr(comment, PullRequestFields.REPOSITORY_ID), 'client')

    def test_review_comment_from_payload_raises_value_error_for_invalid_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid review comment payload'):
            review_comment_from_payload({ReviewCommentFields.PULL_REQUEST_ID: '17'})

    def test_review_comment_from_payload_stringifies_non_string_values(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: 17,
                ReviewCommentFields.COMMENT_ID: 99,
                ReviewCommentFields.AUTHOR: None,
                ReviewCommentFields.BODY: 12345,
            }
        )

        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'None')
        self.assertEqual(comment.body, '12345')

    def test_review_comment_from_payload_normalizes_comment_context(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'reviewer',
                ReviewCommentFields.BODY: 'Please rename this variable.',
                ReviewCommentFields.ALL_COMMENTS: [
                    ReviewComment(
                        pull_request_id='17',
                        comment_id='98',
                        author='reviewer',
                        body='Please add a test.',
                    )
                ],
            }
        )

        self.assertEqual(
            getattr(comment, ReviewCommentFields.ALL_COMMENTS),
            [
                {
                    ReviewCommentFields.COMMENT_ID: '98',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please add a test.',
                }
            ],
        )

    def test_review_comment_from_payload_handles_unicode_characters(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'test-user',
                ReviewCommentFields.BODY: 'Test with unicode: café, naïve, résumé, 🚀',
            }
        )

        self.assertEqual(comment.body, 'Test with unicode: café, naïve, résumé, 🚀')

    def test_review_comment_from_payload_handles_empty_body(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'test-user',
                ReviewCommentFields.BODY: '',
            }
        )

        self.assertEqual(comment.body, '')

    def test_review_comment_from_payload_handles_special_characters(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'user\"with\"quotes',
                ReviewCommentFields.BODY: 'Body with \'single\' and "double" quotes',
            }
        )

        self.assertEqual(comment.author, 'user\"with\"quotes')
        self.assertEqual(comment.body, 'Body with \'single\' and "double" quotes')

    def test_normalize_comment_context_edge_cases(self) -> None:
        self.assertEqual(
            normalize_comment_context([
                {'invalid_field': 'should_be_ignored'},
                None,
                'not_a_dict_or_object',
            ]),
            [],
        )
        self.assertEqual(normalize_comment_context(None), [])
