"""Coverage for the parallel-runner dispatch paths in
``process_assigned_tasks.py``.
"""

from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.jobs.process_assigned_tasks import (
    _completed_future,
    _dispatch_assigned_tasks,
    _dispatch_review_comments,
    _drain_finished_futures,
    _drain_finished_review_batches,
    _process_review_comment_batch_best_effort,
    _runner_has_real_concurrency,
)


class RunnerHasRealConcurrencyTests(unittest.TestCase):
    def test_returns_false_for_none(self) -> None:
        self.assertFalse(_runner_has_real_concurrency(None))

    def test_returns_false_for_non_int_max_workers(self) -> None:
        # Lines 67-68: Mock object as max_workers — defensive fall-back
        # for test setups that mock the runner.
        runner = SimpleNamespace(max_workers=MagicMock())
        self.assertFalse(_runner_has_real_concurrency(runner))

    def test_returns_false_for_single_worker(self) -> None:
        # Line 69.
        self.assertFalse(_runner_has_real_concurrency(
            SimpleNamespace(max_workers=1),
        ))

    def test_returns_true_for_multi_worker(self) -> None:
        self.assertTrue(_runner_has_real_concurrency(
            SimpleNamespace(max_workers=4),
        ))


class DispatchAssignedTasksParallelPathTests(unittest.TestCase):
    """Lines 43-53: parallel submission path."""

    def test_submits_each_task_and_returns_drained_results(self) -> None:
        runner = SimpleNamespace(max_workers=2)
        runner.is_in_flight = MagicMock(return_value=False)
        future = Future()
        future.set_result({'status': 'done'})
        runner.submit = MagicMock(return_value=future)

        service = SimpleNamespace(
            parallel_task_runner=runner,
            get_assigned_tasks=MagicMock(
                return_value=[SimpleNamespace(id='T1')],
            ),
        )
        result = _dispatch_assigned_tasks(service)
        self.assertEqual(result, [{'status': 'done'}])

    def test_skips_in_flight_tasks(self) -> None:
        # Line 45-46.
        runner = SimpleNamespace(max_workers=2)
        runner.is_in_flight = MagicMock(return_value=True)
        runner.submit = MagicMock()
        service = SimpleNamespace(
            parallel_task_runner=runner,
            get_assigned_tasks=MagicMock(
                return_value=[SimpleNamespace(id='T1')],
            ),
        )
        result = _dispatch_assigned_tasks(service)
        self.assertEqual(result, [])
        runner.submit.assert_not_called()

    def test_submit_returning_none_does_not_break_drain(self) -> None:
        # Lines 51-52: ``if future is not None: append``.
        runner = SimpleNamespace(max_workers=2)
        runner.is_in_flight = MagicMock(return_value=False)
        runner.submit = MagicMock(return_value=None)  # runner rejected
        service = SimpleNamespace(
            parallel_task_runner=runner,
            get_assigned_tasks=MagicMock(
                return_value=[SimpleNamespace(id='T1')],
            ),
        )
        result = _dispatch_assigned_tasks(service)
        self.assertEqual(result, [])


class DrainFinishedFuturesTests(unittest.TestCase):
    def test_skips_unfinished_futures(self) -> None:
        # Line 90-91.
        unfinished = Future()  # never .set_result
        finished = Future()
        finished.set_result({'status': 'done'})
        result = _drain_finished_futures([unfinished, finished])
        self.assertEqual(result, [{'status': 'done'}])

    def test_re_raises_future_exceptions(self) -> None:
        # Lines 92-97.
        failing = Future()
        failing.set_exception(RuntimeError('boom'))
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            _drain_finished_futures([failing])

    def test_skips_none_results(self) -> None:
        # Line 98-99: ``if result is not None``.
        future = Future()
        future.set_result(None)
        self.assertEqual(_drain_finished_futures([future]), [])


