import types
import unittest

from kato.helpers.pull_request_utils import (
    pull_request_description,
    pull_request_repositories_text,
    pull_request_summary_comment,
    pull_request_title,
)
from kato.helpers.mission_logging_utils import log_mission_step
from kato.helpers.review_comment_utils import (
    review_fix_context_from_mapping,
    review_fix_result,
    review_comment_fixed_comment,
    review_comment_resolution_key,
)
from kato.helpers.task_execution_utils import (
    apply_testing_message,
    implementation_succeeded,
    skip_task_result,
    task_execution_report,
    testing_failed_result,
    testing_succeeded,
)
from kato.helpers.task_context_utils import (
    repository_branch_text,
    repository_destination_text,
    repository_ids_text,
    session_suffix,
    task_has_actionable_definition,
    task_started_comment,
)
from kato.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from kato.data_layers.data.task import Task
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

    def test_pull_request_title_uses_task_code_and_task_title(self) -> None:
        task = build_task(task_id='UNA-2308', summary='Enhance Sidebar Scroll Buttons with Long-Press Scrolling and Gradient UI')

        self.assertEqual(
            pull_request_title(task),
            'UNA-2308 Enhance Sidebar Scroll Buttons with Long-Press Scrolling and Gradient UI',
        )

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
            'Kato agent started working on this task.',
        )
        self.assertEqual(
            task_started_comment(one_repo_task),
            'Kato agent started working on this task in repository client.',
        )
        self.assertEqual(
            task_started_comment(multi_repo_task),
            'Kato agent started working on this task in repositories: client, backend.',
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
            'Kato addressed review comment 99 on pull request 17.',
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

        self.assertIn('Kato completed task PROJ-1: Fix bug.', summary)
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
            'Implementation summary:\n- client/app.ts\n  Updated the client flow.\n\nValidation report:\nNo tests were defined.',
        )

        self.assertIn('Execution report:', summary)
        self.assertIn('Implementation summary:', summary)
        self.assertIn('Updated the client flow.', summary)
        self.assertIn('Validation report:', summary)
        self.assertIn('No tests were defined.', summary)

    def test_pull_request_description_is_structured_and_explanatory(self) -> None:
        task = build_task(
            task_id='PROJ-1',
            summary='Fix bug',
            description='Update the checkout validation flow',
        )
        description = pull_request_description(
            task,
            {
                'summary': 'Files changed:\n- client/app.ts\n  Updated the client flow.',
                'message': 'Validation report: no tests were defined.',
            },
        )

        self.assertIn('Kato completed task PROJ-1: Fix bug.', description)
        self.assertIn('Requested change:', description)
        self.assertIn('Update the checkout validation flow', description)
        self.assertIn('Implementation summary:', description)
        self.assertIn('Files changed:', description)
        self.assertIn('Execution notes:', description)
        self.assertIn('Validation report: no tests were defined.', description)

    def test_task_execution_helpers_cover_success_and_result_shapes(self) -> None:
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.MESSAGE: 'Implementation note',
            Task.summary.key: 'Files changed:\n- client/app.ts',
        }
        testing = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.MESSAGE: 'Validation report: all good',
        }

        self.assertTrue(implementation_succeeded(execution))
        self.assertTrue(testing_succeeded(testing))
        self.assertEqual(
            apply_testing_message(dict(execution), testing)[ImplementationFields.MESSAGE],
            'Validation report: all good',
        )
        self.assertEqual(
            task_execution_report(apply_testing_message(dict(execution), testing)),
            'Implementation summary:\nFiles changed:\n- client/app.ts\nValidation report:\nValidation report: all good',
        )
        self.assertEqual(
            testing_failed_result('PROJ-1'),
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.TESTING_FAILED,
                PullRequestFields.PULL_REQUESTS: [],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )
        self.assertEqual(
            skip_task_result('PROJ-1', [{'id': '17'}]),
            {
                'id': 'PROJ-1',
                StatusFields.STATUS: StatusFields.SKIPPED,
                PullRequestFields.PULL_REQUESTS: [{'id': '17'}],
                PullRequestFields.FAILED_REPOSITORIES: [],
            },
        )

    def test_log_mission_step_formats_messages_safely(self) -> None:
        logger = unittest.mock.Mock()

        log_mission_step(logger, 'PROJ-1', 'created %s pull request', 'one')
        log_mission_step(logger, 'PROJ-2', 'literal message with %s and %d')

        logger.info.assert_any_call('Mission %s: %s', 'PROJ-1', 'created one pull request')
        logger.info.assert_any_call(
            'Mission %s: %s',
            'PROJ-2',
            'literal message with %s and %d',
        )
