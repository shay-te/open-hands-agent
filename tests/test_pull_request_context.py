import unittest


from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    TaskFields,
)
from openhands_agent.pull_request_context import (
    build_pull_request_context,
    pull_request_context_key,
)


class PullRequestContextTests(unittest.TestCase):
    def test_build_pull_request_context_normalizes_optional_fields(self) -> None:
        context = build_pull_request_context(
            ' client ',
            ' feature/proj-1 ',
            ' conversation-1 ',
            ' PROJ-1 ',
            ' Fix bug ',
        )

        self.assertEqual(
            context,
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1',
                ImplementationFields.SESSION_ID: 'conversation-1',
                TaskFields.ID: 'PROJ-1',
                TaskFields.SUMMARY: 'Fix bug',
            },
        )

    def test_pull_request_context_key_extracts_repository_and_branch(self) -> None:
        self.assertEqual(
            pull_request_context_key(
                {
                    PullRequestFields.REPOSITORY_ID: ' client ',
                    'branch_name': ' feature/proj-1 ',
                }
            ),
            ('client', 'feature/proj-1'),
        )
