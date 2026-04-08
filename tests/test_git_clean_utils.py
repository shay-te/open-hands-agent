import unittest

from kato.helpers.git_clean_utils import (
    generated_artifact_paths_from_status,
    git_ready_command_summary,
    status_contains_only_removable_artifacts,
    status_paths,
    validation_report_paths_from_status,
)


class GitCleanUtilsTests(unittest.TestCase):
    def test_status_paths_normalizes_untracked_modified_and_renamed_paths(self) -> None:
        status_output = (
            ' M src/app.js\n'
            '?? build/\n'
            'R  docs/old.md -> validation_report.md\n'
        )

        self.assertEqual(
            status_paths(status_output),
            ['src/app.js', 'build', 'validation_report.md'],
        )

    def test_validation_report_paths_from_status_detects_renamed_report(self) -> None:
        status_output = 'R  docs/old.md -> validation_report.md\n?? build/main.js\n'

        self.assertEqual(
            validation_report_paths_from_status(status_output),
            ['validation_report.md'],
        )

    def test_generated_artifact_paths_from_status_deduplicates_known_roots(self) -> None:
        status_output = (
            '?? build/main.js\n'
            ' D build/index.html\n'
            '?? dist/app.js\n'
            '?? validation_report.md\n'
            ' M src/app.js\n'
        )

        self.assertEqual(
            generated_artifact_paths_from_status(status_output),
            ['build', 'dist'],
        )

    def test_status_contains_only_removable_artifacts_accepts_generated_roots_and_report(self) -> None:
        status_output = (
            '?? build/main.js\n'
            ' D dist/index.js\n'
            '?? validation_report.md\n'
        )

        self.assertTrue(
            status_contains_only_removable_artifacts(
                status_output,
                ['build', 'dist'],
                ['validation_report.md'],
            )
        )

    def test_status_contains_only_removable_artifacts_rejects_source_changes(self) -> None:
        status_output = '?? build/main.js\n M src/app.js\n'

        self.assertFalse(
            status_contains_only_removable_artifacts(
                status_output,
                ['build'],
                [],
            )
        )

    def test_git_ready_command_summary_includes_remote_sync_when_requested(self) -> None:
        self.assertEqual(
            git_ready_command_summary('master', include_remote_sync=True),
            'git fetch origin && git checkout -f master && '
            'git reset --hard origin/master && git clean -fd',
        )

    def test_git_ready_command_summary_skips_remote_sync_when_not_requested(self) -> None:
        self.assertEqual(
            git_ready_command_summary('main', include_remote_sync=False),
            'git checkout -f main && git clean -fd',
        )


if __name__ == '__main__':
    unittest.main()
