"""Tests for two prompt-token-saving fixes:

1. ``review_comment_context_text`` drops kato's own previous "Kato
   addressed review comment X" replies from the comment thread.
   They're noise to the agent — kato is narrating to itself — and
   on long-running PRs they accumulate.
2. ``review_comment_code_snippet`` reads ``[line - 3, line + 3]``
   from the workspace file and renders it for the prompt builders.
   Saves a Read tool call per inline review comment.

Both fixes follow the same pattern as the localization work: hand
the agent what kato already knows, instead of making the agent
discover it via tool calls.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.data.fields import ReviewCommentFields
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    review_comment_code_snippet,
    review_comment_context_text,
    review_comments_batch_text,
)


def _make_comment(
    *, comment_id: str = '1', body: str = 'fix this',
    file_path: str = '', line_number: int | str = '',
    line_type: str = '', author: str = 'reviewer',
    all_comments=None,
) -> ReviewComment:
    comment = ReviewComment(
        comment_id=comment_id,
        author=author,
        body=body,
        file_path=file_path,
        line_number=line_number,
        line_type=line_type,
    )
    if all_comments is not None:
        setattr(comment, ReviewCommentFields.ALL_COMMENTS, all_comments)
    return comment


class KatoReplyFilterTests(unittest.TestCase):
    """``review_comment_context_text`` drops kato's narrate-to-self lines."""

    def test_kato_fixed_prefix_is_filtered_out(self) -> None:
        comment = _make_comment(
            body='fix this typo',
            all_comments=[
                {'comment_id': '1', 'author': 'reviewer', 'body': 'fix this typo'},
                {
                    'comment_id': '2', 'author': 'kato',
                    'body': 'Kato addressed review comment 1 on pull request 7.',
                },
                {'comment_id': '3', 'author': 'reviewer', 'body': 'thanks'},
            ],
        )
        text = review_comment_context_text(comment)
        self.assertIn('reviewer: fix this typo', text)
        self.assertIn('reviewer: thanks', text)
        self.assertNotIn('Kato addressed', text)

    def test_kato_reply_prefix_is_filtered_out(self) -> None:
        comment = _make_comment(
            body='another comment',
            all_comments=[
                {'comment_id': '1', 'author': 'reviewer', 'body': 'real'},
                {
                    'comment_id': '2', 'author': 'kato',
                    'body': 'Kato addressed this review comment in commit abc123.',
                },
            ],
        )
        text = review_comment_context_text(comment)
        self.assertNotIn('Kato addressed', text)

    def test_filter_does_not_drop_unrelated_kato_mentions(self) -> None:
        # Reviewer-authored body that happens to mention kato is NOT
        # a kato self-reply — keep it.
        comment = _make_comment(
            body='primary',
            all_comments=[
                {'comment_id': '1', 'author': 'reviewer', 'body': 'primary'},
                {
                    'comment_id': '2', 'author': 'reviewer',
                    'body': 'kato should be careful here',
                },
            ],
        )
        text = review_comment_context_text(comment)
        self.assertIn('kato should be careful here', text)

    def test_returns_empty_when_only_kato_replies(self) -> None:
        # If filtering removes every entry (every comment in the
        # thread is a kato self-reply), the helper returns empty —
        # no orphaned "Review comment context:\n" header.
        comment = _make_comment(
            body='primary',
            all_comments=[
                {
                    'comment_id': '1', 'author': 'kato',
                    'body': 'Kato addressed review comment 5 on pull request 7.',
                },
                {
                    'comment_id': '2', 'author': 'kato',
                    'body': 'Kato addressed this review comment in commit abc123.',
                },
            ],
        )
        text = review_comment_context_text(comment)
        self.assertEqual(text, '')


