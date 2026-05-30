"""Unit tests for kato.data_layers.service.parallel_task_runner."""

from __future__ import annotations

import threading
import time
import unittest

from kato_core_lib.data_layers.service.parallel_task_runner import ParallelTaskRunner


class ParallelTaskRunnerTests(unittest.TestCase):
    def test_max_workers_clamped_to_one(self) -> None:
        runner = ParallelTaskRunner(max_workers=0)
        self.assertEqual(runner.max_workers, 1)
        runner.shutdown()

    def test_submit_runs_callable(self) -> None:
        runner = ParallelTaskRunner(max_workers=2)
        self.addCleanup(runner.shutdown)
        future = runner.submit('PROJ-1', lambda: 42)
        self.assertIsNotNone(future)
        assert future is not None
        self.assertEqual(future.result(timeout=2), 42)

    def test_submit_blocks_duplicate_in_flight(self) -> None:
        # First submission grabs the slot and won't release until we
        # signal it to. Second submission for the same task id is
        # rejected outright.
        runner = ParallelTaskRunner(max_workers=2)
        self.addCleanup(runner.shutdown)
        gate = threading.Event()

        first = runner.submit('PROJ-1', gate.wait)
        self.assertIsNotNone(first)

        # Same task id while first is still running.
        second = runner.submit('PROJ-1', lambda: 'should-not-run')
        self.assertIsNone(second)
        self.assertTrue(runner.is_in_flight('PROJ-1'))

        gate.set()
        assert first is not None
        first.result(timeout=2)
        # In-flight set clears once the future's done callback runs.
        # Tiny sleep to let the callback fire on the executor's thread.
        for _ in range(20):
            if not runner.is_in_flight('PROJ-1'):
                break
            time.sleep(0.05)
        self.assertFalse(runner.is_in_flight('PROJ-1'))

    def test_failing_task_releases_slot(self) -> None:
        runner = ParallelTaskRunner(max_workers=2)
        self.addCleanup(runner.shutdown)

        def boom() -> None:
            raise RuntimeError('boom')

        future = runner.submit('PROJ-1', boom)
        assert future is not None
        with self.assertRaises(RuntimeError):
            future.result(timeout=2)
        # Slot must clear even though the worker raised.
        for _ in range(20):
            if not runner.is_in_flight('PROJ-1'):
                break
            time.sleep(0.05)
        self.assertFalse(runner.is_in_flight('PROJ-1'))

    def test_two_distinct_task_ids_run_concurrently(self) -> None:
        runner = ParallelTaskRunner(max_workers=2)
        self.addCleanup(runner.shutdown)
        # Both workers must be alive at the same time for both events
        # to be set before either future returns.
        a_running = threading.Event()
        b_running = threading.Event()
        release = threading.Event()

        def worker(running: threading.Event) -> None:
            running.set()
            release.wait(timeout=2)

        f_a = runner.submit('PROJ-1', lambda: worker(a_running))
        f_b = runner.submit('PROJ-2', lambda: worker(b_running))
        self.assertTrue(a_running.wait(timeout=2))
        self.assertTrue(b_running.wait(timeout=2))
        release.set()
        assert f_a is not None and f_b is not None
        f_a.result(timeout=2)
        f_b.result(timeout=2)

    def test_submit_rejects_empty_task_id(self) -> None:
        runner = ParallelTaskRunner(max_workers=1)
        self.addCleanup(runner.shutdown)
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            runner.submit('', lambda: None)


if __name__ == '__main__':
    unittest.main()
