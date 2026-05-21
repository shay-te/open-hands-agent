from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from provider_client_base.provider_client_base.pull_request_client_base import (
    PullRequestClientBase,
    _coerce_line_number,
)
from provider_client_base.provider_client_base.retrying_client_base import RetryingClientBase
from provider_client_base.provider_client_base.data.fields import PullRequestFields, ReviewCommentFields
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from tests.utils import assert_client_headers_and_timeout, mock_response


# ---------------------------------------------------------------------------
# Minimal stubs (no external core-lib imports)
# ---------------------------------------------------------------------------

class ConcreteRetryingClient(RetryingClientBase):
    provider_name = 'test'


class ConcretePRClient(PullRequestClientBase):
    provider_name = 'test'

    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        pass

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch=None,
        description: str = '',
    ) -> dict:
        return {}

    def list_pull_request_comments(self, repo_owner, repo_slug, pull_request_id):
        return []

    def find_pull_requests(self, repo_owner, repo_slug, *, source_branch='', title_prefix=''):
        return []

    def reply_to_review_comment(self, repo_owner, repo_slug, comment, body):
        pass

    def resolve_review_comment(self, repo_owner, repo_slug, comment):
        pass


# ---------------------------------------------------------------------------
# RetryingClientBase — init
# ---------------------------------------------------------------------------

