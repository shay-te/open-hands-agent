"""Tests for ``webserver/kato_webserver/git_diff_utils.py``.

These functions are thin wrappers around the ``git`` CLI. We stub
``subprocess.run`` (for the ``run_git`` engine) and ``run_git`` itself
(for the higher-level helpers) so the test suite doesn't depend on a
real git binary or a real repo on disk.

Companion file: ``test_git_diff_utils_conflicts.py`` covers
``conflicted_paths``; this file covers everything else.
"""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver import git_diff_utils


class RunGitTests(unittest.TestCase):
    def test_returns_none_when_cwd_empty(self) -> None:
        self.assertIsNone(git_diff_utils.run_git('', ['status'], timeout=5))

    def test_returns_stdout_on_success(self) -> None:
        fake = SimpleNamespace(returncode=0, stdout='hello\n', stderr='')
        with patch.object(subprocess, 'run', return_value=fake) as mock_run:
            out = git_diff_utils.run_git('/repo', ['rev-parse', 'HEAD'], timeout=5)
        self.assertEqual(out, 'hello\n')
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ['git', '-C', '/repo', 'rev-parse', 'HEAD'])
        self.assertEqual(kwargs['timeout'], 5)
        self.assertEqual(kwargs['encoding'], 'utf-8')

    def test_returns_none_on_nonzero_exit(self) -> None:
        # Returning None (not '') is the documented "git failed" signal —
        # callers depend on this to distinguish failure from empty success.
        fake = SimpleNamespace(returncode=1, stdout='', stderr='not a git repo')
        with patch.object(subprocess, 'run', return_value=fake):
            self.assertIsNone(
                git_diff_utils.run_git('/not-a-repo', ['status'], timeout=5),
            )

    def test_returns_none_on_oserror(self) -> None:
        # OSError happens when git isn't on PATH at all.
        with patch.object(subprocess, 'run', side_effect=OSError('no git')):
            self.assertIsNone(
                git_diff_utils.run_git('/repo', ['status'], timeout=5),
            )

    def test_returns_none_on_timeout(self) -> None:
        with patch.object(
            subprocess, 'run',
            side_effect=subprocess.TimeoutExpired('git', 1),
        ):
            self.assertIsNone(
                git_diff_utils.run_git('/repo', ['fetch'], timeout=1),
            )


