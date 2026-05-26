import unittest

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry


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
            PullRequestFields.TITLE: 'PROJ-1 fix it already',
        }

        self.registry.remember_pull_request_context(
            pull_request,
            'feature/proj-1/client',
            agent_session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        self.assertEqual(
            self.registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                'branch_name': 'feature/proj-1/client',
                ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
                'task_id': 'PROJ-1',
                'task_summary': 'fix it already',
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

    def test_session_ids_for_task_normalizes_stored_session_ids(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                TaskFields.ID: 'PROJ-1',
                ImplementationFields.AGENT_SESSION_ID: '  conversation-1\n',
            },
            {
                TaskFields.ID: 'PROJ-1',
                ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            },
        ]

        self.assertEqual(
            self.registry.session_ids_for_task('PROJ-1'),
            ['conversation-1'],
        )

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

    def test_processed_task_pull_requests_returns_empty_when_stored_value_is_not_a_list(self) -> None:
        # Branch 78->80: stored pull_requests is not a list → fall through to ``return []``.
        # Bypass mark_task_processed (which always writes a list) by poking the map directly.
        self.registry.processed_task_map['PROJ-1'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: 'not-a-list',
        }

        self.assertEqual(self.registry.processed_task_pull_requests('PROJ-1'), [])

    def test_tracked_task_ids_skips_blank_task_id_in_pull_request_task_map(self) -> None:
        # Branch 135->134: ``if task_id:`` falsy branch in the
        # pull_request_task_map loop → entry skipped, loop continues.
        self.registry.pull_request_task_map[('client', '17')] = ''
        self.registry.pull_request_task_map[('client', '18')] = 'PROJ-2'

        self.assertEqual(self.registry.tracked_task_ids(), {'PROJ-2'})

    def test_tracked_task_ids_skips_blank_task_id_in_pr_context(self) -> None:
        # Branch 140->138: ``if task_id:`` falsy branch in the
        # pull_request_context_map loop → context skipped, inner loop
        # continues to the next context.
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                TaskFields.ID: '   ',  # blank after .strip()
            },
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                TaskFields.ID: 'PROJ-3',
            },
        ]

        self.assertEqual(self.registry.tracked_task_ids(), {'PROJ-3'})

    def test_task_id_for_pull_request_skips_non_list_pull_requests_in_processed_map(self) -> None:
        # Branch 200->196: ``if not isinstance(pull_requests, list): continue``
        # → loop moves to the next processed task.
        self.registry.processed_task_map['PROJ-bad'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: 'corrupt-not-a-list',
        }
        self.registry.processed_task_map['PROJ-good'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'client',
                }
            ],
        }

        self.assertEqual(
            self.registry.task_id_for_pull_request('17', 'client'),
            'PROJ-good',
        )

    def test_task_id_for_pull_request_keeps_scanning_when_entry_does_not_match(self) -> None:
        # Branch 209->200: inner ``if`` is False → loop continues to the
        # next pull-request entry in the same processed task before
        # eventually returning ''.
        self.registry.processed_task_map['PROJ-1'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                {
                    PullRequestFields.ID: '99',
                    PullRequestFields.REPOSITORY_ID: 'backend',
                },
                {
                    PullRequestFields.ID: '100',
                    PullRequestFields.REPOSITORY_ID: 'client',
                },
            ],
        }

        # Lookup for ('17','client') matches neither entry → falls through
        # the inner loop without setting pull_request_task_map.
        self.assertEqual(
            self.registry.task_id_for_pull_request('17', 'client'), '',
        )
        self.assertNotIn(('client', '17'), self.registry.pull_request_task_map)

    def test_task_id_for_pull_request_skips_non_dict_pull_request_entry(self) -> None:
        # Inner ``if not isinstance(pull_request, dict): continue`` path —
        # included alongside the 209->200 case so both inner-loop
        # branches are exercised together.
        self.registry.processed_task_map['PROJ-1'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                'not-a-dict',
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'client',
                },
            ],
        }

        self.assertEqual(
            self.registry.task_id_for_pull_request('17', 'client'),
            'PROJ-1',
        )
