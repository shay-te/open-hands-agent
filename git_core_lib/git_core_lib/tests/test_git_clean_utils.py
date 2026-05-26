"""Coverage for ``git_core_lib/helpers/git_clean_utils.py``."""

from __future__ import annotations

import unittest

from git_core_lib.git_core_lib.helpers.git_clean_utils import (
    generated_artifact_paths_from_status,
    status_contains_only_removable_artifacts,
    status_paths,
    validation_report_paths_from_status,
)


class StatusPathsTests(unittest.TestCase):
    def test_parses_two_letter_status_codes(self) -> None:
        out = ' M src/a.py\n?? src/b.py\nMM src/c.py\n'
        self.assertEqual(status_paths(out), ['src/a.py', 'src/b.py', 'src/c.py'])

    def test_skips_lines_shorter_than_four_chars(self) -> None:
        # Line 16: ``if len(line) < 4: continue``. Lines like '?' or 'MM '
        # (< 4 chars) get silently dropped — they can't carry a path.
        out = '?\nM\nABC\n M valid.py\n'  # only the last is parsable
        self.assertEqual(status_paths(out), ['valid.py'])

    def test_handles_rename_arrow_syntax(self) -> None:
        out = 'R  old.py -> new.py\n'
        self.assertEqual(status_paths(out), ['new.py'])

    def test_strips_trailing_slash(self) -> None:
        out = '?? src/\n'
        self.assertEqual(status_paths(out), ['src'])

    def test_skips_lines_whose_normalized_path_is_empty(self) -> None:
        # Branch 21->14: ``line[3:]`` resolves to a blank path
        # (e.g. ``'?? /'`` → ``'/'`` → ``''`` after rstrip) — the
        # entry must be dropped silently rather than appended.
        out = '?? /\n M valid.py\n'
        self.assertEqual(status_paths(out), ['valid.py'])


class ValidationReportPathsTests(unittest.TestCase):
    def test_picks_validation_report_files_only(self) -> None:
        out = ' M src/a.py\n?? .kato/validation_report.md\n'
        result = validation_report_paths_from_status(out)
        self.assertEqual(result, ['.kato/validation_report.md'])


class GeneratedArtifactPathsTests(unittest.TestCase):
    def test_picks_recognized_artifact_roots(self) -> None:
        out = '?? build/foo\n?? dist/bar\n?? src/baz.py\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(sorted(result), ['build', 'dist'])

    def test_excludes_validation_reports(self) -> None:
        # validation_report.md isn't treated as a generic artifact root.
        out = '?? build/foo\n?? .kato/validation_report.md\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(result, ['build'])

    def test_dedupes_same_root(self) -> None:
        out = '?? build/a\n?? build/b\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(result, ['build'])


class StatusContainsOnlyRemovableTests(unittest.TestCase):
    def test_true_when_all_paths_are_removable(self) -> None:
        out = '?? build/a\n?? .kato/validation_report.md\n'
        self.assertTrue(
            status_contains_only_removable_artifacts(
                out, ['build'], ['.kato/validation_report.md'],
            )
        )

    def test_false_when_non_removable_path_present(self) -> None:
        out = '?? build/a\n?? src/unexpected.py\n'
        self.assertFalse(
            status_contains_only_removable_artifacts(
                out, ['build'], [],
            )
        )


if __name__ == '__main__':
    unittest.main()
