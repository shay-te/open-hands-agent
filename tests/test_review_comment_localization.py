"""Tests for inline review-comment localization (file path + line number).

Pin down four surfaces at once:

1. ``ReviewComment`` carries the new fields and round-trips through
   equality / repr.
2. Each platform's normalizer (Bitbucket / GitHub / GitLab) reads the
   inline metadata from the API response and populates the fields.
3. ``review_comment_from_payload`` (the YouTrack-comment replay path)
   carries the fields through.
4. ``review_comment_location_text`` renders them into the agent
   prompt in the expected shape.

The agent's incremental token cost has been "scan all files looking
for what 'fix this typo' refers to" — this whole feature exists to
hand the agent the file path + line number from the platform
response so the first turn opens the right file directly.
"""

from __future__ import annotations

import unittest

from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_client import (
    BitbucketClient,
)
from github_core_lib.github_core_lib.client.github_client import GitHubClient
from gitlab_core_lib.gitlab_core_lib.client.gitlab_client import GitLabClient
from kato_core_lib.data_layers.data.fields import ReviewCommentFields
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import review_comment_location_text
from kato_core_lib.helpers.review_comment_utils import review_comment_from_payload


class ReviewCommentDataModelTests(unittest.TestCase):
    """``ReviewComment`` equality / repr include the new fields."""

    def test_default_constructor_leaves_inline_fields_empty(self) -> None:
        comment = ReviewComment()
        self.assertEqual(comment.file_path, '')
        self.assertEqual(comment.line_number, '')
        self.assertEqual(comment.line_type, '')
        self.assertEqual(comment.commit_sha, '')

    def test_equality_includes_inline_fields(self) -> None:
        a = ReviewComment(
            comment_id='1', body='x',
            file_path='src/auth.py', line_number=42, line_type='added',
        )
        b = ReviewComment(
            comment_id='1', body='x',
            file_path='src/auth.py', line_number=42, line_type='added',
        )
        c = ReviewComment(
            comment_id='1', body='x',
            file_path='src/auth.py', line_number=43,  # different line
            line_type='added',
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_repr_lists_inline_fields(self) -> None:
        comment = ReviewComment(
            comment_id='1', file_path='a.py', line_number=7,
            line_type='added', commit_sha='deadbeef',
        )
        text = repr(comment)
        self.assertIn("file_path='a.py'", text)
        self.assertIn('line_number=7', text)
        self.assertIn("line_type='added'", text)
        self.assertIn("commit_sha='deadbeef'", text)


class BitbucketParserTests(unittest.TestCase):
    """``BitbucketClient._normalize_comments`` reads ``inline.path`` + ``inline.to``."""

    def test_inline_comment_captures_path_and_new_line(self) -> None:
        payload = {'values': [{
            'id': 100,
            'content': {'raw': 'fix this typo'},
            'user': {'display_name': 'Reviewer'},
            'inline': {'path': 'src/auth.py', 'to': 42, 'from': None},
            'commit': {'hash': 'abc123'},
        }]}
        comments = BitbucketClient._normalize_comments(payload, '7')
        self.assertEqual(len(comments), 1)
        c = comments[0]
        self.assertEqual(c.file_path, 'src/auth.py')
        self.assertEqual(c.line_number, 42)
        self.assertEqual(c.line_type, 'added')
        self.assertEqual(c.commit_sha, 'abc123')

    def test_inline_comment_on_removed_line_uses_from(self) -> None:
        payload = {'values': [{
            'id': 101,
            'content': {'raw': 'why was this removed?'},
            'user': {'display_name': 'Reviewer'},
            'inline': {'path': 'src/auth.py', 'to': None, 'from': 7},
        }]}
        c = BitbucketClient._normalize_comments(payload, '7')[0]
        self.assertEqual(c.line_number, 7)
        self.assertEqual(c.line_type, 'removed')

    def test_pr_level_comment_has_no_localization(self) -> None:
        # Comments not tied to a line have no ``inline`` block —
        # parser leaves the localization fields empty.
        payload = {'values': [{
            'id': 102,
            'content': {'raw': 'general thoughts'},
            'user': {'display_name': 'Reviewer'},
        }]}
        c = BitbucketClient._normalize_comments(payload, '7')[0]
        self.assertEqual(c.file_path, '')
        self.assertEqual(c.line_number, '')


class GitHubParserTests(unittest.TestCase):
    """GraphQL thread → ``ReviewComment`` carries path + line + commit."""

    def _thread(self, **overrides) -> dict:
        thread = {
            'id': 'thread-1',
            'isResolved': False,
            'path': 'src/auth.py',
            'line': 42,
            'originalLine': None,
            'comments': {
                'nodes': [{
                    'databaseId': 100,
                    'body': 'fix this typo',
                    'author': {'login': 'reviewer'},
                    'commit': {'oid': 'deadbeef'},
                }],
            },
        }
        thread.update(overrides)
        return thread

    def test_thread_with_path_and_line(self) -> None:
        c = GitHubClient._normalize_comments([self._thread()], '7')[0]
        self.assertEqual(c.file_path, 'src/auth.py')
        self.assertEqual(c.line_number, 42)
        self.assertEqual(c.line_type, 'added')
        self.assertEqual(c.commit_sha, 'deadbeef')

    def test_thread_with_only_original_line(self) -> None:
        # Outdated thread: ``line`` is null, fall back to originalLine.
        thread = self._thread(line=None, originalLine=15)
        c = GitHubClient._normalize_comments([thread], '7')[0]
        self.assertEqual(c.line_number, 15)
        self.assertEqual(c.line_type, 'removed')

    def test_pr_level_thread_has_no_path(self) -> None:
        thread = self._thread(path='', line=None, originalLine=None)
        c = GitHubClient._normalize_comments([thread], '7')[0]
        self.assertEqual(c.file_path, '')
        self.assertEqual(c.line_number, '')


class GitLabParserTests(unittest.TestCase):
    """GitLab discussion ``position`` → ``ReviewComment`` localization."""

    def test_inline_note_with_new_line(self) -> None:
        payload = [{
            'id': 'disc-1',
            'resolved': False,
            'notes': [{
                'id': 100,
                'body': 'fix this typo',
                'author': {'username': 'reviewer'},
                'position': {
                    'new_path': 'src/auth.py',
                    'new_line': 42,
                    'old_line': None,
                    'head_sha': 'deadbeef',
                },
            }],
        }]
        c = GitLabClient._normalize_comments(payload, '7')[0]
        self.assertEqual(c.file_path, 'src/auth.py')
        self.assertEqual(c.line_number, 42)
        self.assertEqual(c.line_type, 'added')
        self.assertEqual(c.commit_sha, 'deadbeef')

    def test_inline_note_with_only_old_line(self) -> None:
        payload = [{
            'id': 'disc-2',
            'resolved': False,
            'notes': [{
                'id': 101,
                'body': 'why removed?',
                'author': {'username': 'reviewer'},
                'position': {
                    'new_path': '',
                    'old_path': 'src/old.py',
                    'old_line': 7,
                    'new_line': None,
                },
            }],
        }]
        c = GitLabClient._normalize_comments(payload, '7')[0]
        self.assertEqual(c.file_path, 'src/old.py')
        self.assertEqual(c.line_number, 7)
        self.assertEqual(c.line_type, 'removed')

    def test_note_without_position_has_no_localization(self) -> None:
        payload = [{
            'id': 'disc-3',
            'resolved': False,
            'notes': [{
                'id': 102,
                'body': 'top-level thought',
                'author': {'username': 'reviewer'},
            }],
        }]
        c = GitLabClient._normalize_comments(payload, '7')[0]
        self.assertEqual(c.file_path, '')


class PayloadReplayTests(unittest.TestCase):
    """``review_comment_from_payload`` carries inline fields through."""

    def test_payload_with_inline_fields_round_trips(self) -> None:
        comment = review_comment_from_payload({
            ReviewCommentFields.PULL_REQUEST_ID: '7',
            ReviewCommentFields.COMMENT_ID: '100',
            ReviewCommentFields.AUTHOR: 'reviewer',
            ReviewCommentFields.BODY: 'fix this typo',
            ReviewCommentFields.FILE_PATH: 'src/auth.py',
            ReviewCommentFields.LINE_NUMBER: 42,
            ReviewCommentFields.LINE_TYPE: 'added',
            ReviewCommentFields.COMMIT_SHA: 'deadbeef',
        })
        self.assertEqual(comment.file_path, 'src/auth.py')
        self.assertEqual(comment.line_number, 42)
        self.assertEqual(comment.line_type, 'added')
        self.assertEqual(comment.commit_sha, 'deadbeef')

    def test_payload_without_inline_fields_keeps_empty_defaults(self) -> None:
        comment = review_comment_from_payload({
            ReviewCommentFields.PULL_REQUEST_ID: '7',
            ReviewCommentFields.COMMENT_ID: '100',
            ReviewCommentFields.AUTHOR: 'reviewer',
            ReviewCommentFields.BODY: 'general',
        })
        self.assertEqual(comment.file_path, '')
        self.assertEqual(comment.line_number, '')

    def test_payload_with_string_line_number_is_coerced(self) -> None:
        comment = review_comment_from_payload({
            ReviewCommentFields.PULL_REQUEST_ID: '7',
            ReviewCommentFields.COMMENT_ID: '100',
            ReviewCommentFields.AUTHOR: 'reviewer',
            ReviewCommentFields.BODY: 'fix',
            ReviewCommentFields.LINE_NUMBER: '42',
        })
        self.assertEqual(comment.line_number, 42)

    def test_payload_with_zero_line_number_collapses_to_empty(self) -> None:
        # Some platforms send 0 for "no line"; treat as absent so the
        # prompt builder doesn't render "File: foo.py:0".
        comment = review_comment_from_payload({
            ReviewCommentFields.PULL_REQUEST_ID: '7',
            ReviewCommentFields.COMMENT_ID: '100',
            ReviewCommentFields.AUTHOR: 'reviewer',
            ReviewCommentFields.BODY: 'fix',
            ReviewCommentFields.FILE_PATH: 'a.py',
            ReviewCommentFields.LINE_NUMBER: 0,
        })
        self.assertEqual(comment.line_number, '')


class PromptRenderingTests(unittest.TestCase):
    """``review_comment_location_text`` renders the prompt hint."""

    def test_full_inline_metadata_renders_path_line_type_and_commit(self) -> None:
        comment = ReviewComment(
            file_path='src/auth.py', line_number=42,
            line_type='added', commit_sha='deadbeef',
        )
        text = review_comment_location_text(comment)
        self.assertIn('File: src/auth.py:42 (added)', text)
        self.assertIn('Commit: deadbeef', text)

    def test_path_without_line_renders_just_path(self) -> None:
        comment = ReviewComment(file_path='src/auth.py')
        text = review_comment_location_text(comment)
        self.assertEqual(text, 'File: src/auth.py')

    def test_path_with_line_renders_path_colon_line(self) -> None:
        comment = ReviewComment(file_path='src/auth.py', line_number=42)
        text = review_comment_location_text(comment)
        self.assertEqual(text, 'File: src/auth.py:42')

    def test_no_path_renders_empty(self) -> None:
        # No file → no localization to share. Prompt builder skips
        # the line entirely so output stays clean.
        comment = ReviewComment(line_number=42, commit_sha='deadbeef')
        self.assertEqual(review_comment_location_text(comment), '')

    def test_zero_line_number_is_omitted(self) -> None:
        comment = ReviewComment(file_path='a.py', line_number=0)
        text = review_comment_location_text(comment)
        self.assertEqual(text, 'File: a.py')


if __name__ == '__main__':
    unittest.main()
