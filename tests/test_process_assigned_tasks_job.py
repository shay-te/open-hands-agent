import types
import unittest
from unittest.mock import Mock


from kato_core_lib.jobs.process_assigned_tasks import (
    ProcessAssignedTasksJob,
    collect_processing_results,
    format_processing_results,
)
from kato_core_lib.kato_core_lib import KatoCoreLib
from tests.utils import sync_create_start_core_lib


class ProcessAssignedTasksJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job = ProcessAssignedTasksJob()
        self.openhands_core_lib = sync_create_start_core_lib()

    def test_initialized_accepts_kato_core_lib(self) -> None:
        self.job.initialized(self.openhands_core_lib)

        self.assertIs(self.job._data_handler, self.openhands_core_lib)
        self.assertIsInstance(self.job._data_handler, KatoCoreLib)

    def test_initialized_rejects_invalid_data_handler(self) -> None:
        with self.assertRaises(AssertionError):
            self.job.initialized(types.SimpleNamespace())

    def test_run_logs_results(self) -> None:
        results = [
            {
                'status': 'updated',
                'pull_request_id': '17',
                'branch_name': 'UNA-17',
                'repository_id': 'ob-love-admin-client',
            }
        ]
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = ['task-1']
        self.openhands_core_lib.service.process_assigned_task.return_value = results[0]
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = []
        self.openhands_core_lib.service.process_review_comment = Mock()
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)
        returned_results = self.job.run()

        self.assertIsNone(returned_results)
        self.job.logger.info.assert_called_once_with(
            'completed processing results:\n%s',
            '- updated | PR #17 | branch UNA-17 | repository ob-love-admin-client',
        )

    def test_run_stays_quiet_when_no_results_are_found(self) -> None:
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = []
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = []
        self.openhands_core_lib.service.process_assigned_task = Mock()
        self.openhands_core_lib.service.process_review_comment = Mock()
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)

        self.job.run()

        self.job.logger.info.assert_not_called()

    def test_run_stays_quiet_when_only_skip_results_are_found(self) -> None:
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = ['task-1']
        self.openhands_core_lib.service.process_assigned_task.return_value = {'status': 'skipped'}
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = []
        self.openhands_core_lib.service.process_review_comment = Mock()
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)

        self.job.run()

        self.job.logger.info.assert_not_called()

    def test_run_sends_failure_notification_before_reraising(self) -> None:
        notification_service = Mock()
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.side_effect = RuntimeError('service down')
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = []
        self.openhands_core_lib.service.notification_service = notification_service
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)

        with self.assertRaisesRegex(RuntimeError, 'service down'):
            self.job.run()

        notification_service.notify_failure.assert_called_once()
        self.job.logger.exception.assert_called_once_with(
            'process_assigned_tasks_job failed'
        )

    def test_run_preserves_original_error_when_failure_notification_breaks(self) -> None:
        notification_service = Mock()
        notification_service.notify_failure.side_effect = RuntimeError('mailer down')
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.side_effect = RuntimeError('service down')
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = []
        self.openhands_core_lib.service.notification_service = notification_service
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)

        with self.assertRaisesRegex(RuntimeError, 'service down'):
            self.job.run()

        self.assertEqual(self.job.logger.exception.call_count, 2)

    def test_run_loops_over_each_assigned_task(self) -> None:
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = ['task-1', 'task-2']
        self.openhands_core_lib.service.process_assigned_task.side_effect = [
            {'id': '17'},
            {'id': '18'},
        ]
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = []
        self.openhands_core_lib.service.process_review_comment = Mock()
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)
        self.job.run()

        self.assertEqual(
            self.openhands_core_lib.service.process_assigned_task.call_args_list[0].args,
            ('task-1',),
        )
        self.assertEqual(
            self.openhands_core_lib.service.process_assigned_task.call_args_list[1].args,
            ('task-2',),
        )

    def test_collect_processing_results_processes_tasks_strictly_in_order(self) -> None:
        service = Mock()
        service.get_assigned_tasks.return_value = ['task-1', 'task-2']
        service.get_new_pull_request_comments.return_value = []
        events: list[str] = []

        def process_task(task_id: str):
            events.append(f'start:{task_id}')
            events.append(f'end:{task_id}')
            return {'id': task_id}

        service.process_assigned_task.side_effect = process_task
        service.process_review_comment = Mock()

        results = collect_processing_results(service)

        self.assertEqual(results, [{'id': 'task-1'}, {'id': 'task-2'}])
        self.assertEqual(
            events,
            ['start:task-1', 'end:task-1', 'start:task-2', 'end:task-2'],
        )

    def test_collect_processing_results_continues_after_review_comment_failure(self) -> None:
        service = Mock()
        service.get_assigned_tasks.return_value = []
        service.get_new_pull_request_comments.return_value = ['comment-1', 'comment-2']
        service.process_assigned_task = Mock()
        service.process_review_comment.side_effect = [
            RuntimeError('sandbox entered error state'),
            {'status': 'updated', 'pull_request_id': '18'},
        ]

        results = collect_processing_results(service)

        self.assertEqual(results, [{'status': 'updated', 'pull_request_id': '18'}])
        self.assertEqual(
            service.process_review_comment.call_args_list[0].args,
            ('comment-1',),
        )
        self.assertEqual(
            service.process_review_comment.call_args_list[1].args,
            ('comment-2',),
        )

    def test_run_loops_over_new_pull_request_comments_after_tasks(self) -> None:
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.get_assigned_tasks.return_value = ['task-1']
        self.openhands_core_lib.service.process_assigned_task.return_value = {'id': '17'}
        self.openhands_core_lib.service.get_new_pull_request_comments.return_value = [
            'comment-1',
            'comment-2',
        ]
        self.openhands_core_lib.service.process_review_comment.side_effect = [
            {'status': 'updated'},
            {'status': 'updated'},
        ]
        self.openhands_core_lib.service.notification_service = Mock()
        self.job.logger = Mock()
        self.job.initialized(self.openhands_core_lib)
        self.job.run()

        self.assertEqual(
            self.openhands_core_lib.service.process_review_comment.call_args_list[0].args,
            ('comment-1',),
        )
        self.assertEqual(
            self.openhands_core_lib.service.process_review_comment.call_args_list[1].args,
            ('comment-2',),
        )

    def test_format_processing_results_uses_human_readable_summary(self) -> None:
        formatted = format_processing_results(
            [
                {
                    'status': 'updated',
                    'pull_request_id': '993',
                    'branch_name': 'UNA-2463',
                    'repository_id': 'ob-love-admin-client',
                },
                {
                    'status': 'created',
                    'pull_request_id': '994',
                    'repository_id': 'ob-love-admin-api',
                },
            ]
        )

        self.assertEqual(
            formatted,
            '\n'.join(
                [
                    '- updated | PR #993 | branch UNA-2463 | repository ob-love-admin-client',
                    '- created | PR #994 | repository ob-love-admin-api',
                ]
            ),
        )
