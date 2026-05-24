"""Tests for the in-memory PlanningSession registry."""

from __future__ import annotations

import threading
import unittest

from webserver.kato_webserver.session_registry import (
    PlanningSession,
    SessionRegistry,
)


class PlanningSessionTests(unittest.TestCase):

    def test_to_dict_returns_all_fields(self) -> None:
        s = PlanningSession(
            task_id='T1',
            task_summary='do work',
            status='active',
            agent_session_id='sess-1',
        )
        d = s.to_dict()
        self.assertEqual(d['task_id'], 'T1')
        self.assertEqual(d['task_summary'], 'do work')
        self.assertEqual(d['status'], 'active')
        self.assertEqual(d['agent_session_id'], 'sess-1')
        self.assertIn('created_at_epoch', d)

    def test_default_status_is_waiting(self) -> None:
        s = PlanningSession(task_id='T1', task_summary='go')
        self.assertEqual(s.status, 'waiting')

    def test_default_agent_session_id_is_empty(self) -> None:
        s = PlanningSession(task_id='T1', task_summary='go')
        self.assertEqual(s.agent_session_id, '')

    def test_created_at_epoch_auto_populates(self) -> None:
        s = PlanningSession(task_id='T1', task_summary='go')
        self.assertGreater(s.created_at_epoch, 0)


class SessionRegistryTests(unittest.TestCase):

    def setUp(self) -> None:
        self.registry = SessionRegistry()

    def test_get_session_returns_none_when_missing(self) -> None:
        self.assertIsNone(self.registry.get_session('does-not-exist'))

    def test_upsert_and_get(self) -> None:
        self.registry.upsert(PlanningSession(task_id='T1', task_summary='go'))
        d = self.registry.get_session('T1')
        self.assertIsNotNone(d)
        self.assertEqual(d['task_id'], 'T1')

    def test_upsert_replaces_existing_record(self) -> None:
        self.registry.upsert(PlanningSession(
            task_id='T1', task_summary='first', status='waiting',
        ))
        self.registry.upsert(PlanningSession(
            task_id='T1', task_summary='first', status='active',
        ))
        d = self.registry.get_session('T1')
        self.assertEqual(d['status'], 'active')

    def test_list_sessions_returns_every_record(self) -> None:
        self.registry.upsert(PlanningSession(task_id='T1', task_summary='a'))
        self.registry.upsert(PlanningSession(task_id='T2', task_summary='b'))
        self.registry.upsert(PlanningSession(task_id='T3', task_summary='c'))
        ids = sorted(s['task_id'] for s in self.registry.list_sessions())
        self.assertEqual(ids, ['T1', 'T2', 'T3'])

    def test_list_sessions_empty_by_default(self) -> None:
        self.assertEqual(self.registry.list_sessions(), [])

    def test_remove_drops_a_session(self) -> None:
        self.registry.upsert(PlanningSession(task_id='T1', task_summary='go'))
        self.registry.remove('T1')
        self.assertIsNone(self.registry.get_session('T1'))

    def test_remove_missing_is_a_no_op(self) -> None:
        # Defensive: removing a task that doesn't exist must not raise.
        self.registry.remove('never-existed')

    def test_get_session_returns_a_snapshot_dict_not_the_object(self) -> None:
        # Mutating the dict must not affect the stored record.
        self.registry.upsert(PlanningSession(task_id='T1', task_summary='go'))
        d = self.registry.get_session('T1')
        d['task_summary'] = 'mutated'
        self.assertEqual(self.registry.get_session('T1')['task_summary'], 'go')

    def test_thread_safe_concurrent_upsert(self) -> None:
        # 50 threads each upserting a unique task should produce 50
        # records with no lost writes.
        def upsert(i):
            self.registry.upsert(PlanningSession(
                task_id=f'T{i}', task_summary=f'task {i}',
            ))

        threads = [threading.Thread(target=upsert, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.registry.list_sessions()), 50)


if __name__ == '__main__':
    unittest.main()