class CurrentBranchTests(unittest.TestCase):
    def test_returns_stripped_stdout_on_success(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value='feature/x\n'):
            self.assertEqual(git_diff_utils.current_branch('/repo'), 'feature/x')

    def test_returns_empty_when_run_git_fails(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(git_diff_utils.current_branch('/repo'), '')


class LocalBranchExistsTests(unittest.TestCase):
    def test_false_for_empty_branch(self) -> None:
        self.assertFalse(git_diff_utils.local_branch_exists('/repo', ''))

    def test_true_when_rev_parse_succeeds(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value='abc123\n'):
            self.assertTrue(
                git_diff_utils.local_branch_exists('/repo', 'feature/x'),
            )

    def test_false_when_rev_parse_fails(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertFalse(
                git_diff_utils.local_branch_exists('/repo', 'feature/x'),
            )


class RemoteBranchExistsTests(unittest.TestCase):
    def test_false_for_empty_branch(self) -> None:
        self.assertFalse(git_diff_utils.remote_branch_exists('/repo', ''))

    def test_true_when_remote_ref_resolves(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value='deadbeef\n') as mock_rg:
            self.assertTrue(
                git_diff_utils.remote_branch_exists('/repo', 'feature/x'),
            )
        # Verify we probed the remote namespace, not local.
        args = mock_rg.call_args.args[1]
        self.assertIn('refs/remotes/origin/feature/x', args)


class EnsureBranchCheckedOutTests(unittest.TestCase):
    def test_false_for_empty_branch(self) -> None:
        self.assertFalse(git_diff_utils.ensure_branch_checked_out('/repo', ''))

    def test_true_when_already_on_branch(self) -> None:
        # No checkout needed — short-circuits.
        with patch.object(git_diff_utils, 'current_branch', return_value='feature/x'):
            self.assertTrue(
                git_diff_utils.ensure_branch_checked_out('/repo', 'feature/x'),
            )

    def test_checks_out_existing_local_branch(self) -> None:
        with patch.object(git_diff_utils, 'current_branch', side_effect=['master', 'feature/x']), \
             patch.object(git_diff_utils, 'local_branch_exists', return_value=True), \
             patch.object(git_diff_utils, 'run_git', return_value=''):
            self.assertTrue(
                git_diff_utils.ensure_branch_checked_out('/repo', 'feature/x'),
            )

    def test_creates_tracking_branch_from_remote(self) -> None:
        with patch.object(git_diff_utils, 'current_branch', side_effect=['master', 'feature/x']), \
             patch.object(git_diff_utils, 'local_branch_exists', return_value=False), \
             patch.object(git_diff_utils, 'remote_branch_exists', return_value=True), \
             patch.object(git_diff_utils, 'run_git', return_value='') as mock_rg:
            self.assertTrue(
                git_diff_utils.ensure_branch_checked_out('/repo', 'feature/x'),
            )
            # Used checkout -b to create from origin/<branch>.
            args = mock_rg.call_args.args[1]
            self.assertIn('-b', args)
            self.assertIn('origin/feature/x', args)

    def test_returns_false_when_no_ref_anywhere(self) -> None:
        with patch.object(git_diff_utils, 'current_branch', return_value='master'), \
             patch.object(git_diff_utils, 'local_branch_exists', return_value=False), \
             patch.object(git_diff_utils, 'remote_branch_exists', return_value=False):
            self.assertFalse(
                git_diff_utils.ensure_branch_checked_out('/repo', 'feature/x'),
            )

    def test_returns_false_when_local_checkout_fails(self) -> None:
        # Local branch exists, but ``git checkout`` itself returns None
        # (e.g. dirty working tree blocks it).
        with patch.object(git_diff_utils, 'current_branch', return_value='master'), \
             patch.object(git_diff_utils, 'local_branch_exists', return_value=True), \
             patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertFalse(
                git_diff_utils.ensure_branch_checked_out('/repo', 'feature/x'),
            )

    def test_returns_false_when_remote_checkout_fails(self) -> None:
        # No local ref, remote ref exists, but ``git checkout -b`` fails
        # (e.g. remote ref vanished between probe and checkout). Must
        # short-circuit to False rather than falling through to the
        # post-checkout branch comparison.
        with patch.object(git_diff_utils, 'current_branch', return_value='master'), \
             patch.object(git_diff_utils, 'local_branch_exists', return_value=False), \
             patch.object(git_diff_utils, 'remote_branch_exists', return_value=True), \
             patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertFalse(
                git_diff_utils.ensure_branch_checked_out('/repo', 'feature/x'),
            )


class DetectDefaultBranchTests(unittest.TestCase):
    def test_uses_local_origin_head_when_set(self) -> None:
        # symbolic-ref returns ``origin/develop`` → strip the ``origin/`` prefix.
        with patch.object(git_diff_utils, 'run_git', return_value='origin/develop\n'):
            self.assertEqual(git_diff_utils.detect_default_branch('/repo'), 'develop')

    def test_falls_back_to_ls_remote(self) -> None:
        # Local symbolic-ref returns None (no origin/HEAD configured), so we
        # parse the ``ls-remote --symref`` output.
        ls_remote_out = 'ref: refs/heads/develop\tHEAD\nabc123\tHEAD\n'
        with patch.object(
            git_diff_utils, 'run_git', side_effect=[None, ls_remote_out],
        ):
            self.assertEqual(
                git_diff_utils.detect_default_branch('/repo'), 'develop',
            )

    def test_empty_when_both_methods_fail(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(git_diff_utils.detect_default_branch('/repo'), '')

    def test_local_head_without_slash_returns_ref_as_is(self) -> None:
        # Defensive branch: symbolic-ref returns a name without '/' — return verbatim.
        with patch.object(git_diff_utils, 'run_git', return_value='trunk\n'):
            self.assertEqual(git_diff_utils.detect_default_branch('/repo'), 'trunk')

    def test_ls_remote_skips_non_ref_lines(self) -> None:
        # Only the ``ref: ...`` line carries the branch name.
        out = 'abc123\tHEAD\n'
        with patch.object(
            git_diff_utils, 'run_git', side_effect=[None, out],
        ):
            self.assertEqual(git_diff_utils.detect_default_branch('/repo'), '')


class TrackedFileTreeTests(unittest.TestCase):
    def test_empty_when_run_git_fails(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(git_diff_utils.tracked_file_tree('/repo'), [])

    def test_builds_nested_tree_from_paths(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git',
            return_value='src/a.py\nsrc/b.py\nREADME.md\n',
        ):
            tree = git_diff_utils.tracked_file_tree('/repo')
        # Tree should contain both top-level file (README.md) and a src/ folder.
        names = [node['name'] for node in tree]
        self.assertIn('README.md', names)
        self.assertIn('src', names)


class ListBranchCommitsTests(unittest.TestCase):
    def test_empty_when_no_base_ref(self) -> None:
        self.assertEqual(git_diff_utils.list_branch_commits('/repo', ''), [])

    def test_empty_when_no_cwd(self) -> None:
        self.assertEqual(git_diff_utils.list_branch_commits('', 'master'), [])

    def test_parses_commit_log_entries(self) -> None:
        log_out = (
            'aaa111\taaa\t1700000000\tAlice\tfix bug\n'
            'bbb222\tbbb\t1700100000\tBob\tadd feature\n'
        )
        with patch.object(git_diff_utils, 'run_git', return_value=log_out):
            commits = git_diff_utils.list_branch_commits('/repo', 'master')
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]['sha'], 'aaa111')
        self.assertEqual(commits[0]['short_sha'], 'aaa')
        self.assertEqual(commits[0]['author'], 'Alice')
        self.assertEqual(commits[0]['subject'], 'fix bug')
        self.assertEqual(commits[0]['epoch'], 1700000000.0)

    def test_skips_malformed_lines(self) -> None:
        # Lines with fewer than 5 tab-separated parts are dropped silently.
        log_out = 'good\tg\t100\tA\tsubj\nshort_line\n'
        with patch.object(git_diff_utils, 'run_git', return_value=log_out):
            commits = git_diff_utils.list_branch_commits('/repo', 'master')
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]['sha'], 'good')

    def test_falls_back_to_zero_epoch_on_bad_timestamp(self) -> None:
        log_out = 'sha\ts\tnot-a-number\tA\tsubj\n'
        with patch.object(git_diff_utils, 'run_git', return_value=log_out):
            commits = git_diff_utils.list_branch_commits('/repo', 'master')
        self.assertEqual(commits[0]['epoch'], 0.0)

    def test_empty_when_log_returns_nothing(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=''):
            self.assertEqual(
                git_diff_utils.list_branch_commits('/repo', 'master'), [],
            )

    def test_clamps_limit_to_bounded_range(self) -> None:
        # limit < 1 → coerced to 1; > 200 → coerced to 200.
        with patch.object(git_diff_utils, 'run_git', return_value='') as mock_rg:
            git_diff_utils.list_branch_commits('/repo', 'master', limit=0)
            args = mock_rg.call_args.args[1]
            self.assertIn('--max-count=1', args)

        with patch.object(git_diff_utils, 'run_git', return_value='') as mock_rg:
            git_diff_utils.list_branch_commits('/repo', 'master', limit=999)
            args = mock_rg.call_args.args[1]
            self.assertIn('--max-count=200', args)


