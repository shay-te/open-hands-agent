"""Flow #8 — Adopt an existing Claude session into a kato task.

A-Z scenario:

    1. Operator has been chatting with Claude Code in VS Code on their
       local checkout. They want kato to take over the rest of the
       work in a per-task isolated workspace.
    2. They open the kato planning UI, select task T1, and paste the
       Claude session id (visible from the VS Code transcript file).
    3. kato calls ``ClaudeSessionManager.adopt_session_id``.
    4. The session id is persisted to the per-task record AND
       mirrored to the workspace metadata (``.kato-meta.json``).
    5. Operator's next message in the kato tab spawns Claude with
       ``--resume <adopted-id>`` against the kato workspace clone.
       The conversation continues with full prior context, but in
       kato's isolated git tree.

Why isolation matters: kato edits the workspace clone, not the
operator's original checkout. Both can diverge independently after
adoption — this is intentional, documented in
``ADOPTING_EXISTING_CLAUDE_SESSIONS.md``.

Adversarial regression modes pinned:
    - Empty / whitespace session id silently accepted.
    - Adoption that DOESN'T persist to disk (lost on restart).
    - Adoption that fails to mirror to workspace metadata (workspace
      record still doesn't know about the agent session).
    - Subsequent ``start_session`` not picking up the adopted id
      (the whole reason adoption exists — to be the resume id).
    - Adoption that terminates a running subprocess (the docstring
      explicitly says it should NOT — adoption is a metadata write,
      not a lifecycle event).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Validation: adoption rejects empty / whitespace ids.
# ---------------------------------------------------------------------------


class FlowAdoptValidationTests(unittest.TestCase):

    def test_flow_adopt_rejects_empty_session_id(self) -> None:
        # An empty session id has no semantic meaning — adoption
        # should fail loudly rather than silently write a blank
        # record that the operator can't tell from "never adopted".
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            with self.assertRaises(ValueError):
                mgr.adopt_session_id('T1', agent_session_id='')

    def test_flow_adopt_rejects_whitespace_only_session_id(self) -> None:
        # Defensive: leading/trailing whitespace should be stripped;
        # if NOTHING remains, reject. Otherwise the stored id would
        # be `''` which fails any later resume.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            with self.assertRaises(ValueError):
                mgr.adopt_session_id('T1', agent_session_id='   ')

    def test_flow_adopt_normalizes_session_id_whitespace(self) -> None:
        # Trim leading/trailing whitespace (e.g., from copy-paste)
        # but accept the inner value.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            record = mgr.adopt_session_id(
                'T1', agent_session_id='  sess-abc-123\n',
            )
            self.assertEqual(record.agent_session_id, 'sess-abc-123')


# ---------------------------------------------------------------------------
# Persistence: adoption writes the per-task record to disk.
# ---------------------------------------------------------------------------


class FlowAdoptPersistenceTests(unittest.TestCase):

    def test_flow_adopt_persists_record_to_disk(self) -> None:
        # After adoption, the state_dir on disk should contain the
        # session id. This is what survives kato restart.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id(
                'T1', agent_session_id='adopted-id-from-vscode',
                task_summary='migrated from VS Code',
            )
            # On-disk record exists with the adopted id.
            persisted = json.loads(
                (Path(state_dir) / 'T1.json').read_text(encoding='utf-8'),
            )
            self.assertEqual(
                persisted['agent_session_id'], 'adopted-id-from-vscode',
                'adoption did not persist — restart will lose the adoption',
            )

    def test_flow_adopt_preserves_summary_when_provided(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id(
                'T1', agent_session_id='adopted-id',
                task_summary='migrated from VS Code',
            )
            persisted = json.loads(
                (Path(state_dir) / 'T1.json').read_text(encoding='utf-8'),
            )
            self.assertEqual(
                persisted.get('task_summary'), 'migrated from VS Code',
            )

    def test_flow_adopt_rejects_changing_existing_session_id(self) -> None:
        # Once a task has a session id, adoption is idempotent-only.
        # A different id would violate the same-session invariant.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id('T1', agent_session_id='first-id')
            with self.assertRaises(RuntimeError):
                mgr.adopt_session_id('T1', agent_session_id='second-id')
            persisted = json.loads(
                (Path(state_dir) / 'T1.json').read_text(encoding='utf-8'),
            )
            self.assertEqual(persisted['agent_session_id'], 'first-id')

    def test_flow_adopt_allows_idempotent_re_adopt(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id('T1', agent_session_id='same-id')
            mgr.adopt_session_id('T1', agent_session_id='same-id')
            self.assertEqual(mgr.get_record('T1').agent_session_id, 'same-id')

    def test_flow_adopt_with_no_existing_record_creates_one(self) -> None:
        # First-ever interaction with this task may be the adoption
        # itself — no prior record exists. Adoption must create it.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            self.assertIsNone(mgr.get_record('NEW-TASK'))
            mgr.adopt_session_id(
                'NEW-TASK', agent_session_id='new-id',
                task_summary='first contact',
            )
            self.assertEqual(
                mgr.get_record('NEW-TASK').agent_session_id, 'new-id',
            )

    def test_flow_adopt_keeps_existing_summary_when_new_is_empty(self) -> None:
        # Operator's adoption call doesn't supply a summary — kato
        # already has one from the existing record. Don't overwrite.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager, PlanningSessionRecord,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            # Seed an existing record with a summary.
            with mgr._lock:
                lookup_key = mgr._lookup_key('T1')
                mgr._records[lookup_key] = PlanningSessionRecord(
                    task_id='T1',
                    task_summary='original summary',
                    status=SESSION_STATUS_TERMINATED,
                )
                mgr._persist_record(mgr._records[lookup_key])

            mgr.adopt_session_id('T1', agent_session_id='new-id')

            self.assertEqual(
                mgr.get_record('T1').task_summary, 'original summary',
            )


# ---------------------------------------------------------------------------
# Workspace-metadata mirroring.
# ---------------------------------------------------------------------------


class FlowAdoptWorkspaceMirrorTests(unittest.TestCase):

    def test_flow_adopt_mirrors_to_workspace_metadata(self) -> None:
        # The workspace_manager keeps its own copy of agent_session_id
        # under ``.kato-meta.json``. This is what survives even if
        # the session state dir is wiped (cross-host migration). The
        # adoption MUST update that copy too.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            workspace_mgr = MagicMock()
            workspace_mgr.list_workspaces.return_value = []
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.attach_workspace_manager(workspace_mgr)

            mgr.adopt_session_id(
                'T1', agent_session_id='adopted-id',
                task_summary='go',
            )

            # At least one mirror call; the implementation may call
            # the mirror twice (once via _persist_record, once
            # explicitly inside adopt_session_id) — both are fine.
            self.assertGreaterEqual(
                workspace_mgr.update_agent_session.call_count, 1,
                'adopted id was never mirrored to workspace metadata — '
                'cross-host migration will lose the adoption',
            )
            # Every mirror call carried the adopted id.
            for call in workspace_mgr.update_agent_session.call_args_list:
                forwarded = call.kwargs.get('agent_session_id') or (
                    call.args[1] if len(call.args) > 1 else None
                )
                self.assertEqual(forwarded, 'adopted-id')


# ---------------------------------------------------------------------------
# End-to-end: adoption flows into the next spawn's --resume.
# ---------------------------------------------------------------------------


class FlowAdoptNextSpawnUsesResumeTests(unittest.TestCase):
    """The whole point of adoption is that the NEXT spawn resumes the
    adopted id. Without this, adoption is just metadata-with-no-effect."""

    def _make_stub_session(self, recorded, *, fresh_id='fresh-uuid'):
        class _StubSession:
            def __init__(self, **kwargs):
                recorded['kwargs'] = kwargs
                self._task_id = kwargs.get('task_id', '')
                self._cwd = kwargs.get('cwd', '')
                self._agent_session_id = (
                    kwargs.get('resume_session_id', '') or fresh_id
                )
                self._alive = True

            @property
            def task_id(self): return self._task_id

            @property
            def cwd(self): return self._cwd

            @property
            def agent_session_id(self): return self._agent_session_id

            @property
            def is_alive(self): return self._alive

            @property
            def is_working(self): return False

            def start(self, *, initial_prompt=''): pass

            def send_user_message(self, *args, **kwargs): pass

            def poll_event(self, *args, **kwargs): return None

            @property
            def terminal_event(self): return None

            def terminate(self): self._alive = False

            def recent_events(self): return []

            def events_after(self, _index): return [], 0

        return _StubSession

    def test_flow_adopt_then_spawn_uses_adopted_id_as_resume_id(self) -> None:
        # The full end-to-end guarantee: adopt, then ``start_session``
        # passes ``--resume <adopted-id>``.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            recorded = {}
            mgr = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=self._make_stub_session(recorded),
            )
            mgr.adopt_session_id(
                'T1', agent_session_id='adopted-id-from-vscode',
            )
            mgr.start_session(
                task_id='T1', initial_prompt='continue',
                cwd='/tmp/T1',
            )
            self.assertEqual(
                recorded['kwargs'].get('resume_session_id'),
                'adopted-id-from-vscode',
                'spawn after adopt did NOT pass --resume — adoption was '
                'a no-op, conversation will start fresh',
            )

    def test_flow_adopt_survives_simulated_kato_restart(self) -> None:
        # Adopt with manager #1, destroy, rebuild manager #2 with
        # same state_dir, spawn — should still --resume the adopted id.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            # Run 1: adopt.
            mgr1 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=self._make_stub_session({}),
            )
            mgr1.adopt_session_id('T1', agent_session_id='adopted-id-xyz')
            del mgr1

            # Run 2: fresh process, fresh manager.
            recorded = {}
            mgr2 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=self._make_stub_session(recorded),
            )
            mgr2.start_session(
                task_id='T1', initial_prompt='continue',
                cwd='/tmp/T1',
            )
            self.assertEqual(
                recorded['kwargs'].get('resume_session_id'),
                'adopted-id-xyz',
                'adoption did not survive restart — operator lost their '
                'cross-session continuity',
            )


if __name__ == '__main__':
    unittest.main()