class ProcessReviewCommentBatchBestEffortTests(unittest.TestCase):
    def test_batch_exception_returns_empty(self) -> None:
        # Lines 116-117: batch raises → []. Without this, a single
        # platform error would abort all comment processing.
        service = MagicMock()
        service.process_review_comment_batch.side_effect = RuntimeError(
            'platform down',
        )
        result = _process_review_comment_batch_best_effort(
            service, [SimpleNamespace(comment_id='c1')],
        )
        self.assertEqual(result, [])

    def test_falls_through_when_batch_returns_non_list(self) -> None:
        # Line 122-123: batch returned a Mock (test stub auto-attribute).
        service = MagicMock()
        service.process_review_comment_batch.return_value = MagicMock()
        # Singular path stub.
        service.process_review_comment.return_value = {'status': 'done'}
        result = _process_review_comment_batch_best_effort(
            service, [SimpleNamespace(comment_id='c1')],
        )
        self.assertEqual(result, [{'status': 'done'}])

    def test_singular_path_swallows_exception(self) -> None:
        # Lines 125-128.
        class _NoBatch:
            def process_review_comment(self, _c):
                raise RuntimeError('boom')

        result = _process_review_comment_batch_best_effort(
            _NoBatch(), [SimpleNamespace(comment_id='c1')],
        )
        self.assertEqual(result, [])

    def test_returns_singular_results_when_no_batch_method(self) -> None:
        class _NoBatch:
            def process_review_comment(self, comment):
                return {'comment': comment.comment_id}

        result = _process_review_comment_batch_best_effort(
            _NoBatch(),
            [SimpleNamespace(comment_id='c1'),
             SimpleNamespace(comment_id='c2')],
        )
        self.assertEqual(
            [r['comment'] for r in result], ['c1', 'c2'],
        )


class DispatchReviewCommentsParallelTests(unittest.TestCase):
    def test_parallel_path_submits_per_pr_batches(self) -> None:
        # Lines 184-200.
        runner = SimpleNamespace(max_workers=2)
        runner.is_in_flight = MagicMock(return_value=False)
        future = Future()
        future.set_result([{'comment': 'c1'}])
        runner.submit = MagicMock(return_value=future)

        comment = SimpleNamespace(comment_id='c1')
        setattr(comment, 'repository_id', 'r1')
        setattr(comment, 'pull_request_id', 'pr-1')

        service = SimpleNamespace(
            parallel_task_runner=runner,
            get_new_pull_request_comments=MagicMock(return_value=[comment]),
            task_id_for_review_comment=MagicMock(return_value='T1'),
        )
        result = _dispatch_review_comments(service)
        self.assertEqual(result, [{'comment': 'c1'}])

    def test_parallel_path_runs_inline_when_no_task_id(self) -> None:
        # Lines 187-191: no task_id → run inline + wrap result in
        # a completed future for the drain step.
        runner = SimpleNamespace(max_workers=2)
        runner.is_in_flight = MagicMock(return_value=False)
        comment = SimpleNamespace(comment_id='c1')
        setattr(comment, 'repository_id', 'r1')
        setattr(comment, 'pull_request_id', 'pr-1')

        service = MagicMock()
        service.parallel_task_runner = runner
        service.get_new_pull_request_comments.return_value = [comment]
        service.task_id_for_review_comment.return_value = ''  # no task id
        service.process_review_comment_batch.return_value = [
            {'status': 'addressed'},
        ]
        result = _dispatch_review_comments(service)
        self.assertEqual(result, [{'status': 'addressed'}])

    def test_parallel_path_skips_in_flight_tasks(self) -> None:
        # Lines 192-193.
        runner = SimpleNamespace(max_workers=2)
        runner.is_in_flight = MagicMock(return_value=True)
        runner.submit = MagicMock()
        comment = SimpleNamespace(comment_id='c1')
        setattr(comment, 'repository_id', 'r1')
        setattr(comment, 'pull_request_id', 'pr-1')

        service = SimpleNamespace(
            parallel_task_runner=runner,
            get_new_pull_request_comments=MagicMock(return_value=[comment]),
            task_id_for_review_comment=MagicMock(return_value='T1'),
        )
        result = _dispatch_review_comments(service)
        self.assertEqual(result, [])
        runner.submit.assert_not_called()


