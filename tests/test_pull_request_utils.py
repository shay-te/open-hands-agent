"""Coverage for the YouTrack/Jira summary-comment renderer.

The summary comment is the operator's window into what kato actually
did with a task. Pin down both the success-listing format and — more
importantly — the per-repo failure-reason rendering, since that's the
diagnostic users need when a multi-repo task half-publishes.
"""

from __future__ import annotations

import unittest

from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.helpers.pull_request_utils import pull_request_summary_comment
from tests.utils import build_task


class PullRequestSummaryCommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = build_task(summary='fix it already', description='Some context')
        self.successful_pr = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
        }

    def test_failure_entries_render_with_reason_when_provided_as_dicts(self) -> None:
        comment = pull_request_summary_comment(
            self.task,
            pull_requests=[self.successful_pr],
            failed_repositories=[
                {
                    PullRequestFields.REPOSITORY_ID: 'backend',
                    'error': 'failed to push branch UNA-2574 to origin',
                },
                {
                    PullRequestFields.REPOSITORY_ID: 'pay-core-lib',
                    'error': 'bitbucket 502 after 3 attempts',
                },
            ],
        )

        self.assertIn('Failed repositories:', comment)
        self.assertIn('- backend: failed to push branch UNA-2574 to origin', comment)
        self.assertIn('- pay-core-lib: bitbucket 502 after 3 attempts', comment)

    def test_failure_entries_render_with_reason_when_provided_as_tuples(self) -> None:
        comment = pull_request_summary_comment(
            self.task,
            pull_requests=[self.successful_pr],
            failed_repositories=[('backend', 'github down')],
        )

        self.assertIn('- backend: github down', comment)

    def test_legacy_string_failure_list_still_works_without_reason(self) -> None:
        # Older callers pass plain ids; the comment should still render
        # cleanly (no reason after the colon, no traceback).
        comment = pull_request_summary_comment(
            self.task,
            pull_requests=[self.successful_pr],
            failed_repositories=['backend', 'pay-core-lib'],
        )

        self.assertIn('Failed repositories:', comment)
        self.assertIn('- backend', comment)
        self.assertIn('- pay-core-lib', comment)
        # No bare colons indicating a missing reason.
        self.assertNotIn('- backend:', comment)
        self.assertNotIn('- pay-core-lib:', comment)

    def test_dict_failure_with_empty_reason_drops_the_colon(self) -> None:
        comment = pull_request_summary_comment(
            self.task,
            pull_requests=[],
            failed_repositories=[
                {PullRequestFields.REPOSITORY_ID: 'backend', 'error': ''},
            ],
        )

        # Repo id is listed but no trailing colon-blank line.
        self.assertIn('- backend', comment)
        self.assertNotIn('- backend:', comment)

    def test_no_failures_omits_the_section_entirely(self) -> None:
        comment = pull_request_summary_comment(
            self.task,
            pull_requests=[self.successful_pr],
            failed_repositories=[],
        )

        self.assertNotIn('Failed repositories', comment)

    def test_failure_section_appears_after_successful_links(self) -> None:
        comment = pull_request_summary_comment(
            self.task,
            pull_requests=[self.successful_pr],
            failed_repositories=[
                {PullRequestFields.REPOSITORY_ID: 'backend', 'error': 'oops'},
            ],
        )

        self.assertLess(
            comment.index('Published review links:'),
            comment.index('Failed repositories:'),
        )


if __name__ == '__main__':
    unittest.main()