class RetryingClientBaseInitTests(unittest.TestCase):
    def test_sets_bearer_auth_header(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com', 'my-token', timeout=30)
        assert_client_headers_and_timeout(self, client, 'my-token', 30)

    def test_uses_custom_timeout(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com', 'tok', timeout=60)
        assert_client_headers_and_timeout(self, client, 'tok', 60)

    def test_strips_trailing_slash_from_base_url(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com/', 'tok', timeout=30)
        self.assertFalse(client.base_url.endswith('/'))

    def test_enforces_minimum_max_retries_of_one(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com', 'tok', timeout=30, max_retries=0)
        self.assertEqual(client.max_retries, 1)

    def test_uses_custom_max_retries(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com', 'tok', timeout=30, max_retries=5)
        self.assertEqual(client.max_retries, 5)

    def test_creates_logger_with_class_name(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com', 'tok', timeout=30)
        self.assertEqual(client.logger.name, 'ConcreteRetryingClient')

    def test_default_max_retries_is_three(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com', 'tok', timeout=30)
        self.assertEqual(client.max_retries, 3)


# ---------------------------------------------------------------------------
# RetryingClientBase — retry methods
# ---------------------------------------------------------------------------

class RetryingClientBaseRetryMethodsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ConcreteRetryingClient('https://api.example.com', 'tok', timeout=30)

    def test_get_with_retry_calls_get(self) -> None:
        response = mock_response(json_data={'ok': True})
        with patch.object(self.client, '_get', return_value=response) as mock_get:
            result = self.client._get_with_retry('/path', params={'a': '1'})
        mock_get.assert_called_once_with('/path', params={'a': '1'})
        self.assertIs(result, response)

    def test_post_with_retry_calls_post(self) -> None:
        response = mock_response(json_data={'id': '1'})
        with patch.object(self.client, '_post', return_value=response) as mock_post:
            result = self.client._post_with_retry('/items', json={'name': 'x'})
        mock_post.assert_called_once_with('/items', json={'name': 'x'})
        self.assertIs(result, response)

    def test_put_with_retry_calls_put(self) -> None:
        response = mock_response()
        with patch.object(self.client, '_put', return_value=response) as mock_put:
            result = self.client._put_with_retry('/items/1', json={'name': 'y'})
        mock_put.assert_called_once_with('/items/1', json={'name': 'y'})
        self.assertIs(result, response)

    def test_patch_calls_session_patch_with_correct_url(self) -> None:
        response = mock_response()
        with patch.object(self.client.session, 'patch', return_value=response) as mock_patch:
            result = self.client._patch('/items/1', json={'status': 'done'})
        expected_url = 'https://api.example.com/items/1'
        mock_patch.assert_called_once()
        call_url = mock_patch.call_args[0][0]
        self.assertEqual(call_url, expected_url)
        self.assertIs(result, response)

    def test_patch_with_retry_calls_patch(self) -> None:
        response = mock_response()
        with patch.object(self.client, '_patch', return_value=response) as mock_patch:
            result = self.client._patch_with_retry('/items/2', json={'x': 1})
        mock_patch.assert_called_once_with('/items/2', json={'x': 1})
        self.assertIs(result, response)

    def test_delete_with_retry_calls_delete(self) -> None:
        response = mock_response()
        with patch.object(self.client, '_delete', return_value=response) as mock_delete:
            result = self.client._delete_with_retry('/items/1')
        mock_delete.assert_called_once_with('/items/1')
        self.assertIs(result, response)

    def test_retry_operation_name_format(self) -> None:
        name = self.client._retry_operation_name('GET', '/endpoint')
        self.assertIn('ConcreteRetryingClient', name)
        self.assertIn('GET', name)
        self.assertIn('endpoint', name)

    def test_retry_operation_name_strips_slashes(self) -> None:
        name = self.client._retry_operation_name('POST', '/api/v1/items')
        self.assertTrue(name.startswith('ConcreteRetryingClient POST'))
        self.assertIn('api/v1/items', name)


# ---------------------------------------------------------------------------
# PullRequestClientBase — abstract contract
# ---------------------------------------------------------------------------

class PullRequestClientBaseAbstractTests(unittest.TestCase):
    def test_cannot_instantiate_abstract_base_directly(self) -> None:
        with self.assertRaises(TypeError):
            PullRequestClientBase('https://example.com', 'token', timeout=30)

    def test_concrete_subclass_can_be_instantiated(self) -> None:
        client = ConcretePRClient('https://example.com', 'token', timeout=30)
        self.assertIsInstance(client, PullRequestClientBase)
        self.assertIsInstance(client, RetryingClientBase)


# ---------------------------------------------------------------------------
# PullRequestClientBase — _normalized_pull_request
# ---------------------------------------------------------------------------

class NormalizedPullRequestTests(unittest.TestCase):
    def test_returns_id_title_url(self) -> None:
        result = PullRequestClientBase._normalized_pull_request(
            {'number': 17, PullRequestFields.TITLE: ' PROJ-1: fix it already '},
            id_key='number',
            url=' https://example.com/pr/17 ',
        )
        self.assertEqual(result, {
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: fix it already',
            PullRequestFields.URL: 'https://example.com/pr/17',
        })

    def test_raises_for_missing_id_key(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
            PullRequestClientBase._normalized_pull_request(
                {PullRequestFields.TITLE: 'no id here'},
                id_key='number',
            )

    def test_raises_for_non_dict_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
            PullRequestClientBase._normalized_pull_request(None, id_key='number')

    def test_raises_for_list_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
            PullRequestClientBase._normalized_pull_request([], id_key='number')

    def test_empty_url_defaults_to_empty_string(self) -> None:
        result = PullRequestClientBase._normalized_pull_request({'id': '1'}, id_key='id')
        self.assertEqual(result[PullRequestFields.URL], '')

    def test_strips_whitespace_from_all_values(self) -> None:
        result = PullRequestClientBase._normalized_pull_request(
            {'id': '  42  ', PullRequestFields.TITLE: '  My PR  '},
            id_key='id',
            url='  https://example.com/42  ',
        )
        self.assertEqual(result[PullRequestFields.ID], '42')
        self.assertEqual(result[PullRequestFields.TITLE], 'My PR')
        self.assertEqual(result[PullRequestFields.URL], 'https://example.com/42')

    def test_handles_missing_title_gracefully(self) -> None:
        result = PullRequestClientBase._normalized_pull_request({'id': '5'}, id_key='id')
        self.assertEqual(result[PullRequestFields.TITLE], '')

    def test_id_coerced_to_string(self) -> None:
        result = PullRequestClientBase._normalized_pull_request({'number': 99}, id_key='number')
        self.assertEqual(result[PullRequestFields.ID], '99')
        self.assertIsInstance(result[PullRequestFields.ID], str)


# ---------------------------------------------------------------------------
# PullRequestClientBase — _review_comment_from_values
# ---------------------------------------------------------------------------

class ReviewCommentFromValuesTests(unittest.TestCase):
    def test_returns_review_comment_with_all_fields(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id=' 17 ',
            comment_id=' 99 ',
            author=' reviewer ',
            body=' Please rename. ',
            resolution_target_id=' thread-1 ',
            resolution_target_type='thread',
            file_path=' src/app.py ',
            line_number=42,
            line_type='ADDED',
            commit_sha=' abc123 ',
        )
        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'reviewer')
        self.assertEqual(comment.body, 'Please rename.')
        self.assertEqual(comment.file_path, 'src/app.py')
        self.assertEqual(comment.line_number, 42)
        self.assertEqual(comment.line_type, 'ADDED')
        self.assertEqual(comment.commit_sha, 'abc123')

    def test_sets_resolution_target_id_when_provided(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
            resolution_target_id='thread-1',
        )
        self.assertEqual(getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID), 'thread-1')

    def test_does_not_set_resolution_target_id_when_blank(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
        )
        self.assertFalse(hasattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID))

    def test_sets_resolution_target_type_when_provided(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
            resolution_target_id='thread-1', resolution_target_type='thread',
        )
        self.assertEqual(getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE), 'thread')

    def test_resolvable_defaults_to_bool_of_target_id_when_none(self) -> None:
        comment_with = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
            resolution_target_id='thread-1',
        )
        comment_without = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
        )
        self.assertTrue(getattr(comment_with, ReviewCommentFields.RESOLVABLE))
        self.assertFalse(getattr(comment_without, ReviewCommentFields.RESOLVABLE))

    def test_resolvable_explicit_true(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
            resolvable=True,
        )
        self.assertTrue(getattr(comment, ReviewCommentFields.RESOLVABLE))

    def test_resolvable_explicit_false(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b',
            resolution_target_id='thread-1', resolvable=False,
        )
        self.assertFalse(getattr(comment, ReviewCommentFields.RESOLVABLE))

    def test_line_number_positive_int_preserved(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b', line_number=10,
        )
        self.assertEqual(comment.line_number, 10)

    def test_line_number_zero_coerced_to_empty(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b', line_number=0,
        )
        self.assertEqual(comment.line_number, '')

    def test_line_number_negative_coerced_to_empty(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b', line_number=-5,
        )
        self.assertEqual(comment.line_number, '')

    def test_line_number_none_coerced_to_empty(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b', line_number=None,
        )
        self.assertEqual(comment.line_number, '')

    def test_line_number_string_int_coerced(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b', line_number='42',
        )
        self.assertEqual(comment.line_number, 42)

    def test_line_number_non_numeric_string_coerced_to_empty(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='2', author='a', body='b', line_number='abc',
        )
        self.assertEqual(comment.line_number, '')


# ---------------------------------------------------------------------------
# _coerce_line_number
# ---------------------------------------------------------------------------

class CoerceLineNumberTests(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number(None), '')

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number(''), '')

    def test_zero_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number(0), '')

    def test_negative_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number(-1), '')
        self.assertEqual(_coerce_line_number(-100), '')

    def test_positive_int_returned(self) -> None:
        self.assertEqual(_coerce_line_number(1), 1)
        self.assertEqual(_coerce_line_number(42), 42)
        self.assertEqual(_coerce_line_number(9999), 9999)

    def test_string_int_coerced(self) -> None:
        self.assertEqual(_coerce_line_number('7'), 7)
        self.assertEqual(_coerce_line_number('100'), 100)

    def test_string_zero_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number('0'), '')

    def test_non_numeric_string_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number('abc'), '')
        self.assertEqual(_coerce_line_number('3.7'), '')

    def test_float_truncated_to_int(self) -> None:
        self.assertEqual(_coerce_line_number(3.9), 3)

    def test_float_zero_returns_empty(self) -> None:
        self.assertEqual(_coerce_line_number(0.0), '')


