"""Targeted tests for ``openhands_core_lib/helpers/agent_prompt_utils.py``.

Focuses on the previously-uncovered paths:
  - ``ignored_repository_folder_names`` non-string input
  - ``workspace_scope_block`` skipping empty entries
  - ``review_comment_code_snippet`` — the file-reading branch
  - ``review_comments_batch_text`` localized header + snippet branches
  - ``review_comment_context_text`` non-dict item skipping
  - ``review_comment_location_text`` line_type + commit_sha branches
"""

from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from openhands_core_lib.openhands_core_lib.helpers.agent_prompt_utils import (
    ignored_repository_folder_names,
    review_comment_code_snippet,
    review_comment_context_text,
    review_comment_location_text,
    review_comments_batch_text,
    workspace_scope_block,
)


class IgnoredRepositoryFolderNamesTests(unittest.TestCase):
    def test_accepts_list_input(self) -> None:
        # Hits the ``else: candidates = list(value or [])`` branch.
        result = ignored_repository_folder_names(['foo', 'bar', 'foo'])
        self.assertEqual(result, ['foo', 'bar'])

    def test_none_input_falls_back_to_env(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {'AGENT_IGNORED_REPOSITORY_FOLDERS': 'a,b'},
        ):
            self.assertEqual(ignored_repository_folder_names(None), ['a', 'b'])


class WorkspaceScopeBlockTests(unittest.TestCase):
    def test_skips_empty_entries(self) -> None:
        # Hits the ``if not raw: continue`` branch on line 71.
        out = workspace_scope_block(['', None, '/repo', '.'])
        self.assertIn('/repo', out)


class ReviewCommentCodeSnippetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _comment(self, **kwargs):
        defaults = {'file_path': 'src/app.py', 'line_number': 5}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_returns_empty_when_file_path_missing(self) -> None:
        snippet = review_comment_code_snippet(
            self._comment(file_path=''), str(self.workspace),
        )
        self.assertEqual(snippet, '')

    def test_returns_empty_when_workspace_missing(self) -> None:
        self.assertEqual(
            review_comment_code_snippet(self._comment(), ''),
            '',
        )

    def test_returns_empty_when_line_number_invalid(self) -> None:
        self.assertEqual(
            review_comment_code_snippet(
                self._comment(line_number='not-a-number'),
                str(self.workspace),
            ),
            '',
        )

    def test_returns_empty_when_line_number_non_positive(self) -> None:
        self.assertEqual(
            review_comment_code_snippet(
                self._comment(line_number=0), str(self.workspace),
            ),
            '',
        )

    def test_returns_empty_when_file_missing(self) -> None:
        # ``OSError`` branch on line 213-214.
        self.assertEqual(
            review_comment_code_snippet(
                self._comment(file_path='missing.py'),
                str(self.workspace),
            ),
            '',
        )

    def test_renders_snippet_with_arrow_marker(self) -> None:
        target_file = self.workspace / 'src' / 'app.py'
        target_file.parent.mkdir(parents=True)
        target_file.write_text('\n'.join(f'line {n}' for n in range(1, 11)))

        snippet = review_comment_code_snippet(
            self._comment(line_number=5),
            str(self.workspace),
        )
        self.assertIn('Code at line 5', snippet)
        self.assertIn('→ 5 | line 5', snippet)
        # Context lines are present
        self.assertIn('line 4', snippet)
        self.assertIn('line 6', snippet)

    def test_truncates_very_long_lines(self) -> None:
        target_file = self.workspace / 'long.py'
        target_file.write_text('x' * 500)

        snippet = review_comment_code_snippet(
            self._comment(file_path='long.py', line_number=1),
            str(self.workspace),
        )
        # Line gets truncated to 237 chars + '...'
        self.assertIn('...', snippet)

    def test_returns_empty_when_file_is_blank(self) -> None:
        target_file = self.workspace / 'blank.py'
        target_file.write_text('')

        self.assertEqual(
            review_comment_code_snippet(
                self._comment(file_path='blank.py', line_number=1),
                str(self.workspace),
            ),
            '',
        )


class ReviewCommentsBatchTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_empty_batch(self) -> None:
        self.assertEqual(review_comments_batch_text([]), '')

    def test_localized_header_with_workspace_snippet(self) -> None:
        # Hits lines 249-250 (localized indent) AND 254-259 (snippet inclusion).
        target_file = self.workspace / 'src' / 'a.py'
        target_file.parent.mkdir(parents=True)
        target_file.write_text('\n'.join(f'line {n}' for n in range(1, 6)))

        comment = SimpleNamespace(
            author='reviewer',
            body='this is wrong',
            file_path='src/a.py',
            line_number=3,
            line_type='ADDED',
            commit_sha='abc123',
        )
        out = review_comments_batch_text([comment], str(self.workspace))
        self.assertIn('src/a.py', out)
        self.assertIn('Comment by reviewer: this is wrong', out)
        # Snippet was included
        self.assertIn('→ 3 | line 3', out)

    def test_pr_level_comment_without_location(self) -> None:
        # No file_path/line_number → "PR-level comment" branch (line 252).
        comment = SimpleNamespace(
            author='reviewer', body='general feedback',
            file_path='', line_number='',
            line_type='', commit_sha='',
        )
        out = review_comments_batch_text([comment])
        self.assertIn('PR-level comment', out)


class ReviewCommentContextTextTests(unittest.TestCase):
    def test_skips_non_dict_entries(self) -> None:
        # Hits line 272 (``if not isinstance(item, dict): continue``).
        comment = SimpleNamespace(
            all_comments=[
                'not a dict',  # skipped
                {'author': 'alice', 'body': 'hi'},
                42,  # skipped
            ],
        )
        out = review_comment_context_text(comment)
        self.assertIn('alice', out)
        self.assertIn('hi', out)

    def test_returns_empty_when_no_valid_entries(self) -> None:
        # Hits line 280 (``if not lines: return ''``).
        comment = SimpleNamespace(all_comments=['junk', 42, None])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_no_all_comments_attribute(self) -> None:
        self.assertEqual(
            review_comment_context_text(SimpleNamespace()), '',
        )

    def test_self_reply_prefixes_drop_the_bots_own_replies(self) -> None:
        # Caller-provided prefixes drop the host bot's own replies (parity with
        # claude/codex); the default ('') keeps them (agnostic — no hardcoded
        # bot name).
        comment = SimpleNamespace(all_comments=[
            {'author': 'alice', 'body': 'please rename this'},
            {'author': 'kato', 'body': 'Kato addressed review comment 5'},
        ])
        prefixes = ('Kato addressed review comment ', 'Kato addressed this review comment')
        filtered = review_comment_context_text(comment, prefixes)
        self.assertIn('please rename this', filtered)
        self.assertNotIn('Kato addressed', filtered)
        # Default = no filter: the bot reply stays.
        self.assertIn('Kato addressed', review_comment_context_text(comment))


class ReviewCommentLocationTextTests(unittest.TestCase):
    def test_appends_line_type_and_commit_sha(self) -> None:
        # Hits lines 296-297 (line_type) and 299, 301 (commit_sha).
        comment = SimpleNamespace(
            file_path='src/a.py', line_number=10,
            line_type='REMOVED', commit_sha='deadbeef',
        )
        out = review_comment_location_text(comment)
        self.assertIn('src/a.py:10', out)
        self.assertIn('REMOVED', out)
        self.assertIn('deadbeef', out)

    def test_invalid_line_number_is_dropped(self) -> None:
        # Hits the except branch (line 296-297).
        comment = SimpleNamespace(
            file_path='src/a.py', line_number='oops',
            line_type='', commit_sha='',
        )
        out = review_comment_location_text(comment)
        self.assertIn('src/a.py', out)
        # No "src/a.py:<n>" — line number was dropped silently.
        self.assertNotIn('src/a.py:', out)


class ReviewCommentCodeSnippetBudgetTests(unittest.TestCase):
    """Lines 231-232 & 235 in agent_prompt_utils.py — snippet budget paths."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_snippet_truncates_when_budget_exceeded(self) -> None:
        # Lines 231-232: ``total_bytes > _REVIEW_SNIPPET_MAX_BYTES`` → append
        # truncation marker and break the render loop.
        long_lines = '\n'.join('x' * 200 for _ in range(200))
        (self.workspace / 'big.py').write_text(long_lines)
        comment = SimpleNamespace(file_path='big.py', line_number=100)
        snippet = review_comment_code_snippet(
            comment, str(self.workspace), context_lines=200,
        )
        self.assertIn('snippet truncated', snippet)

    def test_snippet_returns_empty_when_window_past_file_end(self) -> None:
        # Line 235: ``if not rendered: return ''`` — context window lands
        # entirely past the file's end → no rendered lines.
        (self.workspace / 'tiny.py').write_text('one\ntwo\nthree\n')
        comment = SimpleNamespace(file_path='tiny.py', line_number=100)
        snippet = review_comment_code_snippet(
            comment, str(self.workspace), context_lines=1,
        )
        self.assertEqual(snippet, '')


if __name__ == '__main__':
    import unittest.mock  # noqa: F401 used by IgnoredRepositoryFolderNamesTests
    unittest.main()
