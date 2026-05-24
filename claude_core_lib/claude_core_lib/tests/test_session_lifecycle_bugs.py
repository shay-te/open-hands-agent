"""Regression locks for the three session-lifecycle bugs the operator reported.

These tests live alongside the broader claude_core_lib suite but in a
named-bug file so a future regression surfaces with a self-explanatory
failure name. The kato-side mirror lives at
``tests/test_session_lifecycle_bugs.py``.

Bug 1 (history lost on kato restart):
    The operator reported that after restarting kato the chat tab said
    "reattached" but showed no scroll-back history. The history IS
    persisted on disk by Claude Code (one JSONL per session under
    ``~/.claude/projects/<encoded-cwd>/<id>.jsonl``); kato's job is to
    read it back and replay it into the UI.

    Lock that ``load_history_events`` correctly extracts user + assistant
    turns from a real-shape JSONL transcript, in order, with no
    duplication and no loss.

Bug 2 (every follow-up message spawns a session that re-does work):
    The operator reported that each new message after a finished turn
    triggered Claude to re-explore the workspace from scratch. Root
    cause split across both libs: claude_core_lib must pass
    ``--resume <id>`` to the CLI on respawn so Claude reads the prior
    JSONL; kato_core_lib's planning runner must not double-wrap the
    user message in workspace context (that's the kato-side test).

    Lock that ``StreamingClaudeSession`` puts ``--resume <id>`` in the
    spawn argv when ``resume_session_id`` is set AND that the live
    ``claude_session_id`` is adopted synchronously (so the next respawn
    finds the same JSONL).

Bug 3 (draft input lost on tab switch):
    Pure UI concern (React component unmount drops state). No claude_core_lib
    behaviour is involved; the JS-side tests live at
    ``webserver/ui/src/utils/composerDraft.test.js``. This file does
    NOT carry a Bug 3 test — the layer doesn't have the surface to test.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# --------------------------------------------------------------------------
# Bug 1: history replay from disk
# --------------------------------------------------------------------------


class Bug1HistoryReplayFromDiskTests(unittest.TestCase):
    """``load_history_events`` is the on-restart history pipe.

    When the operator opens a chat tab after kato restart, the webserver
    SSE endpoint calls ``load_history_events(claude_session_id)`` and
    pushes every returned event to the browser as a
    ``session_history_event``. That's what populates the scroll-back.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def _write_session(
        self, session_id: str, cwd: str, events: list[dict],
    ) -> Path:
        """Write a fake Claude Code JSONL transcript and return its path."""
        # Claude's per-project folder uses the cwd with /._ replaced by -.
        encoded = cwd.lstrip('/').replace('/', '-').replace('.', '-')
        encoded = '-' + encoded if encoded else 'home'
        project_dir = self.projects_root / encoded
        project_dir.mkdir(parents=True, exist_ok=True)
        target = project_dir / f'{session_id}.jsonl'
        with target.open('w', encoding='utf-8') as fh:
            for event in events:
                fh.write(json.dumps(event) + '\n')
        return target

    def test_returns_user_and_assistant_turns_in_order(self) -> None:
        # The load-bearing case: a multi-turn conversation persisted by
        # Claude Code, read back via ``load_history_events`` and
        # delivered to the UI in chronological order.
        from claude_core_lib.claude_core_lib.session.history import (
            load_history_events,
        )

        session_id = 'sess-abc-123'
        cwd = '/workspaces/PROJ-1/repo-a'
        events = [
            {
                'type': 'user',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': 'please fix the login bug'},
                    ],
                },
            },
            {
                'type': 'assistant',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'assistant',
                    'content': [
                        {'type': 'text', 'text': 'looking at auth flow now'},
                    ],
                },
            },
            {
                'type': 'user',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': 'also add a test'},
                    ],
                },
            },
            {
                'type': 'assistant',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'assistant',
                    'content': [
                        {'type': 'text', 'text': 'added LoginTests.test_invalid_pw'},
                    ],
                },
            },
        ]
        self._write_session(session_id, cwd, events)
        result = load_history_events(
            session_id, projects_root=self.projects_root,
        )
        # All four turns surfaced.
        self.assertEqual(len(result), 4)
        # In the same order they were written.
        self.assertEqual(
            [e['type'] for e in result],
            ['user', 'assistant', 'user', 'assistant'],
        )
        # First user turn's text round-trips intact.
        self.assertIn(
            'please fix the login bug',
            result[0]['message']['content'][0]['text'],
        )

    def test_returns_empty_when_session_id_unknown(self) -> None:
        # If the JSONL is missing (e.g. ``~/.claude`` wiped, or the
        # session was never persisted), the replay returns ``[]`` so
        # the UI falls through to the idle / empty-chat state without
        # crashing the SSE stream.
        from claude_core_lib.claude_core_lib.session.history import (
            load_history_events,
        )
        result = load_history_events(
            'nonexistent-id', projects_root=self.projects_root,
        )
        self.assertEqual(result, [])

    def test_replays_orchestration_user_prompts(self) -> None:
        # Kato's autonomous-flow user prompts must still be visible
        # after restart so the operator can see what Claude was asked.
        from claude_core_lib.claude_core_lib.session.history import (
            load_history_events,
        )
        session_id = 'sess-filtered'
        cwd = '/workspaces/PROJ-1/repo-a'
        events = [
            {
                'type': 'user',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'user',
                    'content': [{
                        'type': 'text',
                        'text': 'Security guardrails:\n- never read ~/.ssh',
                    }],
                },
            },
            {
                'type': 'user',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'user',
                    'content': [{
                        'type': 'text', 'text': 'real operator message',
                    }],
                },
            },
            {
                'type': 'assistant',
                'sessionId': session_id, 'cwd': cwd,
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'on it'}],
                },
            },
        ]
        self._write_session(session_id, cwd, events)
        result = load_history_events(
            session_id, projects_root=self.projects_root,
        )
        # Both the orchestration prompt and the real exchange survive.
        texts = [
            ' '.join(
                block.get('text', '')
                for block in (e.get('message', {}).get('content') or [])
                if isinstance(block, dict)
            )
            for e in result
        ]
        self.assertTrue(
            any('Security guardrails' in t for t in texts),
            f'orchestration prompt missing from history: {texts!r}',
        )
        self.assertTrue(any('real operator message' in t for t in texts))
        self.assertTrue(any('on it' in t for t in texts))