# ---------------------------------------------------------------------------
# ReviewComment data class
# ---------------------------------------------------------------------------

class ReviewCommentTests(unittest.TestCase):
    def test_default_construction(self) -> None:
        comment = ReviewComment()
        self.assertEqual(comment.pull_request_id, '')
        self.assertEqual(comment.comment_id, '')
        self.assertEqual(comment.author, '')
        self.assertEqual(comment.body, '')
        self.assertEqual(comment.file_path, '')
        self.assertEqual(comment.line_number, '')
        self.assertEqual(comment.line_type, '')
        self.assertEqual(comment.commit_sha, '')

    def test_construction_with_all_fields(self) -> None:
        comment = ReviewComment(
            pull_request_id='17', comment_id='99', author='alice',
            body='Fix this.', file_path='src/app.py', line_number=10,
            line_type='ADDED', commit_sha='abc123',
        )
        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'alice')
        self.assertEqual(comment.body, 'Fix this.')
        self.assertEqual(comment.file_path, 'src/app.py')
        self.assertEqual(comment.line_number, 10)
        self.assertEqual(comment.line_type, 'ADDED')
        self.assertEqual(comment.commit_sha, 'abc123')

    def test_repr_contains_all_fields(self) -> None:
        comment = ReviewComment(pull_request_id='17', comment_id='99', author='alice', body='Fix.')
        r = repr(comment)
        self.assertIn('ReviewComment(', r)
        self.assertIn('pull_request_id=', r)
        self.assertIn("'17'", r)
        self.assertIn("'99'", r)
        self.assertIn("'alice'", r)

    def test_equality_same_values(self) -> None:
        a = ReviewComment(pull_request_id='1', comment_id='2', author='x', body='y')
        b = ReviewComment(pull_request_id='1', comment_id='2', author='x', body='y')
        self.assertEqual(a, b)

    def test_inequality_different_body(self) -> None:
        a = ReviewComment(pull_request_id='1', comment_id='2', author='x', body='y')
        b = ReviewComment(pull_request_id='1', comment_id='2', author='x', body='z')
        self.assertNotEqual(a, b)

    def test_inequality_different_type(self) -> None:
        comment = ReviewComment(pull_request_id='1')
        self.assertNotEqual(comment, 'not a comment')
        self.assertNotEqual(comment, None)

    def test_inequality_different_pull_request_id(self) -> None:
        a = ReviewComment(pull_request_id='1')
        b = ReviewComment(pull_request_id='2')
        self.assertNotEqual(a, b)

    def test_inequality_different_line_number(self) -> None:
        a = ReviewComment(line_number=1)
        b = ReviewComment(line_number=2)
        self.assertNotEqual(a, b)

    def test_line_number_accepts_int_or_empty_string(self) -> None:
        c1 = ReviewComment(line_number=5)
        c2 = ReviewComment(line_number='')
        self.assertEqual(c1.line_number, 5)
        self.assertEqual(c2.line_number, '')


