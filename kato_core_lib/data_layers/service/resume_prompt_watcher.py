"""Background watcher that refreshes ``resume_prompt.md`` after each Claude turn.

Polling-based by design — adds no event-callback infrastructure to
``claude_core_lib.session.streaming`` and never competes with the
SSE consumer for items off the live event queue. Each tick:

  1. Walk every session the manager owns.
  2. Snapshot ``session.recent_events()``.
  3. Compare ``len(events)`` (and the position of the newest
     ``result`` event) to the last-seen value.
  4. If a new turn ended, render a fresh ``resume_prompt.md``
     snapshot and atomic-write it at the task's workspace root.

5-second tick is "after each turn" in practice — Claude turns end
at most a few per minute, so the operator sees the file fresh
within seconds of a turn finishing.

Thread-safe: the watcher runs on its own daemon thread; the dict of
last-seen state is only mutated from that thread.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.resume_prompt_writer import (
    build_inputs_from_session,
    render_resume_prompt,
    write_resume_prompt,
)


# How often to poll live sessions. 5s gives "feels live" UX for the
# operator who refreshes the file in Cursor, without burning CPU on
# tasks where nothing is happening. The work per tick is cheap (a
# few list snapshots + at most one file write per task that had a
# new turn).
_DEFAULT_TICK_SECONDS: float = 5.0


class ResumePromptWatcher(object):
    """Owns the polling thread + per-task last-seen state.

    Started once at kato boot; runs until ``stop()`` is called or
    the process exits. Safe to instantiate without starting (tests
    can call ``tick()`` directly).
    """

    def __init__(
        self,
        *,
        session_manager,
        workspace_manager=None,
        tick_seconds: float = _DEFAULT_TICK_SECONDS,
    ) -> None:
        self._session_manager = session_manager
        self._workspace_manager = workspace_manager
        self._tick_seconds = max(0.5, float(tick_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Per-task state: ``{lookup_key: (event_count, last_result_index)}``
        # so a turn that produced no new result event (e.g. just user
        # echoes) doesn't trigger a redundant rewrite.
        self._seen: dict[str, tuple[int, int]] = {}
        self.logger = configure_logger(self.__class__.__name__)

    # ----- lifecycle -----

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name='ResumePromptWatcher',
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
                # Never let one bad tick kill the watcher — the snapshot
                # logic touches live state from many threads.
                self.logger.exception('resume_prompt watcher tick failed')
            self._stop_event.wait(self._tick_seconds)

    # ----- one tick (extracted for tests) -----

    def tick(self) -> int:
        """Walk every known session once. Returns number of files written."""
        sessions = self._list_sessions()
        records_by_task = self._records_by_task()
        written = 0
        for task_id, session in sessions:
            if session is None:
                continue
            try:
                events = list(session.recent_events() or [])
            except Exception:
                self.logger.exception(
                    'resume_prompt: failed to snapshot events for %s',
                    task_id,
                )
                continue
            last_result_index = _index_of_last_result(events)
            seen_key = self._lookup_key(task_id)
            prev = self._seen.get(seen_key)
            current = (len(events), last_result_index)
            if prev == current:
                continue
            # Only WRITE on a fresh turn-end. Tick state advances even
            # without a write so we don't busy-poll a stale session.
            if last_result_index < 0 or (
                prev is not None and last_result_index == prev[1]
            ):
                self._seen[seen_key] = current
                continue
            workspace_path = self._workspace_path_for(task_id)
            if not workspace_path:
                self._seen[seen_key] = current
                continue
            record = records_by_task.get(seen_key)
            inputs = build_inputs_from_session(
                task_id=task_id,
                task_summary=(
                    getattr(record, 'task_summary', '') if record else ''
                ),
                branch_name=(
                    getattr(record, 'expected_branch', '') if record else ''
                ),
                workspace_path=str(workspace_path),
                repository_paths=self._repository_paths(task_id),
                recent_events=events,
                claude_session_id=(
                    getattr(record, 'claude_session_id', '') if record else ''
                ),
            )
            content = render_resume_prompt(inputs)
            if write_resume_prompt(workspace_path, content, logger=self.logger):
                written += 1
            self._seen[seen_key] = current
        return written

    # ----- session manager / workspace manager adapters -----
    # All defensive: any one of these may be None or missing fields
    # in tests / embedded use; the watcher must degrade silently.

    def _list_sessions(self) -> list[tuple[str, object]]:
        manager = self._session_manager
        if manager is None:
            return []
        list_records = getattr(manager, 'list_records', None)
        get_session = getattr(manager, 'get_session', None)
        if not callable(list_records) or not callable(get_session):
            return []
        try:
            records = list(list_records() or [])
        except Exception:
            return []
        out: list[tuple[str, object]] = []
        for record in records:
            task_id = str(getattr(record, 'task_id', '') or '')
            if not task_id:
                continue
            try:
                session = get_session(task_id)
            except Exception:
                session = None
            out.append((task_id, session))
        return out

    def _records_by_task(self) -> dict[str, object]:
        manager = self._session_manager
        if manager is None:
            return {}
        list_records = getattr(manager, 'list_records', None)
        if not callable(list_records):
            return {}
        try:
            records = list(list_records() or [])
        except Exception:
            return {}
        out: dict[str, object] = {}
        for record in records:
            task_id = str(getattr(record, 'task_id', '') or '')
            if task_id:
                out[self._lookup_key(task_id)] = record
        return out

    def _workspace_path_for(self, task_id: str):
        wm = self._workspace_manager
        if wm is None:
            return None
        get_path = getattr(wm, 'workspace_path', None)
        if not callable(get_path):
            return None
        try:
            return get_path(task_id)
        except Exception:
            return None

    def _repository_paths(self, task_id: str) -> list[str]:
        wm = self._workspace_manager
        if wm is None:
            return []
        get_workspace = getattr(wm, 'get', None)
        repo_path = getattr(wm, 'repository_path', None)
        if not callable(get_workspace) or not callable(repo_path):
            return []
        try:
            workspace = get_workspace(task_id)
        except Exception:
            return []
        if workspace is None:
            return []
        repo_ids = list(getattr(workspace, 'repository_ids', []) or [])
        paths: list[str] = []
        for rid in repo_ids:
            try:
                paths.append(str(repo_path(task_id, str(rid))))
            except Exception:
                continue
        return paths

    @staticmethod
    def _lookup_key(task_id: str) -> str:
        return str(task_id or '').strip().lower()


def _index_of_last_result(events: list) -> int:
    """Return the index of the newest ``result`` event, or -1 if none."""
    for i in range(len(events) - 1, -1, -1):
        if getattr(events[i], 'event_type', '') == 'result':
            return i
    return -1


# Convenience builder so callers wire the watcher in one line:
#   watcher = build_and_start_resume_prompt_watcher(app)
def build_and_start_resume_prompt_watcher(
    *,
    session_manager,
    workspace_manager=None,
    tick_seconds: float = _DEFAULT_TICK_SECONDS,
    autostart: bool = True,
) -> ResumePromptWatcher:
    watcher = ResumePromptWatcher(
        session_manager=session_manager,
        workspace_manager=workspace_manager,
        tick_seconds=tick_seconds,
    )
    if autostart:
        watcher.start()
    return watcher