class DiffForCommitTests(unittest.TestCase):
    def test_empty_when_sha_blank(self) -> None:
        self.assertEqual(git_diff_utils.diff_for_commit('/repo', ''), '')
        self.assertEqual(git_diff_utils.diff_for_commit('/repo', '   '), '')

    def test_empty_when_cwd_blank(self) -> None:
        self.assertEqual(git_diff_utils.diff_for_commit('', 'abc'), '')

    def test_returns_run_git_output(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git',
            return_value='diff --git a/x b/x\n',
        ):
            self.assertEqual(
                git_diff_utils.diff_for_commit('/repo', 'abc123'),
                'diff --git a/x b/x\n',
            )

    def test_empty_string_when_run_git_returns_none(self) -> None:
        # ``run_git`` returns None on failure; ``diff_for_commit`` should
        # coerce to '' so callers can concatenate without an "or" check.
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(git_diff_utils.diff_for_commit('/repo', 'abc'), '')


class BlobSizeAtRefTests(unittest.TestCase):
    def test_none_when_inputs_blank(self) -> None:
        # Any of cwd/ref/path being empty short-circuits to None so the
        # caller (file editor panel) renders an "unknown size" placeholder
        # instead of erroring.
        self.assertIsNone(git_diff_utils.blob_size_at_ref('', 'HEAD', 'a.py'))
        self.assertIsNone(git_diff_utils.blob_size_at_ref('/repo', '', 'a.py'))
        self.assertIsNone(git_diff_utils.blob_size_at_ref('/repo', 'HEAD', ''))

    def test_none_when_run_git_fails(self) -> None:
        # ``git cat-file`` returns None when the blob doesn't exist at
        # the ref (renamed / deleted path). Must propagate as None.
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertIsNone(
                git_diff_utils.blob_size_at_ref('/repo', 'HEAD', 'a.py'),
            )

    def test_returns_parsed_size(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value='1234\n'):
            self.assertEqual(
                git_diff_utils.blob_size_at_ref('/repo', 'HEAD', 'a.py'),
                1234,
            )

    def test_none_when_output_not_integer(self) -> None:
        # Defensive: if git's output isn't a plain integer (corrupted
        # output, future format change), fall back to None instead of
        # raising ValueError up the request stack.
        with patch.object(git_diff_utils, 'run_git', return_value='not-a-number\n'):
            self.assertIsNone(
                git_diff_utils.blob_size_at_ref('/repo', 'HEAD', 'a.py'),
            )


