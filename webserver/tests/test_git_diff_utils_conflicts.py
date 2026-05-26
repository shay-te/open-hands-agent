"""Tests for ``conflicted_paths`` (the helper that powers the
CONFLICTED badge in the Changes tab and the ⚠ icon in the Files
tree).

We exercise the helper at the parser level — feeding fake
``git ls-files --unmerged`` output via a stubbed ``run_git`` —
because spinning up a real merge-conflicted git repo per test is
expensive and brittle on CI.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver import git_diff_utils


class ConflictedPathsTests(unittest.TestCase):
    def test_empty_when_no_unmerged_output(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=''):
            self.assertEqual(git_diff_utils.conflicted_paths('/repo'), [])

    def test_empty_when_run_git_returns_none(self) -> None:
        # ``run_git`` returns None when git isn't on PATH or the cwd
        # isn't a repo. Conflict detection must degrade quietly.
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(git_diff_utils.conflicted_paths('/not-a-repo'), [])

    def test_dedupes_three_stages_per_file(self) -> None:
        # Each conflicted file produces three lines (stage 1/2/3)
        # — one per common ancestor / our / their version. Real git
        # output for a single file looks like this:
        sample = (
            '100644 1111111111111111111111111111111111111111 1\tsrc/auth.py\n'
            '100644 2222222222222222222222222222222222222222 2\tsrc/auth.py\n'
            '100644 3333333333333333333333333333333333333333 3\tsrc/auth.py\n'
        )
        with patch.object(git_diff_utils, 'run_git', return_value=sample):
            self.assertEqual(
                git_diff_utils.conflicted_paths('/repo'),
                ['src/auth.py'],
            )

    def test_multiple_conflicted_files_sorted(self) -> None:
        sample = (
            '100644 aaaa 1\tsrc/z.py\n'
            '100644 bbbb 2\tsrc/z.py\n'
            '100644 cccc 1\tsrc/a.py\n'
            '100644 dddd 2\tsrc/a.py\n'
            '100644 eeee 3\tsrc/m.py\n'
        )
        with patch.object(git_diff_utils, 'run_git', return_value=sample):
            self.assertEqual(
                git_diff_utils.conflicted_paths('/repo'),
                ['src/a.py', 'src/m.py', 'src/z.py'],
            )

    def test_skips_lines_without_tab(self) -> None:
        # Defensive: if git ever changes its output format, we
        # silently skip unparseable lines rather than crash.
        sample = 'garbage line with no tab\n100644 aaaa 2\tvalid.py\n'
        with patch.object(git_diff_utils, 'run_git', return_value=sample):
            self.assertEqual(
                git_diff_utils.conflicted_paths('/repo'),
                ['valid.py'],
            )

    def test_strips_trailing_whitespace_in_path(self) -> None:
        sample = '100644 aaaa 2\t  src/auth.py  \n'
        with patch.object(git_diff_utils, 'run_git', return_value=sample):
            self.assertEqual(
                git_diff_utils.conflicted_paths('/repo'),
                ['src/auth.py'],
            )

    def test_skips_lines_with_blank_path_after_tab(self) -> None:
        # The line has a tab but the segment after it is whitespace —
        # ``path`` strips to '' and must not be added to the result set.
        sample = (
            '100644 aaaa 2\t   \n'
            '100644 bbbb 2\tvalid.py\n'
        )
        with patch.object(git_diff_utils, 'run_git', return_value=sample):
            self.assertEqual(
                git_diff_utils.conflicted_paths('/repo'),
                ['valid.py'],
            )


if __name__ == '__main__':
    unittest.main()
