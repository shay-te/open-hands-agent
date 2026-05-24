"""Unit tests for ``resolve_agent_session_id``.

The function reads ``agent_session_id`` off a session manager record
(set by ``ClaudeSessionManager``) and falls back to the workspace's
``agent_session_id`` field. Lives in this lib because the downstream
JSONL replay it feeds is Claude-specific.

NO MagicMock — every stand-in is a concrete class implementing the
duck-typed surface the resolver consumes.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from claude_core_lib.claude_core_lib.session.history import (
    resolve_agent_session_id,
)


class ResolveAgentSessionIdTests(unittest.TestCase):

    def test_returns_empty_when_both_managers_are_none(self) -> None:
        self.assertEqual(resolve_agent_session_id(None, None, 'T1'), '')

    def test_prefers_session_managers_record_when_present(self) -> None:
        class _Mgr:
            def get_record(self, task_id):
                return SimpleNamespace(
                    task_id=task_id, agent_session_id='from-manager',
                )

        self.assertEqual(
            resolve_agent_session_id(_Mgr(), None, 'T1'),
            'from-manager',
        )

    def test_falls_back_to_workspace_manager_agent_session_id(self) -> None:
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='from-workspace')

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _Ws(), 'T1'),
            'from-workspace',
        )

    def test_workspace_agent_session_id_used_when_record_missing(self) -> None:
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='from-ws')

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _Ws(), 'T1'),
            'from-ws',
        )

    def test_swallows_session_manager_exception_and_falls_through(self) -> None:
        class _BoomSessionMgr:
            def get_record(self, task_id):
                raise RuntimeError('session-mgr exploded')

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='still-resolved')

        self.assertEqual(
            resolve_agent_session_id(_BoomSessionMgr(), _Ws(), 'T1'),
            'still-resolved',
        )

    def test_swallows_workspace_manager_exception_and_returns_empty(self) -> None:
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _BoomWs:
            def get(self, task_id):
                raise RuntimeError('workspace-mgr exploded')

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _BoomWs(), 'T1'),
            '',
        )

    def test_empty_session_id_field_falls_through_to_workspace(self) -> None:
        """``agent_session_id=''`` on the record is treated as "not set"
        so the resolver tries the workspace fallback instead."""
        class _Mgr:
            def get_record(self, task_id):
                return SimpleNamespace(agent_session_id='')

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='from-workspace')

        self.assertEqual(
            resolve_agent_session_id(_Mgr(), _Ws(), 'T1'),
            'from-workspace',
        )

    def test_whitespace_session_id_field_falls_through_to_workspace(self) -> None:
        class _Mgr:
            def get_record(self, task_id):
                return SimpleNamespace(agent_session_id='   ')

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='from-workspace')

        self.assertEqual(
            resolve_agent_session_id(_Mgr(), _Ws(), 'T1'),
            'from-workspace',
        )

    def test_strips_workspace_session_id_before_returning(self) -> None:
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='  from-workspace\n')

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _Ws(), 'T1'),
            'from-workspace',
        )

    def test_reads_legacy_claude_session_id_from_workspace(self) -> None:
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(claude_session_id=' legacy-id\n')

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _Ws(), 'T1'),
            'legacy-id',
        )

    def test_missing_workspace_yields_empty_string(self) -> None:
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _Ws:
            def get(self, task_id):
                return None

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _Ws(), 'T1'),
            '',
        )

    def test_workspace_with_empty_agent_session_id_returns_empty(self) -> None:
        """Workspace exists but ``agent_session_id`` is empty → empty string."""
        class _SessionMgr:
            def get_record(self, task_id):
                return None

        class _Ws:
            def get(self, task_id):
                return SimpleNamespace(agent_session_id='')

        self.assertEqual(
            resolve_agent_session_id(_SessionMgr(), _Ws(), 'T1'),
            '',
        )


if __name__ == '__main__':
    unittest.main()
