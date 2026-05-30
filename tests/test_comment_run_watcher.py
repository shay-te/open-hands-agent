"""Tests for the always-on, browser-independent comment-run drain.

The watcher owns no dispatch logic — each tick just runs the
AgentService's existing ``advance_finished_comment_runs`` (complete
finished turns → chain the next) and ``drain_all_queued_task_comments``
(start anything still queued). These pin: it ticks both, counts their
changes, is resilient to one method failing, tolerates a missing/None
service, and starts/stops cleanly.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.comment_run_watcher import (
    CommentRunWatcher,
    build_and_start_comment_run_watcher,
)


def _service(advance=None, drain=None) -> MagicMock:
    svc = MagicMock()
    svc.advance_finished_comment_runs.return_value = advance or []
    svc.drain_all_queued_task_comments.return_value = drain or []
    return svc


class CommentRunWatcherTickTests(unittest.TestCase):
    def test_tick_runs_advance_then_drain_and_counts_changes(self) -> None:
        svc = _service(advance=[{'a': 1}], drain=[{'b': 1}, {'b': 2}])
        changes = CommentRunWatcher(service=svc).tick()
        self.assertEqual(changes, 3)
        svc.advance_finished_comment_runs.assert_called_once_with()
        svc.drain_all_queued_task_comments.assert_called_once_with()

    def test_tick_is_a_noop_with_no_service(self) -> None:
        self.assertEqual(CommentRunWatcher(service=None).tick(), 0)

    def test_advance_failure_does_not_stop_the_drain(self) -> None:
        # Isolation: an advance pass that raises is swallowed and the
        # queued drain still runs — one failing pass never strands the
        # other.
        svc = _service(drain=[{'b': 1}])
        svc.advance_finished_comment_runs.side_effect = RuntimeError('boom')
        watcher = CommentRunWatcher(service=svc)
        self.assertEqual(watcher.tick(), 1)
        svc.drain_all_queued_task_comments.assert_called_once_with()

    def test_drain_failure_is_swallowed(self) -> None:
        svc = _service(advance=[{'a': 1}])
        svc.drain_all_queued_task_comments.side_effect = RuntimeError('boom')
        self.assertEqual(CommentRunWatcher(service=svc).tick(), 1)

    def test_tick_tolerates_a_service_missing_the_methods(self) -> None:
        self.assertEqual(CommentRunWatcher(service=object()).tick(), 0)


class CommentRunWatcherLifecycleTests(unittest.TestCase):
    def test_start_runs_ticks_then_stop_halts_them(self) -> None:
        svc = _service()
        watcher = CommentRunWatcher(service=svc, tick_seconds=0.5)
        watcher.start()
        self.addCleanup(watcher.stop)
        deadline = time.monotonic() + 2.0
        while (
            time.monotonic() < deadline
            and not svc.advance_finished_comment_runs.called
        ):
            time.sleep(0.02)
        self.assertTrue(svc.advance_finished_comment_runs.called)

        watcher.stop()
        calls_after_stop = svc.advance_finished_comment_runs.call_count
        time.sleep(0.3)
        self.assertEqual(
            svc.advance_finished_comment_runs.call_count, calls_after_stop,
            'watcher kept ticking after stop()',
        )

    def test_build_and_start_autostarts_and_is_stoppable(self) -> None:
        watcher = build_and_start_comment_run_watcher(
            service=_service(), tick_seconds=0.5,
        )
        self.addCleanup(watcher.stop)
        self.assertIsNotNone(watcher._thread)
        self.assertTrue(watcher._thread.is_alive())
        watcher.stop()
        self.assertIsNone(watcher._thread)

    def test_double_start_is_idempotent(self) -> None:
        watcher = CommentRunWatcher(service=_service(), tick_seconds=0.5)
        watcher.start()
        self.addCleanup(watcher.stop)
        first = watcher._thread
        watcher.start()
        self.assertIs(watcher._thread, first)


if __name__ == '__main__':
    unittest.main()
