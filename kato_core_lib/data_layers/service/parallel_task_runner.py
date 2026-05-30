"""Concurrency primitives for running multiple kato tasks in parallel.

Phase 3 of the workspace-mode rollout. The kato scan loop is still
single-threaded — it pulls assigned tasks from the ticket system one at
a time — but instead of running each one inline, it now hands them off
to a ``ThreadPoolExecutor`` sized by :envvar:`KATO_MAX_PARALLEL_TASKS`.

Why this is safe:

* Each task gets its own workspace folder + clone-set, so two parallel
  workers never share git state.
* Each task gets its own Claude subprocess (or OpenHands conversation),
  so the agent backends never share session state.
* The orchestrator's services (TaskService, RepositoryService, …) are
  HTTP / git wrappers — every method is stateless or guarded by its
  own lock. Concurrent calls are fine.

What we own here:

* The worker pool itself.
* An in-memory "in flight" set so a fast scan loop can't double-submit
  the same task while the previous run is still executing.
* Shutdown plumbing so kato drains gracefully.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from kato_core_lib.helpers.logging_utils import configure_logger


class ParallelTaskRunner(object):
    """Thread-pooled task dispatcher with at-most-once-per-task semantics.

    Construct once per kato process; the pool lives for the lifetime of
    the orchestrator. Submitting the same ``task_id`` while a previous
    run is still in flight returns ``None`` (caller should skip);
    completion frees the slot for the next scan.
    """

    def __init__(self, *, max_workers: int) -> None:
        self._max_workers = max(1, int(max_workers or 1))
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix='kato-task-worker',
        )
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def submit(
        self,
        task_id: str,
        callable_: Callable[[], Any],
    ) -> Future | None:
        """Submit ``callable_`` for parallel execution under ``task_id``.

        Returns the underlying ``Future`` for callers that want to wait,
        or ``None`` when ``task_id`` is already running. The runner
        guarantees the in-flight set is cleared in a ``done_callback``
        even on exception, so a worker crash never permanently locks
        the slot.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            raise ValueError('task_id is required')
        with self._lock:
            if normalized in self._in_flight:
                self.logger.debug(
                    'skip submit: task %s already in flight', normalized,
                )
                return None
            self._in_flight.add(normalized)
        future = self._executor.submit(callable_)
        future.add_done_callback(lambda _f: self._release(normalized))
        return future

    def is_in_flight(self, task_id: str) -> bool:
        normalized = str(task_id or '').strip()
        if not normalized:
            return False
        with self._lock:
            return normalized in self._in_flight

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting new submissions and (optionally) drain the pool."""
        self._executor.shutdown(wait=wait, cancel_futures=False)

    # ----- internals -----

    def _release(self, task_id: str) -> None:
        with self._lock:
            self._in_flight.discard(task_id)
