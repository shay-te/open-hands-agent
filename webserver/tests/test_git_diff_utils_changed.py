"""Tests for ``changed_paths`` — the helper that powers the green
"modified on this branch" colouring + ``M`` badge in the Files tree.

It resolves a diff base, then unions two git calls (in this order):
  0. ``git merge-base <base_ref> HEAD``  — the anchor (see ``_diff_base``)
  1. ``git diff --name-only <merge-base>``  — tracked committed/uncommitted
  2. ``git ls-files --others --exclude-standard`` — untracked, non-ignored

We stub ``run_git`` with a side-effect sequence rather than spinning
up a real repo with a base ref per test (slow / brittle on CI). The
FIRST side-effect value is the merge-base SHA.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver import git_diff_utils


class ChangedPathsTests(unittest.TestCase):
    def test_empty_args_short_circuit(self) -> None:
        self.assertEqual(git_diff_utils.changed_paths('', 'origin/main'), [])
        self.assertEqual(git_diff_utils.changed_paths('/repo', ''), [])

    def test_unions_tracked_and_untracked_sorted_deduped(self) -> None:
        tracked = 'src/app.py\nREADME.md\nsrc/app.py\n'   # dup on purpose
        untracked = 'src/new_file.py\nREADME.md\n'        # README also tracked
        with patch.object(
            git_diff_utils, 'run_git', side_effect=['basesha\n', tracked, untracked],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['README.md', 'src/app.py', 'src/new_file.py'],
            )

    def test_only_tracked_when_no_untracked(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git', side_effect=['basesha\n', 'a.py\nb.py\n', ''],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['a.py', 'b.py'],
            )

    def test_only_untracked_when_no_tracked_diff(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git', side_effect=['basesha\n', '', 'fresh.py\n'],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['fresh.py'],
            )

    def test_run_git_failure_degrades_to_empty(self) -> None:
        # run_git → None on every call (git missing / not a repo /
        # bad base ref): merge-base fails (falls back to base_ref), then
        # the diff and ls-files calls fail too. Must degrade quietly.
        with patch.object(
            git_diff_utils, 'run_git', side_effect=[None, None, None],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/bogus'), [],
            )

    def test_strips_whitespace_and_skips_blank_lines(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git',
            side_effect=['basesha\n', '  src/x.py  \n\n', '\n  y.py \n'],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['src/x.py', 'y.py'],
            )


class DiffBaseTests(unittest.TestCase):
    """``_diff_base`` resolves the merge-base, not the destination tip.

    Regression for the operator's report: Kato's Changes tab showed
    thousands of phantom DELETIONS (files master gained AFTER the task
    branch forked) that weren't in the PR. The PR uses three-dot
    ``base...HEAD``; diffing against the merge-base reproduces that.
    """

    def test_resolves_merge_base_of_base_ref_and_head(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git', return_value='f0rkp0int\n',
        ) as mock_rg:
            self.assertEqual(
                git_diff_utils._diff_base('/repo', 'origin/master'),
                'f0rkp0int',
            )
        self.assertEqual(
            mock_rg.call_args.args[1], ['merge-base', 'origin/master', 'HEAD'],
        )

    def test_falls_back_to_base_ref_when_no_common_ancestor(self) -> None:
        # Unrelated histories / unresolvable ref → run_git None → use tip.
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(
                git_diff_utils._diff_base('/repo', 'origin/master'),
                'origin/master',
            )

    def test_changed_paths_diffs_against_merge_base_not_tip(self) -> None:
        # The tracked-diff call must use the merge-base SHA, NOT the
        # passed-in tip ref — otherwise master's post-fork files show
        # up as deletions.
        with patch.object(
            git_diff_utils, 'run_git',
            side_effect=['f0rkp0int\n', 'changed.py\n', ''],
        ) as mock_rg:
            git_diff_utils.changed_paths('/repo', 'origin/master')
        diff_call = mock_rg.call_args_list[1].args[1]
        self.assertEqual(diff_call, ['diff', '--name-only', 'f0rkp0int'])


if __name__ == '__main__':
    unittest.main()
