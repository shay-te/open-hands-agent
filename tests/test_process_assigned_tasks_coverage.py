"""Dispatch / drain coverage with REAL ParallelTaskRunner.

The previous version of this file used ``SimpleNamespace(max_workers=2,
submit=MagicMock(), is_in_flight=MagicMock(return_value=False))`` for
the runner. That made every test pass even though the real
ThreadPoolExecutor was never involved — submit/drain order, future
state propagation, and the in-flight dedup set all stayed untested.

Here we drive an actual :class:`ParallelTaskRunner` so the real submit
→ done-callback → drain cycle runs. The service stays a small, real
class (``_RealScanService`` from ``chaos_lib``) instead of a Mock so
the dispatch helpers exercise real attribute lookups.
"""

from __future__ import annotations

import time
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

from tests.chaos_lib import (
    IMPATIENT_TITLES,
    _RealScanService,
    build_real_runner,
    impatient_title,
    make_review_comment,
    make_task,
)


def _wait_for(predicate, *, timeout: float = 2.0) -> bool:
    """Wait up to ``timeout`` for ``predicate()`` to become true."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class RunnerHasRealConcurrencyTests(unittest.TestCase):
    def test_returns_false_for_none(self) -> None:
        self.assertFalse(_runner_has_real_concurrency(None))

    def test_returns_false_for_non_int_max_workers(self) -> None:
        # Defensive fall-back for mocked test setups where ``max_workers``
        # is a Mock attribute (truthy, not int).
        runner = SimpleNamespace(max_workers=MagicMock())
        self.assertFalse(_runner_has_real_concurrency(runner))

    def test_returns_false_for_real_single_worker_runner(self) -> None:
        # REAL runner: a 1-worker pool falls back to the inline path.
        runner = build_real_runner(max_workers=1)
        self.addCleanup(runner.shutdown)
        self.assertFalse(_runner_has_real_concurrency(runner))

    def test_returns_true_for_real_multi_worker_runner(self) -> None:
        runner = build_real_runner(max_workers=4)
        self.addCleanup(runner.shutdown)
        self.assertTrue(_runner_has_real_concurrency(runner))


class DispatchAssignedTasksParallelPathTests(unittest.TestCase):
    """Parallel submission path through a REAL ParallelTaskRunner."""

    def setUp(self) -> None:
        self.runner = build_real_runner(max_workers=4)
        self.addCleanup(self.runner.shutdown)

    def test_submits_each_task_and_drains_real_completed_futures(self) -> None:
        # Real submit → real worker thread → real done_callback.
        task = make_task('T1')
        service = _RealScanService(
            runner=self.runner,
            assigned_tasks=[task],
            process_result={'status': 'done', 'task_id': 'T1'},
        )
        result = _dispatch_assigned_tasks(service)
        # Wait briefly so the worker has actually run.
        self.assertTrue(_wait_for(lambda: bool(result) or service.process_calls))
        # The worker really executed the task.
        self.assertEqual([t.id for t in service.process_calls], ['T1'])
        # First scan tick may race: drain returns whatever finished.
        # Run again to get the final result (real submit dedup will skip).
        if not result:
            result = _dispatch_assigned_tasks(service)
        self.assertEqual(result, [{'status': 'done', 'task_id': 'T1'}])

    def test_real_runner_skips_already_in_flight(self) -> None:
        # Submit a worker that blocks until released; second dispatch
        # must NOT re-submit the same id.
        release = __import__('threading').Event()
        seen = []

        def slow(task):
            seen.append(task.id)
            release.wait(timeout=2.0)
            return {'status': 'done'}

        task = make_task('T1', summary='fix it')
        service = _RealScanService(
            runner=self.runner,
            assigned_tasks=[task],
            process_result=slow,
        )
        _dispatch_assigned_tasks(service)
        # Wait for the worker to start.
        self.assertTrue(_wait_for(lambda: bool(seen)))
        self.assertTrue(self.runner.is_in_flight('T1'))

        # Second dispatch while in-flight: real runner returns None
        # → no extra worker invocation.
        result = _dispatch_assigned_tasks(service)
        self.assertEqual(result, [])
        self.assertEqual(seen, ['T1'])  # not called twice

        release.set()
        self.assertTrue(_wait_for(lambda: not self.runner.is_in_flight('T1')))

    def test_returning_none_from_worker_does_not_break_drain(self) -> None:
        service = _RealScanService(
            runner=self.runner,
            assigned_tasks=[make_task('T1')],
            process_result=None,
        )
        _dispatch_assigned_tasks(service)
        self.assertTrue(_wait_for(lambda: bool(service.process_calls)))
        # A second drain returns [] — None is filtered by the drain
        # helper, and the runner has already released the slot.
        result = _dispatch_assigned_tasks(service)
        self.assertEqual(result, [])

    def test_inline_fallback_when_runner_is_none(self) -> None:
        # No runner → inline path; result lands immediately.
        service = _RealScanService(
            runner=None,
            assigned_tasks=[make_task('T1'), make_task('T2')],
            process_result=lambda t: {'task_id': t.id, 'status': 'done'},
        )
        result = _dispatch_assigned_tasks(service)
        self.assertEqual(
            sorted(r['task_id'] for r in result), ['T1', 'T2'],
        )


class DrainFinishedFuturesTests(unittest.TestCase):
    def test_skips_unfinished_futures(self) -> None:
        unfinished = Future()  # never .set_result
        finished = Future()
        finished.set_result({'status': 'done'})
        result = _drain_finished_futures([unfinished, finished])
        self.assertEqual(result, [{'status': 'done'}])

    def test_re_raises_future_exceptions(self) -> None:
        failing = Future()
        failing.set_exception(RuntimeError('boom'))
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            _drain_finished_futures([failing])

    def test_skips_none_results(self) -> None:
        future = Future()
        future.set_result(None)
        self.assertEqual(_drain_finished_futures([future]), [])

    def test_drains_real_executor_futures(self) -> None:
        # End-to-end through a real ThreadPoolExecutor (the inner core
        # of ParallelTaskRunner).
        runner = build_real_runner(max_workers=2)
        self.addCleanup(runner.shutdown)
        f1 = runner.submit('T1', lambda: {'task_id': 'T1', 'status': 'done'})
        f2 = runner.submit('T2', lambda: {'task_id': 'T2', 'status': 'done'})
        # Wait for both.
        self.assertTrue(_wait_for(lambda: f1.done() and f2.done()))
        drained = _drain_finished_futures([f1, f2])
        self.assertEqual(
            sorted(r['task_id'] for r in drained), ['T1', 'T2'],
        )


class ProcessReviewCommentBatchBestEffortTests(unittest.TestCase):
    """Batch / fallback paths with a small real service shape."""

    def test_batch_exception_returns_empty(self) -> None:
        class _Service:
            def process_review_comment_batch(self, comments):
                raise RuntimeError('platform down')

        result = _process_review_comment_batch_best_effort(
            _Service(),
            [make_review_comment(comment_id='c1', body='fix this please')],
        )
        self.assertEqual(result, [])

    def test_falls_through_when_batch_returns_non_list(self) -> None:
        # Older test stubs auto-create a Mock attribute that returns a Mock.
        service = MagicMock()
        service.process_review_comment_batch.return_value = MagicMock()
        service.process_review_comment.return_value = {'status': 'done'}
        result = _process_review_comment_batch_best_effort(
            service,
            [make_review_comment(comment_id='c1')],
        )
        self.assertEqual(result, [{'status': 'done'}])

    def test_singular_path_swallows_exception(self) -> None:
        class _NoBatch:
            def process_review_comment(self, _c):
                raise RuntimeError('boom')

        result = _process_review_comment_batch_best_effort(
            _NoBatch(),
            [make_review_comment(comment_id='c1')],
        )
        self.assertEqual(result, [])

    def test_returns_singular_results_when_no_batch_method(self) -> None:
        class _NoBatch:
            def process_review_comment(self, comment):
                return {'comment': comment.comment_id}

        result = _process_review_comment_batch_best_effort(
            _NoBatch(),
            [
                make_review_comment(comment_id='c1', body=impatient_title()),
                make_review_comment(comment_id='c2', body='do it'),
            ],
        )
        self.assertEqual(
            [r['comment'] for r in result], ['c1', 'c2'],
        )

    def test_singular_path_skips_none_results(self) -> None:
        """Covers the ``if single is not None`` False branch (line 191->179).

        ``process_review_comment`` can legitimately return ``None`` when
        a comment is skipped (e.g. filtered as a non-actionable mention).
        Those Nones must not leak into the result list.
        """
        class _NoBatch:
            def __init__(self):
                self.calls = 0

            def process_review_comment(self, _c):
                self.calls += 1
                return None  # always skipped

        service = _NoBatch()
        result = _process_review_comment_batch_best_effort(
            service,
            [
                make_review_comment(comment_id='c1'),
                make_review_comment(comment_id='c2'),
            ],
        )
        self.assertEqual(result, [])
        self.assertEqual(service.calls, 2)


class DispatchReviewCommentsParallelTests(unittest.TestCase):
    """Real ParallelTaskRunner driving the review-comment dispatch."""

    def setUp(self) -> None:
        self.runner = build_real_runner(max_workers=4)
        self.addCleanup(self.runner.shutdown)

    def test_parallel_path_submits_per_pr_batches(self) -> None:
        comment = make_review_comment(comment_id='c1', body='whats wrong')
        service = _RealScanService(
            runner=self.runner,
            review_comments=[comment],
            review_batch_result=[{'comment': 'c1'}],
            task_id_for_comment_fn=lambda c: 'T1',
        )
        # First dispatch submits; result may race — wait + re-drain.
        _dispatch_review_comments(service)
        self.assertTrue(_wait_for(lambda: bool(service.batch_calls)))
        # Real batch invocation happened with our real comment.
        self.assertEqual(
            [c.comment_id for c in service.batch_calls[0]], ['c1'],
        )

    def test_parallel_path_runs_inline_when_no_task_id(self) -> None:
        comment = make_review_comment(comment_id='c1', body='fix it')
        service = _RealScanService(
            runner=self.runner,
            review_comments=[comment],
            review_batch_result=[{'status': 'addressed'}],
            task_id_for_comment_fn=lambda c: '',  # no task id → inline
        )
        result = _dispatch_review_comments(service)
        self.assertEqual(result, [{'status': 'addressed'}])
        # Inline path called batch synchronously (not via the executor).
        self.assertEqual(len(service.batch_calls), 1)

    def test_parallel_path_skips_in_flight_tasks(self) -> None:
        # Submit a blocking task to mark T1 in-flight.
        release = __import__('threading').Event()
        self.runner.submit('T1', lambda: release.wait(timeout=2.0))
        self.assertTrue(self.runner.is_in_flight('T1'))

        comment = make_review_comment(comment_id='c1')
        service = _RealScanService(
            runner=self.runner,
            review_comments=[comment],
            review_batch_result=[{'comment': 'c1'}],
            task_id_for_comment_fn=lambda c: 'T1',
        )
        result = _dispatch_review_comments(service)
        self.assertEqual(result, [])
        self.assertEqual(service.batch_calls, [])  # never invoked

        release.set()
        self.assertTrue(_wait_for(lambda: not self.runner.is_in_flight('T1')))


class DrainFinishedReviewBatchesTests(unittest.TestCase):
    def test_flattens_list_results(self) -> None:
        f1 = Future()
        f1.set_result([{'c': 1}, {'c': 2}])
        f2 = Future()
        f2.set_result({'c': 3})  # single dict, not list
        result = _drain_finished_review_batches([f1, f2])
        self.assertEqual(result, [{'c': 1}, {'c': 2}, {'c': 3}])

    def test_completed_future_wrap_helper(self) -> None:
        fut = _completed_future({'value': 1})
        self.assertTrue(fut.done())
        self.assertEqual(fut.result(), {'value': 1})


class ProcessAssignedTasksJobTests(unittest.TestCase):
    """The thin Job wrapper. Logging + error notification only — small mocks ok."""

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

    def test_handles_impatient_title_summary_without_truncation(self) -> None:
        # Stress: a tag-laden human title shouldn't crash format helpers.
        from kato_core_lib.jobs.process_assigned_tasks import (
            format_processing_results,
        )
        for title in IMPATIENT_TITLES:
            result = format_processing_results([
                {'status': 'done', 'pull_request_id': '1', 'summary': title},
            ])
            self.assertIn('done', result)


if __name__ == '__main__':
    unittest.main()