# --------------------------------------------------------------------------
# Bug 2: --resume <id> on respawn keeps the same JSONL
# --------------------------------------------------------------------------


class Bug2ResumePassesSameSessionIdTests(unittest.TestCase):
    """``StreamingClaudeSession`` + ``ClaudeSessionManager`` together
    must guarantee that when the operator sends a follow-up message to
    an exited subprocess, the next spawn:

    1. passes ``--resume <persisted_id>`` to the ``claude`` CLI, AND
    2. advertises that same id on ``session.claude_session_id`` BEFORE
       the first stream-json event arrives.

    Without (1), Claude starts a brand-new conversation and the JSONL
    chain breaks. Without (2), kato's webserver SSE handler can't
    look up the history (it resolves the id from the live session
    record). Both together are what makes follow-up turns continue
    seamlessly instead of re-exploring the workspace.
    """

    def _stub_session_class(self, recorded):
        """Build a stub ``StreamingClaudeSession`` class that records
        the kwargs it was constructed with and exposes the same
        ``claude_session_id`` / ``cwd`` / ``is_alive`` surface the
        real session does. The session manager treats this as the
        real factory via the ``session_factory=`` constructor arg."""

        class _StubSession:
            def __init__(self, **kwargs):
                # Capture the resume id for later assertions.
                recorded['kwargs'] = kwargs
                self._kwargs = kwargs
                self._task_id = kwargs.get('task_id', '')
                self._cwd = kwargs.get('cwd', '')
                # Mirror the real synchronous-adopt behaviour:
                # ``--resume <id>`` adopts the id BEFORE the first
                # stream event arrives. The real implementation does
                # this in ``_build_command``.
                self._claude_session_id = kwargs.get('resume_session_id', '') or 'fresh-id'
                self._alive = True

            @property
            def task_id(self): return self._task_id

            @property
            def cwd(self): return self._cwd

            @property
            def claude_session_id(self): return self._claude_session_id

            @property
            def is_alive(self): return self._alive

            @property
            def is_working(self): return False

            def start(self, *, initial_prompt=''):
                recorded['initial_prompt'] = initial_prompt

            def send_user_message(self, *args, **kwargs):
                recorded['send_user_message'] = (args, kwargs)

            def poll_event(self, *args, **kwargs):
                return None

            @property
            def terminal_event(self):
                return None

            def terminate(self):
                self._alive = False

            def recent_events(self):
                return []

            def events_after(self, _index):
                return [], 0

        return _StubSession

    def test_respawn_passes_resume_session_id_when_record_exists(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as td:
            recorded = {}
            stub = self._stub_session_class(recorded)
            manager = ClaudeSessionManager(
                state_dir=td, session_factory=stub,
            )
            # First spawn: nothing on disk → Claude assigns its own id.
            manager.start_session(
                task_id='T1',
                task_summary='do work',
                initial_prompt='go',
                cwd='/tmp/T1',
            )
            first_kwargs = recorded['kwargs']
            self.assertEqual(first_kwargs.get('resume_session_id', ''), '')

            # Terminate so the next start_session goes through respawn
            # rather than reusing the existing live session.
            manager.terminate_session('T1')

            persisted_id = manager.get_record('T1').claude_session_id
            self.assertEqual(persisted_id, 'fresh-id')

            # Second spawn: kato now has a record with a persisted id.
            recorded.clear()
            manager.start_session(
                task_id='T1',
                task_summary='do work',
                initial_prompt='follow-up message',
                cwd='/tmp/T1',
            )
            second_kwargs = recorded['kwargs']
            # --resume <id> threaded through to the streaming session.
            self.assertEqual(
                second_kwargs.get('resume_session_id'),
                persisted_id,
            )

    def test_streaming_session_command_includes_resume_flag(self) -> None:
        # Direct verification on ``StreamingClaudeSession._build_command``:
        # when ``resume_session_id`` is non-empty, ``--resume <id>`` is
        # in the spawn argv. Locks the actual CLI invocation against a
        # regression that drops the flag.
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )
        with tempfile.TemporaryDirectory() as td:
            session = StreamingClaudeSession(
                task_id='T1',
                cwd=td,
                binary='claude',
                resume_session_id='persisted-id-xyz',
            )
            command = session._build_command()
        self.assertIn('--resume', command)
        idx = command.index('--resume')
        self.assertEqual(command[idx + 1], 'persisted-id-xyz')

    def test_streaming_session_command_omits_resume_flag_when_blank(self) -> None:
        # Symmetric guarantee: on a true first spawn (no persisted id),
        # ``--resume`` is NOT in argv — Claude assigns a fresh id and
        # starts a clean JSONL.
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )
        with tempfile.TemporaryDirectory() as td:
            session = StreamingClaudeSession(
                task_id='T1',
                cwd=td,
                binary='claude',
                resume_session_id='',
            )
            command = session._build_command()
        self.assertNotIn('--resume', command)

    def test_mid_work_continuity_second_spawn_reuses_first_spawn_id(self) -> None:
        # The operator's complaint: "every follow-up message creates a
        # new session and re-explores the workspace." That happens iff
        # the second spawn drops the first spawn's session id.
        #
        # Real flow: first spawn auto-generates a uuid inside
        # ``_build_command``; the manager's ``_with_refreshed_session_id``
        # mirrors it onto the record on the next ``get_record`` /
        # ``list_records`` call. Second spawn pulls the resume id from
        # that record. This test wires those two ends together and
        # asserts the SAME id flows through end-to-end.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as td:
            recorded = {}
            stub = self._stub_session_class(recorded)
            manager = ClaudeSessionManager(
                state_dir=td, session_factory=stub,
            )
            # First spawn: stub auto-adopts ``fresh-id`` (the real
            # session generates a uuid synchronously in _build_command).
            manager.start_session(
                task_id='T1',
                task_summary='do work',
                initial_prompt='first message',
                cwd='/tmp/T1',
            )
            first_kwargs = recorded['kwargs']
            self.assertEqual(first_kwargs.get('resume_session_id', ''), '')

            # The manager's get_record() path is what refreshes the
            # record with the live session's id. Real kato code calls
            # this on every UI poll + before the next start_session.
            record = manager.get_record('T1')
            self.assertEqual(record.claude_session_id, 'fresh-id')

            # Subprocess exits (Claude finished its turn, idle timeout
            # fired, etc).
            manager.terminate_session('T1')

            # Operator types a follow-up. Second spawn MUST resume the
            # same id — otherwise Claude starts a new conversation and
            # re-reads the whole workspace.
            recorded.clear()
            manager.start_session(
                task_id='T1',
                task_summary='do work',
                initial_prompt='follow-up message',
                cwd='/tmp/T1',
            )
            second_kwargs = recorded['kwargs']
            self.assertEqual(
                second_kwargs.get('resume_session_id'),
                'fresh-id',
                'second spawn dropped the first spawn\'s session id — '
                'follow-up messages will fork a new session and waste tokens',
            )

    def test_cross_restart_persistence_survives_a_fresh_manager(self) -> None:
        # The operator's complaint: "if I stop kato and restart it he
        # forgets the entire session." That happens iff the manager
        # built by the new kato process doesn't re-read the on-disk
        # record from the previous process.
        #
        # This test simulates kato restart by discarding manager #1
        # and building manager #2 against the SAME ``state_dir``. The
        # second manager must hydrate the record from disk so the
        # first message after restart respawns with ``--resume <id>``,
        # not as a fresh session.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as td:
            # ---- kato run #1 ----
            recorded_run1 = {}
            stub1 = self._stub_session_class(recorded_run1)
            manager_run1 = ClaudeSessionManager(
                state_dir=td, session_factory=stub1,
            )
            manager_run1.start_session(
                task_id='T1',
                task_summary='do work',
                initial_prompt='first message',
                cwd='/tmp/T1',
            )
            manager_run1.terminate_session('T1')
            persisted_id = manager_run1.get_record('T1').claude_session_id
            self.assertEqual(persisted_id, 'fresh-id')
            del manager_run1  # operator stops kato

            # ---- kato run #2 ----
            # Fresh process: build a new manager pointed at the same
            # state_dir. _load_persisted_records runs in __init__ and
            # rehydrates the record off disk.
            recorded_run2 = {}
            stub2 = self._stub_session_class(recorded_run2)
            manager_run2 = ClaudeSessionManager(
                state_dir=td, session_factory=stub2,
            )

            # Sanity: the record is back, with its session id intact.
            record = manager_run2.get_record('T1')
            self.assertIsNotNone(
                record,
                'manager rebuilt against same state_dir failed to '
                'hydrate per-task record — restart will start fresh',
            )
            self.assertEqual(record.claude_session_id, persisted_id)

            # First message in the new run: must --resume the saved id.
            manager_run2.start_session(
                task_id='T1',
                task_summary='do work',
                initial_prompt='first message after restart',
                cwd='/tmp/T1',
            )
            self.assertEqual(
                recorded_run2['kwargs'].get('resume_session_id'),
                persisted_id,
                'fresh manager did not pass --resume to the spawn — '
                'kato restart will start a brand-new Claude session',
            )

    def test_claude_session_id_adopted_before_first_event_arrives(self) -> None:
        # The webserver SSE handler resolves the session id from the
        # live session BEFORE any event has been streamed. The session
        # must therefore advertise the resume id synchronously inside
        # ``_build_command`` — not later, after ``init`` arrives.
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )
        with tempfile.TemporaryDirectory() as td:
            session = StreamingClaudeSession(
                task_id='T1',
                cwd=td,
                binary='claude',
                resume_session_id='persisted-id-abc',
            )
            # Pre-flight: blank.
            self.assertEqual(session.claude_session_id, '')
            session._build_command()
        # Post-build: id is adopted synchronously.
        self.assertEqual(session.claude_session_id, 'persisted-id-abc')


