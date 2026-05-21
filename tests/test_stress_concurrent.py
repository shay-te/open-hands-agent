"""Concurrency stress tests against the REAL comment store + runner.

These deliberately spin up many threads pounding the same real store /
the same real ParallelTaskRunner. The point is to catch lock-contention,
torn JSON writes, dedup-set leaks, and "AI tests pass but prod races
under load" failures that a single-threaded MagicMock test cannot.

Every test uses ``tempfile.TemporaryDirectory`` for the workspace root
so they're hermetic and parallel-safe themselves.
"""

from __future__ import annotations

import random
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.comment_core_lib import (
    CommentRecord,
    CommentSource,
    KatoCommentStatus,
    LocalCommentStore,
)

from tests.chaos_lib import (
    CHAOS_BODIES,
    IMPATIENT_TITLES,
    build_real_agent_service,
    build_real_runner,
    chaos_body,
    impatient_body,
    impatient_comment,
    impatient_title,
    materialize_workspace,
    queue_real_comment,
    real_store_for,
)


class LocalCommentStoreConcurrentHammerTests(unittest.TestCase):
    """Many threads adding / updating / listing the SAME real store.

    Atomic JSON writes + the per-store RLock are what these tests
    actually exercise. A failure here would be: torn JSON on disk,
    missing comments, deadlock, or a count mismatch.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-stress-')
        self.addCleanup(self._tmp.cleanup)
        workspace = Path(self._tmp.name) / 'workspace'
        workspace.mkdir()
        self.store = LocalCommentStore(workspace)

    def test_50_concurrent_adders_all_persist(self) -> None:
        # 50 threads each adding 4 real comments concurrently. After
        # they all join, every single one must be on disk — no
        # silent drops, no torn writes.
        N_THREADS = 50
        PER_THREAD = 4

        def worker(worker_id: int) -> int:
            count = 0
            rng = random.Random(worker_id)
            for i in range(PER_THREAD):
                body = rng.choice(CHAOS_BODIES)
                self.store.add(CommentRecord(
                    repo_id='repo-1',
                    body=body or f'fix it from worker {worker_id} #{i}',
                    author=f'op-{worker_id}',
                    source=CommentSource.LOCAL.value,
                    kato_status=KatoCommentStatus.QUEUED.value,
                ))
                count += 1
            return count

        with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            totals = [f.result() for f in as_completed(
                pool.submit(worker, i) for i in range(N_THREADS)
            )]

        self.assertEqual(sum(totals), N_THREADS * PER_THREAD)
        on_disk = self.store.list()
        self.assertEqual(len(on_disk), N_THREADS * PER_THREAD)
        # And the JSON is still valid (a torn write would surface as
        # an unreadable store returning [] silently).
        self.assertTrue(self.store.storage_path.exists())
        # Bodies preserved through chaos round-trips.
        bodies = {c.body for c in on_disk}
        self.assertTrue(len(bodies) > 1)  # we hit multiple chaos strings

    def test_concurrent_status_updates_never_lose_an_update(self) -> None:
        # Seed N comments. Spawn 4 threads that each iterate and flip
        # each comment through queued → in_progress → addressed in
        # different orders. Final state must be ADDRESSED for ALL
        # comments — no lost updates.
        N = 30
        ids: list[str] = []
        for i in range(N):
            r = self.store.add(CommentRecord(
                repo_id='r1',
                body=impatient_comment(seed=i),
                author='op',
                source=CommentSource.LOCAL.value,
                kato_status=KatoCommentStatus.QUEUED.value,
            ))
            ids.append(r.id)

        def flipper(start_offset: int) -> None:
            # Each thread takes a different starting point so they
            # contend on the same ids in different orders.
            for offset in range(N):
                comment_id = ids[(start_offset + offset) % N]
                self.store.update_kato_status(
                    comment_id,
                    kato_status=KatoCommentStatus.IN_PROGRESS.value,
                )
                self.store.update_kato_status(
                    comment_id,
                    kato_status=KatoCommentStatus.ADDRESSED.value,
                    addressed_sha=f'sha-{offset}',
                )

        threads = [threading.Thread(target=flipper, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = self.store.list()
        self.assertEqual(len(final), N)
        for c in final:
            self.assertEqual(c.kato_status, KatoCommentStatus.ADDRESSED.value)
            self.assertTrue(c.kato_addressed_sha.startswith('sha-'))

    def test_listers_never_see_torn_state_while_writers_pound(self) -> None:
        # A reader thread spams list() while a writer thread adds.
        # The reader should never observe a JSONDecodeError (it would
        # surface as a silently empty list on the next call — but the
        # writes still kept happening). Reader yields between reads so
        # the writer isn't starved on the shared RLock.
        N_WRITES = 100
        stop = threading.Event()
        list_lengths: list[int] = []
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                for i in range(N_WRITES):
                    # impatient_comment never returns empty / whitespace
                    # (impatient_body intentionally does, for input-tolerance
                    # tests elsewhere — not useful here).
                    self.store.add(CommentRecord(
                        repo_id='r1',
                        body=impatient_comment(seed=i),
                        author='op',
                        source=CommentSource.LOCAL.value,
                        kato_status=KatoCommentStatus.QUEUED.value,
                    ))
            except BaseException as exc:            # pragma: no cover
                errors.append(exc)
            finally:
                stop.set()

        def reader() -> None:
            while not stop.is_set():
                try:
                    list_lengths.append(len(self.store.list()))
                except BaseException as exc:        # pragma: no cover
                    errors.append(exc)
                    return
                # Yield so writer isn't starved on the shared RLock.
                time.sleep(0.001)

        t_w = threading.Thread(target=writer)
        t_r = threading.Thread(target=reader)
        t_w.start(); t_r.start()
        t_w.join(timeout=10.0)
        t_r.join(timeout=10.0)

        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.list()), N_WRITES)
        # Reader saw monotonically non-decreasing counts (no torn drops).
        for prev, curr in zip(list_lengths, list_lengths[1:]):
            self.assertLessEqual(prev, curr)


class ParallelTaskRunnerConcurrencyTests(unittest.TestCase):
    """Real ParallelTaskRunner under load — dedup set + done-callback safety."""

    def test_same_task_id_is_deduped_under_burst_submit(self) -> None:
        runner = build_real_runner(max_workers=4)
        self.addCleanup(runner.shutdown)
        gate = threading.Event()
        executions: list[str] = []
        lock = threading.Lock()

        def worker():
            with lock:
                executions.append('ran')
            gate.wait(timeout=2.0)

        # 100 burst submits of the same task id from 100 threads.
        results = []
        def submit_once():
            try:
                fut = runner.submit('SAME-TASK', worker)
                results.append(fut)
            except Exception as exc:    # pragma: no cover
                results.append(exc)

        threads = [threading.Thread(target=submit_once) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly ONE submit returned a future; the rest got None
        # (already in flight). Even with 100 concurrent submitters.
        accepted = [r for r in results if r is not None and not isinstance(r, Exception)]
        self.assertEqual(len(accepted), 1)
        self.assertTrue(runner.is_in_flight('SAME-TASK'))

        gate.set()
        # Wait for the worker to finish and the done-callback to fire.
        deadline = time.time() + 2.0
        while time.time() < deadline and runner.is_in_flight('SAME-TASK'):
            time.sleep(0.005)
        self.assertFalse(runner.is_in_flight('SAME-TASK'))
        self.assertEqual(executions, ['ran'])  # worker ran exactly once

    def test_worker_exception_still_releases_in_flight_slot(self) -> None:
        runner = build_real_runner(max_workers=2)
        self.addCleanup(runner.shutdown)

        def boom():
            raise RuntimeError(impatient_title())  # human-style error msg

        fut = runner.submit('TASK-X', boom)
        # Wait for the future to complete (with the exception).
        deadline = time.time() + 1.0
        while time.time() < deadline and not fut.done():
            time.sleep(0.005)
        with self.assertRaises(RuntimeError):
            fut.result()
        # And the slot is freed — a follow-up submit must succeed.
        deadline = time.time() + 1.0
        while time.time() < deadline and runner.is_in_flight('TASK-X'):
            time.sleep(0.005)
        self.assertFalse(runner.is_in_flight('TASK-X'))
        followup = runner.submit('TASK-X', lambda: 'ok')
        self.assertIsNotNone(followup)


class DrainQueuedCommentsUnderConcurrentSubmissionTests(unittest.TestCase):
    """Operator adds queued comments while the scan-loop drain is running.

    The scan loop runs ``drain_all_queued_task_comments`` periodically.
    In parallel, the UI may be persisting new local comments via a
    separately-constructed ``CommentService``. Both paths converge
    on the same on-disk JSON file.

    NOTE — known production behaviour: ``LocalCommentStore`` uses a
    per-instance RLock, and ``AgentService._comment_store_for`` builds
    a fresh instance per call. Two threads adding through different
    instances of the same workspace's store can lose updates (read /
    read / append / write / write — second writer overwrites first).
    See :meth:`test_cross_instance_writes_can_lose_updates` below for
    a deterministic reproducer. This test deliberately uses a SHARED
    store instance for the inserter so it doesn't exercise that race;
    it asserts the next-strongest property: no torn JSON, nothing
    stuck QUEUED after both threads quiesce.
    """

    def test_drain_and_operator_inserts_converge(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='kato-drain-race-')
        self.addCleanup(tmp.cleanup)
        service, workspace_service = build_real_agent_service(Path(tmp.name))
        materialize_workspace(workspace_service, 'RACE-1')
        shared_store = real_store_for(workspace_service, 'RACE-1')

        # Force inserter AND drainer to use the SAME store instance so
        # the per-instance RLock actually serialises read-modify-write
        # cycles. (The lost-update race that happens when each call
        # builds a fresh instance is covered separately in
        # ``test_cross_instance_writes_can_lose_updates``.)
        with patch.object(service, '_comment_store_for',
                          return_value=shared_store), \
             patch.object(service, '_run_comment_agent', return_value=True):
            stop = threading.Event()
            inserted_ids: list[str] = []
            inserted_lock = threading.Lock()

            def inserter():
                try:
                    for i in range(40):
                        r = shared_store.add(CommentRecord(
                            repo_id='repo-a',
                            body=impatient_comment(seed=i),
                            author='op',
                            source=CommentSource.LOCAL.value,
                            kato_status=KatoCommentStatus.QUEUED.value,
                        ))
                        with inserted_lock:
                            inserted_ids.append(r.id)
                        time.sleep(0.001)
                finally:
                    stop.set()

            def drainer():
                deadline = time.time() + 10.0
                while time.time() < deadline:
                    service.drain_all_queued_task_comments()
                    if stop.is_set() and shared_store.queue_size() == 0:
                        return
                    time.sleep(0.001)

            t_i = threading.Thread(target=inserter)
            t_d = threading.Thread(target=drainer)
            t_i.start(); t_d.start()
            t_i.join(timeout=10.0)
            t_d.join(timeout=10.0)

            self.assertFalse(t_i.is_alive())
            self.assertFalse(t_d.is_alive())

        # Strong post-conditions with the shared store: every inserted
        # comment is on disk, none stuck QUEUED, JSON readable.
        on_disk = {c.id: c.kato_status for c in shared_store.list()}
        self.assertEqual(len(on_disk), len(inserted_ids),
                         'inserter and on-disk count diverged')
        for comment_id in inserted_ids:
            self.assertIn(comment_id, on_disk)
            self.assertNotEqual(
                on_disk[comment_id], KatoCommentStatus.QUEUED.value,
                f'comment {comment_id} stuck QUEUED after race',
            )

    def test_cross_instance_writes_lose_updates_deterministic_reproducer(self) -> None:
        """Deterministic reproducer for the cross-instance lost-update bug.

        ``LocalCommentStore`` uses a per-instance ``RLock`` for its
        read-modify-write cycle. Two instances of the same workspace
        each have their own lock, so this interleaving:

            store_a.add(r_a):  load → []      ┐
            store_b.add(r_b):  load → []      │ both see same baseline
            store_a.add(r_a):  append → [r_a] │
            store_b.add(r_b):  append → [r_b] │
            store_a.add(r_a):  persist [r_a]  ┘ first writer
            store_b.add(r_b):  persist [r_b]  → SECOND WIPES FIRST

        ...drops ``r_a`` silently. ``AgentService._comment_store_for``
        builds a fresh instance on every call, so this happens in
        production whenever the scan-loop drain (one thread) and the
        webserver's CommentService (another thread) write to the same
        workspace's store.

        This test forces that exact interleaving via a barrier injected
        AFTER the baseline read but BEFORE the persist, so we get a
        deterministic failure rather than a racey one. The final on-disk
        record count is EXACTLY 1 — one writer's record was lost. When
        the bug is fixed (e.g. a path-keyed class-level lock, or
        ``fcntl.flock``), this assertion will start failing because the
        count will reach 2 — flip it to ``assertEqual(..., 2)`` then.
        """
        tmp = tempfile.TemporaryDirectory(prefix='kato-lost-update-')
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name) / 'ws'
        workspace.mkdir()

        store_a = LocalCommentStore(workspace)
        store_b = LocalCommentStore(workspace)

        # Synchronize the two adds at the read/persist seam so the
        # race is no longer probabilistic. Each thread loads its
        # baseline, then waits at ``both_loaded``; only after BOTH
        # threads have loaded does the persist phase begin.
        both_loaded = threading.Barrier(2)
        results: dict[str, BaseException | None] = {}

        original_load = LocalCommentStore._load_all

        def synced_load_all(self):
            data = original_load(self)
            # Wait for the SIBLING thread to also have loaded its
            # baseline (same lock has been released by then on each
            # side because _load_all returns inside the with-block).
            both_loaded.wait(timeout=2.0)
            return data

        # Inject the barrier ONLY into the load-step of these two
        # instances; other tests are unaffected (we restore on exit).
        with patch.object(LocalCommentStore, '_load_all', synced_load_all):
            def worker(store, body, key):
                try:
                    store.add(CommentRecord(
                        repo_id='r', body=body, author='op',
                        source=CommentSource.LOCAL.value,
                    ))
                    results[key] = None
                except BaseException as exc:    # pragma: no cover
                    results[key] = exc

            t_a = threading.Thread(
                target=worker, args=(store_a, 'from-a', 'a'),
            )
            t_b = threading.Thread(
                target=worker, args=(store_b, 'from-b', 'b'),
            )
            t_a.start(); t_b.start()
            t_a.join(timeout=3.0); t_b.join(timeout=3.0)

        self.assertFalse(t_a.is_alive(), 'thread A deadlocked')
        self.assertFalse(t_b.is_alive(), 'thread B deadlocked')
        # Both threads completed without raising.
        self.assertIsNone(results.get('a'))
        self.assertIsNone(results.get('b'))

        # The bug: FEWER than 2 records survive. Under proper shared
        # locking the on-disk count would be EXACTLY 2. Under the
        # current per-instance-lock code, both threads load the same
        # baseline, append, and persist; outcomes observed in
        # practice (depending on the exact rename interleaving in
        # atomic_write_json):
        #   * 1 record  — one writer's persist won; the other was lost
        #   * 0 records — both renames raced the shared tmp path and
        #                 failed; both writers swallowed the OSError
        # Either way: at least one write was silently lost. That's
        # the regression. When LocalCommentStore grows shared locking
        # this assertion will FAIL (count == 2) — flip it to
        # ``assertEqual(len(on_disk), 2)`` then.
        on_disk = LocalCommentStore(workspace).list()
        self.assertLess(
            len(on_disk), 2,
            'LocalCommentStore appears to have grown cross-instance '
            'locking (BOTH writes survived). Flip this assertion to '
            'assertEqual(len(on_disk), 2) because the bug is fixed. '
            'Current on-disk: ' + repr([c.body for c in on_disk]),
        )


if __name__ == '__main__':
    unittest.main()