# ---------------------------------------------------------------------------
# Fields constants
# ---------------------------------------------------------------------------

class FieldConstantsTests(unittest.TestCase):
    def test_pull_request_fields(self) -> None:
        self.assertEqual(PullRequestFields.ID, 'id')
        self.assertEqual(PullRequestFields.TITLE, 'title')
        self.assertEqual(PullRequestFields.URL, 'url')
        self.assertEqual(PullRequestFields.SOURCE_BRANCH, 'source_branch')
        self.assertEqual(PullRequestFields.DESTINATION_BRANCH, 'destination_branch')
        self.assertEqual(PullRequestFields.DESCRIPTION, 'description')
        self.assertEqual(PullRequestFields.REPOSITORY_ID, 'repository_id')
        self.assertEqual(PullRequestFields.PULL_REQUESTS, 'pull_requests')
        self.assertEqual(PullRequestFields.FAILED_REPOSITORIES, 'failed_repositories')

    def test_review_comment_fields(self) -> None:
        self.assertEqual(ReviewCommentFields.PULL_REQUEST_ID, 'pull_request_id')
        self.assertEqual(ReviewCommentFields.COMMENT_ID, 'comment_id')
        self.assertEqual(ReviewCommentFields.AUTHOR, 'author')
        self.assertEqual(ReviewCommentFields.BODY, 'body')
        self.assertEqual(ReviewCommentFields.ALL_COMMENTS, 'all_comments')
        self.assertEqual(ReviewCommentFields.RESOLUTION_TARGET_ID, 'resolution_target_id')
        self.assertEqual(ReviewCommentFields.RESOLUTION_TARGET_TYPE, 'resolution_target_type')
        self.assertEqual(ReviewCommentFields.RESOLVABLE, 'resolvable')
        self.assertEqual(ReviewCommentFields.FILE_PATH, 'file_path')
        self.assertEqual(ReviewCommentFields.LINE_NUMBER, 'line_number')
        self.assertEqual(ReviewCommentFields.LINE_TYPE, 'line_type')
        self.assertEqual(ReviewCommentFields.COMMIT_SHA, 'commit_sha')