# --------------------------------------------------------------------------
# Bug 3: no claude_core_lib involvement — see UI test suite
# --------------------------------------------------------------------------


class Bug3LayerHasNoSurfaceNoteTests(unittest.TestCase):
    """Bug 3 (draft input lost on tab switch) is a pure React-component
    state problem. claude_core_lib doesn't see the chat composer at
    all — the fix lives in ``webserver/ui/src/utils/composerDraft.js``
    + ``MessageForm.jsx``, with its own ``composerDraft.test.js``
    suite. This single test exists so the file structure makes it
    obvious that no claude_core_lib-side regression check is missing
    — the layer doesn't have the surface to regress on."""

    def test_layer_has_no_chat_composer_responsibility(self) -> None:
        # The claude_core_lib package owns the CLI subprocess, the
        # streaming protocol, and the session manager. It does NOT
        # know about the chat input UI. Sanity-check that nothing
        # in the source tree references the draft-storage key — if
        # someone adds composer-state logic here that should be in
        # the UI layer, this test fails.
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        for path in root.rglob('*.py'):
            if 'tests' in path.parts:
                continue
            content = path.read_text(encoding='utf-8', errors='replace')
            self.assertNotIn(
                'kato.composer.draft', content,
                f'composer draft logic leaked into {path}',
            )


if __name__ == '__main__':
    unittest.main()
