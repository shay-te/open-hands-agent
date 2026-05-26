import unittest

from kato_core_lib.data_layers.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.helpers.review_comment_utils import (
    is_mention_comment,
    normalize_comment_context,
    review_comment_from_payload,
    review_comment_processing_keys,
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

    def test_is_mention_comment_detects_at_mention(self) -> None:
        self.assertTrue(is_mention_comment(ReviewComment('1', '1', 'reviewer', '@john can you look at this?')))
        self.assertTrue(is_mention_comment(ReviewComment('1', '1', 'reviewer', 'I think @shay.te should handle this')))
        self.assertTrue(is_mention_comment(ReviewComment('1', '1', 'reviewer', 'Fix this @alice')))

    def test_is_mention_comment_ignores_email_addresses(self) -> None:
        self.assertFalse(is_mention_comment(ReviewComment('1', '1', 'reviewer', 'contact shay.te@gmail.com for details')))
        self.assertFalse(is_mention_comment(ReviewComment('1', '1', 'reviewer', 'user@host is not a mention')))

    def test_is_mention_comment_returns_false_for_plain_comment(self) -> None:
        self.assertFalse(is_mention_comment(ReviewComment('1', '1', 'reviewer', 'Please rename this variable.')))
        self.assertFalse(is_mention_comment(ReviewComment('1', '1', 'reviewer', 'Extract this to a helper.')))
        self.assertFalse(is_mention_comment(ReviewComment('1', '1', 'reviewer', '')))

    def test_is_mention_comment_returns_false_for_none_body(self) -> None:
        self.assertFalse(is_mention_comment(ReviewComment('1', '1', 'reviewer', None)))

    def test_review_comment_processing_keys_includes_resolution_target(self) -> None:
        # Happy path: ``resolution_target_id`` resolves (via fallback
        # to comment_id) and the composed ``type:id`` key is added.
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='42',
            author='reviewer',
            body='please rename',
        )
        keys = review_comment_processing_keys(comment)
        self.assertIn('42', keys)
        self.assertIn('comment:42', keys)

    def test_review_comment_processing_keys_omits_composed_key_when_id_blank(self) -> None:
        # Line 163: ``if resolution_target_id:`` False — both the
        # explicit resolution target and the comment_id fallback are
        # empty, so we must not add a useless ``type:`` (no id) key.
        # The trailing comprehension drops the empty primary id too,
        # leaving an empty set.
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='',
            author='reviewer',
            body='please rename',
        )
        self.assertEqual(review_comment_processing_keys(comment), set())
