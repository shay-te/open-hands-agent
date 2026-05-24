"""Adversarial regression test for session/manager.py bug:
``adopt_session_id`` writes the adopted id into the per-task record
but does NOT touch the live ``_sessions[task_id]``. The docstring
says the caller is expected to terminate the live session first, but
the function doesn't enforce or warn — it silently writes the
record and the next ``start_session`` reuses the still-alive
subprocess (lines 268-270), completely bypassing the adopted id.

Operator-visible consequence:
    1. Operator runs adoption via UI; modal says "✓ adopted".
    2. Operator sends a follow-up message.
    3. kato sees the existing subprocess is alive, returns it as-is.
    4. Claude continues the OLD conversation, ignoring the adoption.
    5. Operator's intent (continue from external session) is silently
       dropped.

The contract: adoption must either
   (a) refuse with a clear error when a live session exists, OR
   (b) terminate the live session as part of adoption.
This test pins option (a) since it gives the operator clearer control,
and forces the caller to acknowledge the lifecycle change.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace


def _make_live_stub_session():
    """A stub StreamingClaudeSession that reports alive."""
    class _Stub:
        def __init__(self, **kwargs):
            self._task_id = kwargs.get('task_id', '')
            self._kwargs = kwargs

        @property
        def task_id(self): return self._task_id

        @property
        def is_alive(self): return True  # <-- live

        @property
        def is_working(self): return False

        @property
        def cwd(self): return self._kwargs.get('cwd', '')

        @property
        def agent_session_id(self):
            return self._kwargs.get('resume_session_id', '') or 'live-id-from-spawn'

        def start(self, *, initial_prompt=''): pass

        def send_user_message(self, *args, **kwargs): pass

        def poll_event(self, *args, **kwargs): return None

        @property
        def terminal_event(self): return None

        def terminate(self): pass

        def recent_events(self): return []

        def events_after(self, _index): return [], 0

    return _Stub


class BugAdoptWhileSessionAliveTests(unittest.TestCase):

    def test_adopt_while_live_session_running_does_not_silently_drop_intent(self) -> None:
        # Setup: kato has a LIVE session for task T1 with id 'live-id-from-spawn'.
        # Operator calls adopt_session_id with id 'external-id-from-vscode'.
        # The operator's intent is to continue the conversation from the
        # external session on the NEXT spawn.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            stub_factory = _make_live_stub_session()
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=stub_factory,
            )
            # Spawn so there's a live session for T1.
            mgr.start_session(
                task_id='T1', initial_prompt='go', cwd='/tmp/T1',
            )
            self.assertIsNotNone(mgr.get_session('T1'))

            # Try to adopt while the live session is running. The
            # function MUST either refuse OR terminate the live
            # subprocess before writing the adopted id.
            try:
                mgr.adopt_session_id(
                    'T1', agent_session_id='external-id-from-vscode',
                )
            except (RuntimeError, ValueError) as exc:
                # Contract option (a): refuse adoption explicitly so the
                # caller can decide whether to terminate first. This is
                # an acceptable outcome.
                msg = str(exc).lower()
                self.assertTrue(
                    'live' in msg or 'running' in msg or 'terminate' in msg,
                    f'adoption refused but the error message did not name '
                    f'the cause: {exc!r}. Operator must be able to '
                    f'diagnose this from the message alone.',
                )
                return  # contract honored

            # Contract option (b): adoption succeeded — the live session
            # must have been terminated, so the next start_session
            # spawns fresh and resumes the adopted id.
            live = mgr.get_session('T1')
            if live is not None and live.is_alive:
                self.fail(
                    'adopt_session_id silently wrote the adopted id but '
                    'left the OLD live session running. Next start_session '
                    'will reuse the live session (one-per-task invariant), '
                    'silently dropping the adoption — operator\'s intent '
                    'is lost.',
                )


if __name__ == '__main__':
    unittest.main()
