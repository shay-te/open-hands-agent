"""Regression locks for the three session-lifecycle bugs the operator reported.

These tests exist as a single named-bug file (rather than scattered across
the broader coverage suites) so a future refactor that re-introduces one
of these issues surfaces with a self-explanatory failure name.

Bug 1 (history lost / re-runs work on kato restart):
    kato used to call ``_resume_streaming_sessions`` from ``main()`` which
    autonomously spawned Claude with a "resume the interrupted task" prompt
    for every active workspace at boot. Result: tokens burned, work
    re-explored, chat history pushed below an autonomous turn.

    Fix: tabs are lazy after restart. The disk JSONL is replayed via the
    existing SSE pipe when the operator opens a tab; Claude is only
    spawned (with ``--resume <id>``) when the operator sends a follow-up
    message. Matches VS Code Claude Code behaviour.

Bug 2 (new "session" + re-explores workspace on follow-up):
    ``resume_session_for_chat`` always wrapped the user's message in
    ``prepend_chat_workspace_context`` (workspace inventory + continuity
    block + forbidden-repo guardrails). When ``--resume <id>`` was being
    passed (which loads the prior conversation), wrapping made Claude
    treat each respawn as a fresh task and re-explore the workspace.

    Fix: skip the wrapper when there's a persisted session id to resume
    from. Claude already has the workspace context from the JSONL.

Bug 3 (draft input vanishes on tab switch):
    The chat composer kept its textarea value in component state. Tab
    switches unmount the component, so React drops the state. Pure-Python
    side of the fix: nothing to test here. The composer-draft helpers
    live in ``webserver/ui/src/utils/composerDraft.js`` with their own
    JS tests; the verifiable Python-side behaviour is that the
    ``MessageForm`` component receives ``taskId`` as a prop from
    ``SessionDetail`` (the key that drives the per-task storage). That's
    a JSX file, not Python — see the JS suite for that lock.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib import main as main_module
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)


class Bug1NoAutoResumeOnKatoRestartTests(unittest.TestCase):
    """Lock that ``main()`` does NOT autonomously spawn Claude sessions
    for active workspaces at boot. The operator's chat tab must show the
    on-disk conversation history without kato pushing a new turn."""

    def _run_main(self, **overrides):
        """Drive ``main(cfg)`` past every external gate so we can observe
        the post-init startup steps."""
        cfg = SimpleNamespace(
            core_lib=SimpleNamespace(app=SimpleNamespace(name='kato')),
            kato=SimpleNamespace(),
        )
        patches = dict(
            validate_environment=MagicMock(),
            validate_bypass_permissions=MagicMock(),
            validate_read_only_tools_requires_docker=MagicMock(),
            validate_anthropic_tls_pin_or_refuse=MagicMock(),
            print_security_posture=MagicMock(),
            KatoInstance=MagicMock(),
            configure_logger=MagicMock(return_value=MagicMock()),
            _recover_orphan_workspaces=MagicMock(),
            _reconcile_workspace_branches=MagicMock(),
            _reset_stuck_workspace_statuses=MagicMock(),
            _start_planning_webserver_if_enabled=MagicMock(),
            _register_shutdown_hook=MagicMock(),
            _warm_up_repository_inventory=MagicMock(),
            _run_task_scan_loop=MagicMock(),
            _task_scan_settings=MagicMock(return_value=(0.0, 0.0)),
        )
        patches.update(overrides)
        ctx = []
        for name, value in patches.items():
            p = patch.object(main_module, name, value)
            ctx.append(p)
            p.start()
        # Force bypass-validator off so the docker preflight block skips.
        from sandbox_core_lib.sandbox_core_lib import (
            bypass_permissions_validator as real_bypass,
        )
        original = real_bypass.is_docker_mode_enabled
        real_bypass.is_docker_mode_enabled = lambda: False
        try:
            main_module.main.__wrapped__(cfg)
        finally:
            real_bypass.is_docker_mode_enabled = original
            for p in ctx:
                p.stop()

    def test_main_does_not_call_resume_streaming_sessions(self) -> None:
        # Hard lock: if a future refactor wires ``_resume_streaming_sessions``
        # back into ``main()`` this test fails. The function is still
        # defined for unit-test reachability, but the boot path no longer
        # invokes it.
        with patch.object(
            main_module, '_resume_streaming_sessions',
        ) as resume_mock:
            self._run_main()
        resume_mock.assert_not_called()

    def test_main_calls_post_init_helpers_but_not_resume(self) -> None:
        # Spot-check that the OTHER post-init helpers DO fire — proves
        # the assertion above isn't passing because we shortcut out of
        # ``main()`` before reaching the (removed) resume step.
        with patch.object(
            main_module, '_recover_orphan_workspaces',
        ) as recover_mock, patch.object(
            main_module, '_reconcile_workspace_branches',
        ) as reconcile_mock, patch.object(
            main_module, '_reset_stuck_workspace_statuses',
        ) as reset_mock, patch.object(
            main_module, '_start_planning_webserver_if_enabled',
        ) as webserver_mock, patch.object(
            main_module, '_resume_streaming_sessions',
        ) as resume_mock:
            self._run_main(
                _recover_orphan_workspaces=recover_mock,
                _reconcile_workspace_branches=reconcile_mock,
                _reset_stuck_workspace_statuses=reset_mock,
                _start_planning_webserver_if_enabled=webserver_mock,
                _resume_streaming_sessions=resume_mock,
            )
        recover_mock.assert_called_once()
        reconcile_mock.assert_called_once()
        reset_mock.assert_called_once()
        webserver_mock.assert_called_once()
        resume_mock.assert_not_called()


class Bug2ResumeSendsRawUserMessageTests(unittest.TestCase):
    """Lock that ``resume_session_for_chat`` does NOT pre-wrap the
    operator's message in workspace-inventory / continuity / forbidden
    blocks when a session id is on file. Wrapping makes Claude treat
    each respawn as a fresh task and re-explore the workspace —
    burning tokens and producing the "starts everything from scratch"
    behaviour the operator reported."""

    def test_resume_with_session_id_sends_raw_message(self) -> None:
        manager = MagicMock()
        manager.get_record.return_value = SimpleNamespace(
            agent_session_id='persisted-id-abc',
        )
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='T1', message='fix the bug',
            cwd='/wks/T1', task_summary='task summary',
            additional_dirs=['/wks/T1/sibling'],
        )
        sent = manager.start_session.call_args.kwargs['initial_prompt']
        # The raw message was sent — no inventory / continuity wrapper.
        self.assertEqual(sent, 'fix the bug')

    def test_resume_with_session_id_does_not_emit_continuity_block(self) -> None:
        manager = MagicMock()
        manager.get_record.return_value = SimpleNamespace(
            agent_session_id='persisted-id-abc',
        )
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='T1', message='message', cwd='/wks',
        )
        sent = manager.start_session.call_args.kwargs['initial_prompt']
        # Continuity-block tells (used on first spawn) are absent.
        self.assertNotIn('Trust it', sent)
        self.assertNotIn('Repositories available', sent)
        self.assertNotIn('Forbidden repository folders', sent)

    def test_first_spawn_with_no_record_keeps_workspace_wrapper(self) -> None:
        # Symmetric guarantee: when there's NO record (genuine first
        # message after adopt), the wrapper IS injected so Claude sees
        # the workspace on its very first turn.
        manager = MagicMock()
        manager.get_record.return_value = None
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='T1', message='start work', cwd='/wks',
        )
        sent = manager.start_session.call_args.kwargs['initial_prompt']
        self.assertIn('Trust it', sent)
        self.assertIn('start work', sent)

    def test_first_spawn_with_record_lacking_session_id_keeps_wrapper(
        self,
    ) -> None:
        # Edge: record exists but ``agent_session_id`` is blank (e.g.
        # the prior session was rejected by Claude and self-healed back
        # to a fresh start). Treat as first-spawn — wrap the message.
        manager = MagicMock()
        manager.get_record.return_value = SimpleNamespace(agent_session_id='')
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='T1', message='start work', cwd='/wks',
        )
        sent = manager.start_session.call_args.kwargs['initial_prompt']
        self.assertIn('Trust it', sent)


class Bug3DraftPropPassedToComposerTests(unittest.TestCase):
    """Python-side check that ``SessionDetail.jsx`` passes ``taskId`` to
    ``MessageForm``. That prop is what the JS-side draft helpers key
    on. Without it, the per-task localStorage scheme can't work.

    The JS-side helpers (read/write/clear) have their own dedicated
    node:test suite — see ``webserver/ui/src/utils/composerDraft.test.js``.
    """

    def test_session_detail_passes_task_id_to_message_form(self) -> None:
        from pathlib import Path
        session_detail = Path(__file__).resolve().parents[1] / (
            'webserver/ui/src/components/SessionDetail.jsx'
        )
        source = session_detail.read_text(encoding='utf-8')
        # Find the MessageForm tag and verify ``taskId={taskId}`` is in
        # its props. A regression that drops the prop fails this check.
        message_form_block = source[source.index('<MessageForm'):]
        message_form_block = message_form_block[: message_form_block.index('/>')]
        self.assertIn('taskId={taskId}', message_form_block)

    def test_message_form_imports_draft_helpers(self) -> None:
        from pathlib import Path
        message_form = Path(__file__).resolve().parents[1] / (
            'webserver/ui/src/components/MessageForm.jsx'
        )
        source = message_form.read_text(encoding='utf-8')
        # The component imports ``readDraft`` and ``writeDraft`` from
        # the pure helper module. Catches regressions that re-inline
        # the storage code into the component (and lose testability).
        self.assertIn(
            "from '../utils/composerDraft.js'", source,
        )
        self.assertIn('readDraft', source)
        self.assertIn('writeDraft', source)


if __name__ == '__main__':
    unittest.main()
