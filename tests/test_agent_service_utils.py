import types
import unittest

from openhands_agent.data_layers.service.agent_service_utils import (
    pull_request_repositories_text,
    pull_request_summary_comment,
    repository_branch_text,
    repository_destination_text,
    repository_ids_text,
    review_fix_context_from_mapping,
    review_fix_result,
    review_comment_fixed_comment,
    review_comment_resolution_key,
    session_suffix,
    task_has_actionable_definition,
    task_started_comment,
)
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    TaskFields,
)
from utils import build_review_comment, build_task


class AgentServiceUtilsTests(unittest.TestCase):
    def test_task_has_actionable_definition_rejects_thin_task_without_real_description(self) -> None:
        task = build_task(summary='fix', description='No description provided.')

        self.assertFalse(task_has_actionable_definition(task))

    def test_task_has_actionable_definition_accepts_real_description_or_detailed_summary(self) -> None:
        self.assertTrue(
            task_has_actionable_definition(
                build_task(summary='fix', description='Update the checkout validation flow')
            )
        )
        self.assertTrue(
            task_has_actionable_definition(
                build_task(
                    summary='Fix the checkout validation edge case',
                    description='No description provided.',
                )
            )
        )

    def test_repository_text_helpers_ignore_missing_values(self) -> None:
        repositories = [
            types.SimpleNamespace(id='client', destination_branch=''),
            types.SimpleNamespace(id='backend', destination_branch='main'),
            types.SimpleNamespace(id='  ', destination_branch='release'),
        ]

        self.assertEqual(repository_ids_text(repositories), 'client, backend')
        self.assertEqual(
            repository_destination_text(repositories),
            'client->default, backend->main',
        )
        self.assertEqual(
            repository_branch_text({'client': 'feature/proj-1/client', 'backend': 'feature/proj-1/backend'}),
            'client->feature/proj-1/client, backend->feature/proj-1/backend',
        )
        self.assertEqual(repository_branch_text({}), '<none>')

    def test_pull_request_repositories_text_ignores_invalid_entries(self) -> None:
        pull_requests = [
            {PullRequestFields.REPOSITORY_ID: 'client'},
            'not-a-dict',
            {PullRequestFields.REPOSITORY_ID: '  '},
            {PullRequestFields.REPOSITORY_ID: 'backend'},
        ]

        self.assertEqual(
            pull_request_repositories_text(pull_requests),
            'client, backend',
        )
        self.assertEqual(pull_request_repositories_text('bad'), '<none>')

    def test_session_suffix_and_started_comment_cover_empty_and_repository_scopes(self) -> None:
        self.assertEqual(session_suffix({}), '')
        self.assertEqual(
            session_suffix({ImplementationFields.SESSION_ID: 'conversation-1'}),
            ' (session conversation-1)',
        )

        no_repo_task = build_task()
        one_repo_task = build_task(repositories=[types.SimpleNamespace(id='client')])
        multi_repo_task = build_task(
            repositories=[
                types.SimpleNamespace(id='client'),
                types.SimpleNamespace(id='backend'),
            ]
        )

        self.assertEqual(
            task_started_comment(no_repo_task),
            'OpenHands agent started working on this task.',
        )
        self.assertEqual(
            task_started_comment(one_repo_task),
            'OpenHands agent started working on this task in repository client.',
        )
        self.assertEqual(
            task_started_comment(multi_repo_task),
            'OpenHands agent started working on this task in repositories: client, backend.',
        )

    def test_review_comment_helpers_use_resolution_target_when_present(self) -> None:
        default_comment = build_review_comment(comment_id='99')
        targeted_comment = build_review_comment(
            comment_id='99',
            resolution_target_id='thread-17',
            resolution_target_type='thread',
        )

        self.assertEqual(
            review_comment_resolution_key(default_comment),
            ('comment', '99'),
        )
        self.assertEqual(
            review_comment_resolution_key(targeted_comment),
            ('thread', 'thread-17'),
        )
        self.assertEqual(
            review_comment_fixed_comment(default_comment),
            'OpenHands addressed review comment 99 on pull request 17.',
        )

    def test_review_fix_context_and_result_use_normalized_mapping_values(self) -> None:
        comment = build_review_comment(pull_request_id='17', comment_id='99')
        context = review_fix_context_from_mapping(
            {
                PullRequestFields.REPOSITORY_ID: ' client ',
                'branch_name': ' feature/proj-1/client ',
                ImplementationFields.SESSION_ID: ' conversation-1 ',
                TaskFields.ID: ' PROJ-1 ',
                TaskFields.SUMMARY: ' Fix bug ',
            }
        )

        self.assertEqual(context.repository_id, 'client')
        self.assertEqual(context.branch_name, 'feature/proj-1/client')
        self.assertEqual(context.session_id, 'conversation-1')
        self.assertEqual(context.task_id, 'PROJ-1')
        self.assertEqual(context.task_summary, 'Fix bug')
        self.assertEqual(
            review_fix_result(comment, context),
            {
                'status': 'updated',
                'pull_request_id': '17',
                'branch_name': 'feature/proj-1/client',
                'repository_id': 'client',
            },
        )

    def test_pull_request_summary_comment_includes_links_and_failed_repositories(self) -> None:
        task = build_task(task_id='PROJ-1', summary='Fix bug')
        summary = pull_request_summary_comment(
            task,
            [
                {
                    PullRequestFields.REPOSITORY_ID: 'client',
                    PullRequestFields.URL: 'https://example.com/pr/17',
                }
            ],
            ['backend'],
        )

        self.assertIn('OpenHands completed task PROJ-1: Fix bug.', summary)
        self.assertNotIn('Validation report:', summary)
        self.assertIn('Published review links:', summary)
        self.assertIn('- client: https://example.com/pr/17', summary)
        self.assertIn('Failed repositories: backend', summary)

    def test_pull_request_summary_comment_includes_validation_report_when_present(self) -> None:
        task = build_task(task_id='PROJ-1', summary='Fix bug')
        summary = pull_request_summary_comment(
            task,
            [],
            [],
            'Validation report: no tests were defined.',
        )

        self.assertIn('Validation report:', summary)
        self.assertIn('Validation report: no tests were defined.', summary)