class FileTextAtRefTests(unittest.TestCase):
    def test_none_when_inputs_blank(self) -> None:
        # Same short-circuit as ``blob_size_at_ref`` — empty cwd/ref/path
        # never reaches the git subprocess.
        self.assertIsNone(git_diff_utils.file_text_at_ref('', 'HEAD', 'a.py'))
        self.assertIsNone(git_diff_utils.file_text_at_ref('/repo', '', 'a.py'))
        self.assertIsNone(git_diff_utils.file_text_at_ref('/repo', 'HEAD', ''))

    def test_returns_run_git_output(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value='file body\n'):
            self.assertEqual(
                git_diff_utils.file_text_at_ref('/repo', 'HEAD', 'a.py'),
                'file body\n',
            )

    def test_strips_leading_slash_from_path(self) -> None:
        # Repo-relative paths arrive from the UI sometimes with a leading
        # slash; the safe-path normalization must strip it before composing
        # the ``<ref>:<path>`` argument.
        with patch.object(git_diff_utils, 'run_git', return_value='ok') as mock_rg:
            git_diff_utils.file_text_at_ref('/repo', 'HEAD', '/src/a.py')
        args = mock_rg.call_args.args[1]
        self.assertIn('HEAD:src/a.py', args)


class ElideOversizedFileDiffsTests(unittest.TestCase):
    def _section(self, path: str, n: int) -> str:
        body = '\n'.join(f'+line {i}' for i in range(n))
        return (
            f'diff --git a/{path} b/{path}\n'
            f'index 1111111..2222222 100644\n'
            f'--- a/{path}\n'
            f'+++ b/{path}\n'
            f'@@ -0,0 +1,{n} @@\n'
            f'{body}\n'
        )

    def test_empty_input_passes_through(self) -> None:
        self.assertEqual(git_diff_utils._elide_oversized_file_diffs(''), '')

    def test_small_diff_is_unchanged(self) -> None:
        small = self._section('src/app.py', 5)
        self.assertEqual(
            git_diff_utils._elide_oversized_file_diffs(small), small,
        )

    def test_oversized_section_body_replaced_with_notice(self) -> None:
        huge = self._section(
            'build/static/js/main.adc4c4f0.js',
            git_diff_utils.TRACKED_FILE_DIFF_LINE_LIMIT + 50,
        )
        out = git_diff_utils._elide_oversized_file_diffs(huge)
        # Header (path + kind) preserved so react-diff-view still
        # resolves the file; body collapsed to one context line.
        self.assertIn('diff --git a/build/static/js/main.adc4c4f0.js', out)
        self.assertIn('--- a/build/static/js/main.adc4c4f0.js', out)
        self.assertIn('+++ b/build/static/js/main.adc4c4f0.js', out)
        self.assertIn('diff too large to display', out)
        self.assertIn('@@ -1 +1 @@', out)
        self.assertLess(len(out), len(huge) // 10)
        self.assertNotIn('+line 100', out)

    def test_minified_few_lines_but_huge_bytes_is_elided(self) -> None:
        # A minified bundle is a HANDFUL of lines, each enormous — the
        # byte cap (not the line cap) must catch this.
        giant_line = '+' + ('x' * (git_diff_utils.TRACKED_FILE_DIFF_BYTE_LIMIT + 10))
        section = (
            'diff --git a/build/main.abc123.js b/build/main.abc123.js\n'
            'index 1..2 100644\n'
            '--- a/build/main.abc123.js\n'
            '+++ b/build/main.abc123.js\n'
            '@@ -1 +1 @@\n'
            f'{giant_line}\n'
        )
        out = git_diff_utils._elide_oversized_file_diffs(section)
        self.assertIn('diff too large to display', out)
        self.assertNotIn('xxxx', out)               # giant body gone
        self.assertLess(len(out), 1024)
        self.assertIn('diff --git a/build/main.abc123.js', out)

    def test_only_the_oversized_section_is_elided(self) -> None:
        small = self._section('src/keep.py', 3)
        huge = self._section(
            'build/bundle.js',
            git_diff_utils.TRACKED_FILE_DIFF_LINE_LIMIT + 10,
        )
        out = git_diff_utils._elide_oversized_file_diffs(small + huge)
        self.assertIn('+line 2', out)            # src/keep.py survives
        self.assertIn('diff too large to display', out)
        self.assertIn('diff --git a/src/keep.py', out)
        self.assertIn('diff --git a/build/bundle.js', out)

    def test_oversized_section_without_hunk_is_passed_through(self) -> None:
        # Binary-stub or rename-only sections have NO ``@@`` line. Even
        # when they breach the byte cap (huge ``index`` line, GIT-LFS
        # pointer noise), we leave them as-is — there's no body to elide,
        # and synthesizing a hunk would corrupt react-diff-view's parse.
        huge_index = 'x' * (git_diff_utils.TRACKED_FILE_DIFF_BYTE_LIMIT + 10)
        section = (
            'diff --git a/big.bin b/big.bin\n'
            f'index {huge_index}..1234567 100644\n'
            'Binary files a/big.bin and b/big.bin differ\n'
        )
        out = git_diff_utils._elide_oversized_file_diffs(section)
        # Untouched — no notice injected, original bytes preserved.
        self.assertEqual(out, section)
        self.assertNotIn('diff too large to display', out)

    def test_diff_against_base_elides_then_appends_untracked(self) -> None:
        huge = self._section(
            'build/x.js', git_diff_utils.TRACKED_FILE_DIFF_LINE_LIMIT + 5,
        )
        with patch.object(git_diff_utils, 'run_git', return_value=huge), \
             patch.object(
                 git_diff_utils, '_untracked_files_as_diff',
                 return_value='diff --git a/u b/u\n',
             ):
            out = git_diff_utils.diff_against_base('/repo', 'origin/main')
        self.assertIn('diff too large to display', out)
        self.assertNotIn('+line 50', out)
        self.assertTrue(out.endswith('diff --git a/u b/u\n'))


class UntrackedFilesAsDiffTests(unittest.TestCase):
    def test_empty_when_run_git_returns_none(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=None):
            self.assertEqual(git_diff_utils._untracked_files_as_diff('/repo'), '')

    def test_empty_when_run_git_returns_blank(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=''):
            self.assertEqual(git_diff_utils._untracked_files_as_diff('/repo'), '')

    def test_skips_blank_lines(self) -> None:
        # ``git ls-files`` output with a stray whitespace-only line must
        # not produce a synthesized hunk for it (path strips to '').
        with patch.object(git_diff_utils, 'run_git', return_value='\n   \n'), \
             patch.object(
                 git_diff_utils, '_synthesize_new_file_hunk',
                 return_value='SHOULD_NOT_APPEAR',
             ):
            self.assertEqual(git_diff_utils._untracked_files_as_diff('/repo'), '')


if __name__ == '__main__':
    unittest.main()
