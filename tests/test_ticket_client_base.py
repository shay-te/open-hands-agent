import unittest

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import TaskCommentFields


class TicketClientBaseTests(unittest.TestCase):
    def test_recognizes_agent_operational_comment_prefixes(self) -> None:
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'OpenHands agent started working on this task in repository backend.'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'OpenHands completed task PROJ-1: Fix the auth flow.'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'OpenHands agent stopped working on this task: gateway timeout'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'OpenHands agent could not safely process this task: timeout'
            )
        )
        self.assertTrue(
            TicketClientBase._is_agent_operational_comment(
                'OpenHands agent skipped this task because the task definition is too thin to work from safely.'
            )
        )
        self.assertFalse(
            TicketClientBase._is_agent_operational_comment(
                'Please add tests before merging.'
            )
        )

    def test_active_retry_blocking_comment_returns_latest_failure_without_override(self) -> None:
        comment = TicketClientBase.active_retry_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent could not safely process this task: timeout'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'Please keep the fix minimal.',
                },
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent stopped working on this task: branch conflict'
                    ),
                },
            ]
        )

        self.assertEqual(
            comment,
            'OpenHands agent stopped working on this task: branch conflict',
        )

    def test_active_retry_blocking_comment_clears_after_explicit_retry_instruction(self) -> None:
        comment = TicketClientBase.active_retry_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent skipped this task because it could not detect '
                        'which repository to use from the task content: no configured '
                        'repository matched task PROJ-1.'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'You can move forward and try again now.',
                },
            ]
        )

        self.assertEqual(comment, '')

    def test_active_retry_blocking_comment_tracks_task_definition_skip_comment(self) -> None:
        comment = TicketClientBase.active_retry_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent skipped this task because the task definition '
                        'is too thin to work from safely.'
                    ),
                }
            ]
        )

        self.assertEqual(
            comment,
            'OpenHands agent skipped this task because the task definition is too thin to work from safely.',
        )

    def test_active_retry_blocking_comment_ignores_negative_retry_instruction(self) -> None:
        comment = TicketClientBase.active_retry_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent stopped working on this task: branch conflict'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: "Please don't try again yet.",
                },
            ]
        )

        self.assertEqual(
            comment,
            'OpenHands agent stopped working on this task: branch conflict',
        )

    def test_active_retry_blocking_comment_ignores_operational_comments_as_override(self) -> None:
        comment = TicketClientBase.active_retry_blocking_comment(
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent could not safely process this task: timeout'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent started working on this task in repository backend.'
                    ),
                },
            ]
        )

        self.assertEqual(
            comment,
            'OpenHands agent could not safely process this task: timeout',
        )

    def test_build_task_description_with_comments_filters_operational_entries(self) -> None:
        description = TicketClientBase._build_task_description_with_comments(
            'Details',
            [
                {
                    TaskCommentFields.AUTHOR: 'shay',
                    TaskCommentFields.BODY: (
                        'OpenHands agent stopped working on this task: timeout'
                    ),
                },
                {
                    TaskCommentFields.AUTHOR: 'reviewer',
                    TaskCommentFields.BODY: 'Please add tests.',
                },
            ],
        )

        self.assertIn('Details', description)
        self.assertIn('Issue comments:', description)
        self.assertIn('- reviewer: Please add tests.', description)
        self.assertNotIn('stopped working on this task', description)

    def test_set_task_comments_persists_normalized_comments_on_task(self) -> None:
        task = Task(
            id='PROJ-1',
            summary='Fix bug',
            description='Details',
            branch_name='feature/proj-1',
        )
        comments = [
            {
                TaskCommentFields.AUTHOR: 'reviewer',
                TaskCommentFields.BODY: 'Please add tests.',
            }
        ]

        TicketClientBase._set_task_comments(task, comments)

        self.assertEqual(getattr(task, TaskCommentFields.ALL_COMMENTS), comments)