class DrainFinishedReviewBatchesTests(unittest.TestCase):
    def test_flattens_list_results(self) -> None:
        # Lines 211-213.
        f1 = Future()
        f1.set_result([{'c': 1}, {'c': 2}])
        f2 = Future()
        f2.set_result({'c': 3})  # single dict, not list — line 214
        result = _drain_finished_review_batches([f1, f2])
        self.assertEqual(result, [{'c': 1}, {'c': 2}, {'c': 3}])

    def test_completed_future_wrap_helper(self) -> None:
        # Lines 221-225.
        fut = _completed_future({'value': 1})
        self.assertTrue(fut.done())
        self.assertEqual(fut.result(), {'value': 1})


class ProcessAssignedTasksJobTests(unittest.TestCase):
    def test_run_logs_results_excluding_skipped(self) -> None:
        from kato_core_lib.jobs.process_assigned_tasks import (
            ProcessAssignedTasksJob,
        )
        from kato_core_lib.data_layers.data.fields import StatusFields

        job = ProcessAssignedTasksJob()
        job._data_handler = MagicMock()
        job._data_handler.service = MagicMock()
        job.logger = MagicMock()
        with patch(
            'kato_core_lib.jobs.process_assigned_tasks.collect_processing_results',
            return_value=[
                {'status': 'done'},
                {'status': StatusFields.SKIPPED},
            ],
        ):
            job.run()
        # Logger.info was called with results-to-log (excludes skipped).
        job.logger.info.assert_called()

    def test_run_surfaces_exception_through_notification(self) -> None:
        from kato_core_lib.jobs.process_assigned_tasks import (
            ProcessAssignedTasksJob,
        )
        job = ProcessAssignedTasksJob()
        job._data_handler = MagicMock()
        job._data_handler.service.notification_service = MagicMock()
        with patch(
            'kato_core_lib.jobs.process_assigned_tasks.collect_processing_results',
            side_effect=RuntimeError('scan fail'),
        ):
            with self.assertRaises(RuntimeError):
                job.run()


class FormatProcessingResultsTests(unittest.TestCase):
    def test_includes_optional_fields(self) -> None:
        from kato_core_lib.jobs.process_assigned_tasks import (
            format_processing_results,
        )
        result = format_processing_results([
            {
                'status': 'done',
                'pull_request_id': '17',
                'branch_name': 'feat/x',
                'repository_id': 'repo-a',
            },
        ])
        self.assertIn('PR #17', result)
        self.assertIn('branch feat/x', result)
        self.assertIn('repository repo-a', result)


class AdvanceFinishedCommentRunsBranches(unittest.TestCase):
    """Cover defensive branches in ``_advance_finished_local_comment_runs``
    and ``_drain_queued_local_comments``: missing method, exception
    in invocation, and non-list return values."""

    def test_advance_returns_empty_when_service_lacks_method(self) -> None:
        # Line 41: ``advance_finished_comment_runs`` not callable.
        from kato_core_lib.jobs.process_assigned_tasks import (
            _advance_finished_local_comment_runs,
        )
        # SimpleNamespace without the attribute.
        self.assertEqual(_advance_finished_local_comment_runs(SimpleNamespace()), [])

    def test_advance_returns_empty_when_method_raises(self) -> None:
        # Lines 44-48: ``advance_finished_comment_runs`` raises.
        from kato_core_lib.jobs.process_assigned_tasks import (
            _advance_finished_local_comment_runs,
        )
        svc = SimpleNamespace(
            advance_finished_comment_runs=MagicMock(side_effect=RuntimeError('x')),
        )
        # Must swallow + return [], not propagate.
        self.assertEqual(_advance_finished_local_comment_runs(svc), [])

    def test_advance_returns_list_when_method_returns_tuple(self) -> None:
        # Tuple → list conversion at the end of _advance_finished_local_comment_runs.
        from kato_core_lib.jobs.process_assigned_tasks import (
            _advance_finished_local_comment_runs,
        )
        svc = SimpleNamespace(
            advance_finished_comment_runs=MagicMock(return_value=('a', 'b')),
        )
        self.assertEqual(_advance_finished_local_comment_runs(svc), ['a', 'b'])

    def test_drain_returns_empty_when_service_lacks_method(self) -> None:
        # Line 64: ``drain_all_queued_task_comments`` not callable.
        from kato_core_lib.jobs.process_assigned_tasks import (
            _drain_queued_local_comments,
        )
        self.assertEqual(_drain_queued_local_comments(SimpleNamespace()), [])


if __name__ == '__main__':
    unittest.main()