class CodeSnippetReaderTests(unittest.TestCase):
    """``review_comment_code_snippet`` reads the workspace file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.file_path = self.workspace / 'src' / 'auth.py'
        self.file_path.parent.mkdir(parents=True)
        # Lines 1-10. Comment will land on line 5.
        self.file_path.write_text(
            '\n'.join([f'line {n}' for n in range(1, 11)]) + '\n',
            encoding='utf-8',
        )

    def test_renders_three_lines_above_and_below_target(self) -> None:
        comment = _make_comment(file_path='src/auth.py', line_number=5)
        text = review_comment_code_snippet(comment, str(self.workspace))
        # Includes lines 2-8.
        self.assertIn('line 2', text)
        self.assertIn('line 8', text)
        self.assertIn('line 5', text)
        self.assertNotIn('line 1', text)
        self.assertNotIn('line 9', text)
        # Target line is arrow-marked.
        self.assertIn('→', text)

    def test_clamps_at_top_of_file(self) -> None:
        comment = _make_comment(file_path='src/auth.py', line_number=2)
        text = review_comment_code_snippet(comment, str(self.workspace))
        self.assertIn('line 1', text)
        self.assertIn('line 2', text)

    def test_clamps_at_bottom_of_file(self) -> None:
        comment = _make_comment(file_path='src/auth.py', line_number=10)
        text = review_comment_code_snippet(comment, str(self.workspace))
        self.assertIn('line 10', text)
        self.assertIn('line 7', text)

    def test_returns_empty_when_no_workspace_path(self) -> None:
        comment = _make_comment(file_path='src/auth.py', line_number=5)
        self.assertEqual(review_comment_code_snippet(comment, ''), '')

    def test_returns_empty_when_no_file_path(self) -> None:
        comment = _make_comment(line_number=5)
        self.assertEqual(
            review_comment_code_snippet(comment, str(self.workspace)),
            '',
        )

    def test_returns_empty_when_no_line_number(self) -> None:
        comment = _make_comment(file_path='src/auth.py')
        self.assertEqual(
            review_comment_code_snippet(comment, str(self.workspace)),
            '',
        )

    def test_returns_empty_when_file_missing(self) -> None:
        comment = _make_comment(file_path='nope/missing.py', line_number=5)
        self.assertEqual(
            review_comment_code_snippet(comment, str(self.workspace)),
            '',
        )

    def test_truncates_absurdly_long_line(self) -> None:
        long_path = self.workspace / 'long.txt'
        long_path.write_text('x' * 1000 + '\n', encoding='utf-8')
        comment = _make_comment(file_path='long.txt', line_number=1)
        text = review_comment_code_snippet(comment, str(self.workspace))
        # Line truncation marker is the literal "..." appended.
        self.assertIn('...', text)


class BatchPromptIncludesSnippetTests(unittest.TestCase):
    """The batched prompt builder embeds snippets when workspace given."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        cache_dir = self.workspace / 'src'
        cache_dir.mkdir(parents=True)
        (cache_dir / 'cache.py').write_text(
            'def __init__(self, ttl):\n'
            '    self.ttl = ttl\n'
            '    self.timeout = 3600\n'
            '    self.store = {}\n',
            encoding='utf-8',
        )

    def test_batch_text_includes_snippet_when_workspace_given(self) -> None:
        comment = _make_comment(
            comment_id='1',
            file_path='src/cache.py', line_number=3, line_type='added',
            body='this should be a constant',
        )
        text = review_comments_batch_text([comment], workspace_path=str(self.workspace))
        self.assertIn('Code at line 3', text)
        self.assertIn('self.timeout = 3600', text)

    def test_batch_text_omits_snippet_when_no_workspace(self) -> None:
        comment = _make_comment(
            comment_id='1',
            file_path='src/cache.py', line_number=3,
            body='constant please',
        )
        text = review_comments_batch_text([comment])
        self.assertNotIn('Code at line', text)

    def test_claude_batch_prompt_threads_workspace_through(self) -> None:
        comment = _make_comment(
            comment_id='1',
            file_path='src/cache.py', line_number=3, line_type='added',
            body='this should be a constant',
        )
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            [comment, _make_comment(comment_id='2', body='other')],
            'feature/proj-7',
            workspace_path=str(self.workspace),
        )
        self.assertIn('Code at line 3', prompt)
        self.assertIn('self.timeout = 3600', prompt)


class SingularPromptIncludesSnippetTests(unittest.TestCase):
    """The single-comment prompt also embeds a snippet when wired."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        (self.workspace / 'app.py').write_text(
            '\n'.join([f'line {n}' for n in range(1, 11)]) + '\n',
            encoding='utf-8',
        )

    def test_singular_prompt_with_workspace_inlines_snippet(self) -> None:
        comment = _make_comment(
            file_path='app.py', line_number=5, line_type='added',
            body='fix this',
        )
        prompt = ClaudeCliClient._build_review_prompt(
            comment, 'feature/proj-7', workspace_path=str(self.workspace),
        )
        self.assertIn('Code at line 5', prompt)
        self.assertIn('line 5', prompt)

    def test_singular_prompt_without_workspace_omits_snippet(self) -> None:
        comment = _make_comment(
            file_path='app.py', line_number=5, body='fix this',
        )
        prompt = ClaudeCliClient._build_review_prompt(
            comment, 'feature/proj-7',
        )
        self.assertNotIn('Code at line', prompt)


if __name__ == '__main__':
    unittest.main()
