"""Background watcher that drives local-comment runs browser-independently.

Polling-based, same design as ``ResumePromptWatcher`` — adds no event-
callback infrastructure to the streaming session and never competes with
the SSE consumer for items off the live event queue. Each tick runs the
EXISTING comment-dispatch primitives on the ``AgentService`` (it owns no
dispatch logic of its own):

  1. ``advance_finished_comment_runs()`` — detect comment turns whose
     RESULT has landed in the session's event buffer and mark them
     ADDRESSED/FAILED, which chains straight to the next queued comment.
  2. ``drain_all_queued_task_comments()`` — start any comment still
     QUEUED, re-trying a dispatch the busy-check declined a tick ago.

Why it exists: without an always-on server-side drain, a queued comment
advanced ONLY via a browser tab watching that task's live SSE (the
RESULT event handler) or the ticket-scan loop — which runs at
``scan_interval_seconds`` (~180s) and is disabled entirely in the
manual-only ``scan_interval_seconds<=0`` mode. So a comment whose prior
turn ended with no tab open was stranded (the operator's "the next
comment takes ages, and the last one never runs" report). This watcher
ticks the same two service methods every couple of seconds regardless of
any browser or the scan cadence.

Thread-safe: runs on its own daemon thread; it holds no mutable state of
its own — all state lives in the comment store, which is already locked.
"""
from __future__ import annotations

import threading

from kato_core_lib.helpers.logging_utils import configure_logger


# 2s tick: local comments carry no provider rate-limit concern (unlike
# the ticket scan), so we poll fast enough that the NEXT queued comment
# starts within a couple of seconds of the previous one finishing, even
# with no browser watching. The work per tick is cheap — both methods
# skip tasks with nothing in progress / nothing queued, and the comment
# store's reads are mtime-cached.
_DEFAULT_TICK_SECONDS: float = 2.0


class CommentRunWatcher(object):
    """Owns the polling thread that drains local-comment runs.

    Started once at kato boot; runs until ``stop()`` is called or the
    process exits. Safe to instantiate without starting (tests can call
    ``tick()`` directly).
    """

    def __init__(
        self,
        *,
        service,
        tick_seconds: float = _DEFAULT_TICK_SECONDS,
    ) -> None:
        self._service = service
        self._tick_seconds = max(0.5, float(tick_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.logger = configure_logger(self.__class__.__name__)

    # ----- lifecycle -----

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name='CommentRunWatcher',
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.1, float(timeout)))
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # Never let one bad tick kill the watcher — the methods
                # below touch live session + on-disk store state.
                self.logger.exception('comment-run watcher tick failed')
            self._stop_event.wait(self._tick_seconds)

    # ----- one tick (extracted for tests) -----

    def tick(self) -> int:
        """One drain pass. Returns the count of comment state changes.

        Advance first (complete finished turns → chains the next), then
        drain (start anything still queued, retrying declined dispatches).
        Each call is best-effort and isolated so one failing pass never
        strands the other or kills the watcher.
        """
        service = self._service
        if service is None:
            return 0
        changes = 0
        advance = getattr(service, 'advance_finished_comment_runs', None)
        if callable(advance):
            try:
                changes += len(advance() or [])
            except Exception:
                self.logger.exception(
                    'comment-run watcher: advance-finished pass failed',
                )
        drain = getattr(service, 'drain_all_queued_task_comments', None)
        if callable(drain):
            try:
                changes += len(drain() or [])
            except Exception:
                self.logger.exception(
                    'comment-run watcher: queued drain pass failed',
                )
        return changes


# Convenience builder so callers wire the watcher in one line.
def build_and_start_comment_run_watcher(
    *,
    service,
    tick_seconds: float = _DEFAULT_TICK_SECONDS,
    autostart: bool = True,
) -> CommentRunWatcher:
    watcher = CommentRunWatcher(service=service, tick_seconds=tick_seconds)
    if autostart:
        watcher.start()
    return watcher