# ---------------------------------------------------------------------------
# Flow tests — A-Z
# ---------------------------------------------------------------------------

class PullRequestClientBaseFlowTests(unittest.TestCase):
    def test_create_then_find_pull_request_flow(self) -> None:
        client = ConcretePRClient('https://api.example.com', 'tok', timeout=30)
        created = PullRequestClientBase._normalized_pull_request(
            {'number': 42, PullRequestFields.TITLE: 'Feature X'},
            id_key='number',
            url='https://api.example.com/pr/42',
        )
        self.assertEqual(created[PullRequestFields.ID], '42')
        self.assertEqual(created[PullRequestFields.TITLE], 'Feature X')

    def test_review_comment_lifecycle_flow(self) -> None:
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='17', comment_id='99',
            author='reviewer', body='Please rename this.',
            resolution_target_id='thread-1', resolution_target_type='thread',
            file_path='src/app.py', line_number=42,
        )
        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.file_path, 'src/app.py')
        self.assertEqual(comment.line_number, 42)
        self.assertTrue(getattr(comment, ReviewCommentFields.RESOLVABLE))

        response = PullRequestClientBase._normalized_pull_request(
            {'id': '17', PullRequestFields.TITLE: 'Fix PR'},
            id_key='id',
        )
        self.assertEqual(response[PullRequestFields.ID], '17')

    def test_retry_client_operation_name_flow(self) -> None:
        client = ConcreteRetryingClient('https://api.example.com/', 'tok', timeout=30)
        name = client._retry_operation_name('GET', '/api/v1/items')
        self.assertIn('ConcreteRetryingClient', name)
        self.assertIn('GET', name)
        self.assertIn('api/v1/items', name)
        self.assertFalse(name.startswith('https://api.example.com//'))

    def test_multiple_review_comments_equality_and_identity(self) -> None:
        c1 = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='a', author='x', body='hello', line_number=5,
        )
        c2 = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='a', author='x', body='hello', line_number=5,
        )
        c3 = PullRequestClientBase._review_comment_from_values(
            pull_request_id='1', comment_id='b', author='x', body='world',
        )
        self.assertEqual(c1, c2)
        self.assertNotEqual(c1, c3)
        self.assertIsInstance(c1, ReviewComment)


if __name__ == '__main__':
    unittest.main()
