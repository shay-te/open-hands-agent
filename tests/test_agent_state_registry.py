import unittest

from kato.data_layers.data.fields import PullRequestFields, ReviewCommentFields, StatusFields
from kato.data_layers.service.agent_state_registry import AgentStateRegistry


class AgentStateRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentStateRegistry()

    def test_mark_task_processed_round_trips_pull_requests(self) -> None:
        pull_requests = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
            }
        ]

        self.registry.mark_task_processed('PROJ-1', pull_requests)

        self.assertTrue(self.registry.is_task_processed('PROJ-1'))
        self.assertEqual(self.registry.processed_task_pull_requests('PROJ-1'), pull_requests)
        self.assertEqual(
            self.registry.processed_task_map['PROJ-1'][StatusFields.STATUS],
            StatusFields.READY_FOR_REVIEW,
        )

    def test_processed_task_pull_requests_returns_empty_list_for_unknown_task(self) -> None:
        self.assertEqual(self.registry.processed_task_pull_requests('missing'), [])

    def test_remember_pull_request_context_and_pull_request_context_round_trip(self) -> None:
        pull_request = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
        }

        self.registry.remember_pull_request_context(
            pull_request,
            'feature/proj-1/client',
            session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='Fix bug',
        )

        self.assertEqual(
            self.registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
                'session_id': 'conversation-1',
                'task_id': 'PROJ-1',
                'task_summary': 'Fix bug',
            },
        )
        self.assertEqual(self.registry.task_id_for_pull_request('17', 'client'), 'PROJ-1')

    def test_pull_request_context_raises_on_ambiguous_pr_id(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        ]

        with self.assertRaisesRegex(ValueError, 'ambiguous pull request id across repositories'):
            self.registry.pull_request_context('17')

    def test_pull_request_context_disambiguates_when_repository_id_is_provided(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        ]

        self.assertEqual(
            self.registry.pull_request_context('17', 'backend'),
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        )

    def test_task_id_for_pull_request_falls_back_to_processed_task_map_and_caches_result(self) -> None:
        self.registry.mark_task_processed(
            'PROJ-1',
            [
                {
                    PullRequestFields.REPOSITORY_ID: 'client',
                    PullRequestFields.ID: '17',
                }
            ],
        )
        self.registry.pull_request_task_map.clear()

        self.assertEqual(self.registry.task_id_for_pull_request('17', 'client'), 'PROJ-1')
        self.assertEqual(
            self.registry.pull_request_task_map[('client', '17')],
            'PROJ-1',
        )

    def test_task_id_for_pull_request_returns_empty_string_when_unknown(self) -> None:
        self.assertEqual(self.registry.task_id_for_pull_request('17', 'client'), '')

    def test_review_comment_processed_round_trip(self) -> None:
        self.assertFalse(self.registry.is_review_comment_processed('client', '17', '99'))

        self.registry.mark_review_comment_processed('client', '17', '99')

        self.assertTrue(self.registry.is_review_comment_processed('client', '17', '99'))

    def test_remember_pull_request_context_deduplicates_same_repository_and_branch(self) -> None:
        pull_request = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
        }

        self.registry.remember_pull_request_context(pull_request, 'PROJ-1')
        self.registry.remember_pull_request_context(pull_request, 'PROJ-1')

        self.assertEqual(
            self.registry.pull_request_context_map['17'],
            [
                {
                    PullRequestFields.REPOSITORY_ID: 'client',
                    'branch_name': 'PROJ-1',
                }
            ],
        )

    def test_tracked_pull_request_contexts_deduplicates_identical_entries(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
        ]

        self.assertEqual(
            self.registry.tracked_pull_request_contexts(),
            [
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'client',
                    'branch_name': 'feature/proj-1/client',
                }
            ],
        )
