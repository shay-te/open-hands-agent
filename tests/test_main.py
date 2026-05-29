import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch


from kato_core_lib.main import (
    _RESUME_CONTINUE_PROMPT,
    _RESUME_WAIT_PROMPT,
    _cleanup_done_tasks_at_boot,
    _requeue_stuck_comments,
    _start_pending_comment_work_after_ui,
    _start_pending_comment_work,
    _start_pending_comment_work_when_ui_ready,
    _reset_stuck_workspace_statuses,
    _resume_prompt_for_workspace,
    _resume_streaming_sessions,
    _run_task_scan_loop,
    main,
)
from tests.utils import build_test_cfg


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self._env_patch = patch.dict(
            'os.environ',
            {
                'KATO_IGNORED_REPOSITORY_FOLDERS': '',
                # OG4 — TLS pin validator is now strict-by-default in
                # main(). Existing tests don't exercise pinning, so
                # they opt out at the test-env level. The dedicated
                # ``MainTlsPinIntegrationTests`` class below locks
                # the actual integration behavior.
                'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
            },
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_main_returns_zero_on_success(self) -> None:
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato_core_lib.main.validate_environment') as mock_validate_environment, patch(
            'kato_core_lib.main.KatoInstance.init'
        ) as mock_init, patch(
            'kato_core_lib.main.KatoInstance.get',
            return_value=app,
        ), patch('kato_core_lib.main._run_task_scan_loop') as mock_run_loop:
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_validate_environment.assert_called_once_with(mode='all')
        mock_init.assert_called_once_with(self.cfg)
        mock_run_loop.assert_called_once_with(
            app,
            startup_delay_seconds=30.0,
            scan_interval_seconds=60.0,
            force_scan_event=ANY,
        )
        app.logger.info.assert_any_call('Starting kato agent')

    def test_main_configures_logger_when_app_logger_is_missing(self) -> None:
        configured_logger = Mock()
        app = types.SimpleNamespace(logger=None)

        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.configure_logger', return_value=configured_logger
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get',
            return_value=app,
        ), patch('kato_core_lib.main._run_task_scan_loop'):
            main(self.cfg)

        self.assertIs(app.logger, configured_logger)

    def test_run_task_scan_loop_waits_before_first_scan_and_sleeps_between_cycles(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [None, None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job) as mock_job_cls, patch(
            'kato_core_lib.main.supports_inline_status',
            return_value=False,
        ), patch('kato_core_lib.main.time.sleep') as mock_sleep:
            _run_task_scan_loop(
                app,
                startup_delay_seconds=30.0,
                scan_interval_seconds=60.0,
                sleep_fn=mock_sleep,
                max_cycles=2,
            )

        mock_job_cls.assert_called_once_with()
        job.initialized.assert_called_once_with(app)
        self.assertEqual(job.run.call_count, 2)
        # The first sleep is the 30s startup delay. After each scan tick
        # the loop divides the 60s scan interval into 5s heartbeat chunks
        # (so the planning UI status bar gets a live countdown). Total
        # sleep between ticks must still sum to 60s.
        sleep_durations = [call_obj.args[0] for call_obj in mock_sleep.call_args_list]
        self.assertEqual(sleep_durations[0], 30.0)
        between_ticks = sleep_durations[1:]
        # 12 chunks of 5s = 60s total. Allow either-or since the loop
        # may emit slightly fewer chunks if the deadline elapses early.
        self.assertAlmostEqual(sum(between_ticks), 60.0, delta=5.0)
        app.logger.info.assert_any_call(
            'Waiting %s before scanning tasks while Kato warms up',
            '30 seconds',
        )

    def test_run_task_scan_loop_uses_warmup_countdown_when_inline_status_is_supported(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job), patch(
            'kato_core_lib.main.supports_inline_status',
            return_value=True,
        ), patch(
            'kato_core_lib.main.sleep_with_warmup_countdown'
        ) as mock_warmup_countdown:
            _run_task_scan_loop(
                app,
                startup_delay_seconds=30.0,
                # Any positive value keeps the loop running — ``<=0``
                # is the manual-only sentinel that short-circuits.
                scan_interval_seconds=0.01,
                max_cycles=1,
            )

        mock_warmup_countdown.assert_called_once_with(30.0, sleep_fn=unittest.mock.ANY)
        # Each scan tick now logs the start/end so the planning UI status
        # bar reflects what kato is doing in real time.
        app.logger.info.assert_any_call('Scanning for new tasks and reviews')
        app.logger.info.assert_any_call('Scan complete')

    def test_run_task_scan_loop_continues_after_failure(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [RuntimeError('service down'), None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job), patch(
            'kato_core_lib.main.time.sleep'
        ) as mock_sleep:
            _run_task_scan_loop(
                app,
                startup_delay_seconds=0.0,
                scan_interval_seconds=60.0,
                sleep_fn=mock_sleep,
                max_cycles=2,
            )

        self.assertEqual(job.run.call_count, 2)
        app.logger.warning.assert_called_once_with(
            'task scan failed; retrying in %s seconds',
            60.0,
        )

    def test_resume_prompt_continues_interrupted_work_by_default(self) -> None:
        record = types.SimpleNamespace()

        self.assertEqual(_resume_prompt_for_workspace(record), _RESUME_CONTINUE_PROMPT)

    def test_resume_prompt_waits_for_operator_for_planning_workspace(self) -> None:
        record = types.SimpleNamespace(resume_on_startup=False)

        self.assertEqual(_resume_prompt_for_workspace(record), _RESUME_WAIT_PROMPT)

    def test_resume_prompt_includes_forbidden_repository_guardrails(self) -> None:
        record = types.SimpleNamespace()

        with patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            prompt = _resume_prompt_for_workspace(record)

        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('secret-client', prompt)
        self.assertTrue(prompt.endswith(_RESUME_CONTINUE_PROMPT))

    def test_resume_streaming_sessions_starts_active_workspace_with_continue_prompt(self) -> None:
        workspace_root = types.SimpleNamespace(is_dir=Mock(return_value=True))
        workspace_manager = types.SimpleNamespace(
            list_workspaces=Mock(
                return_value=[
                    types.SimpleNamespace(
                        task_id='PROJ-1',
                        task_summary='continue me',
                        status='active',
                        cwd='',
                        repository_ids=['client'],
                    )
                ]
            ),
            repository_path=Mock(return_value=workspace_root),
        )
        session_manager = types.SimpleNamespace(start_session=Mock())
        app = types.SimpleNamespace(
            logger=Mock(),
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
        )

        _resume_streaming_sessions(app)

        session_manager.start_session.assert_called_once()
        call_kwargs = session_manager.start_session.call_args.kwargs
        self.assertEqual(call_kwargs['task_id'], 'PROJ-1')
        self.assertEqual(call_kwargs['initial_prompt'], _RESUME_CONTINUE_PROMPT)
        self.assertEqual(call_kwargs['cwd'], str(workspace_root))

    def test_resume_streaming_sessions_uses_wait_prompt_for_operator_driven_workspace(self) -> None:
        workspace_manager = types.SimpleNamespace(
            list_workspaces=Mock(
                return_value=[
                    types.SimpleNamespace(
                        task_id='PROJ-2',
                        task_summary='planning chat',
                        status='active',
                        cwd='/repo',
                        repository_ids=['client'],
                        resume_on_startup=False,
                    )
                ]
            ),
        )
        session_manager = types.SimpleNamespace(start_session=Mock())
        app = types.SimpleNamespace(
            logger=Mock(),
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
        )

        _resume_streaming_sessions(app)

        session_manager.start_session.assert_called_once()
        call_kwargs = session_manager.start_session.call_args.kwargs
        self.assertEqual(call_kwargs['task_id'], 'PROJ-2')
        self.assertEqual(call_kwargs['initial_prompt'], _RESUME_WAIT_PROMPT)

    def test_resume_streaming_sessions_recovers_latest_agent_session_id_after_restart(self) -> None:
        """End-to-end: a kato restart re-attaches the existing chat to its
        most recently persisted Claude session id, not a fresh session.

        Sets up a real ``ClaudeSessionManager`` (not a mock) pointed at a
        temp state dir. Starts a session for PROJ-1 — this writes
        ``agent_session_id`` to ``<state_dir>/PROJ-1.json``. Then
        simulates "kato restart" by building a brand-new manager pointed
        at the same dir and feeding it through ``_resume_streaming_sessions``.
        Asserts that the resumed session inherits the persisted session id,
        which is what makes the chat resume from where it left off instead
        of starting a fresh conversation.
        """
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from claude_core_lib.claude_core_lib.tests.session.test_manager import _FakeStreamingSession

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            first_fakes: list = []

            def first_factory(**kwargs):
                s = _FakeStreamingSession(**kwargs)
                first_fakes.append(s)
                return s

            # --- Run 1: start a session, capture the persisted agent_session_id
            first_manager = ClaudeSessionManager(
                state_dir=state_dir, session_factory=first_factory,
            )
            first_manager.start_session(task_id='PROJ-1', task_summary='resume me')
            persisted_session_id = first_fakes[0].agent_session_id
            self.assertTrue(persisted_session_id)

            # --- Simulated restart: new manager, same state_dir, no in-memory carry-over
            second_fakes: list = []

            def second_factory(**kwargs):
                s = _FakeStreamingSession(**kwargs)
                second_fakes.append(s)
                return s

            rebooted_manager = ClaudeSessionManager(
                state_dir=state_dir, session_factory=second_factory,
            )

            workspace_root = types.SimpleNamespace(is_dir=Mock(return_value=True))
            workspace_manager = types.SimpleNamespace(
                list_workspaces=Mock(
                    return_value=[
                        types.SimpleNamespace(
                            task_id='PROJ-1',
                            task_summary='resume me',
                            status='active',
                            cwd='',
                            repository_ids=['client'],
                        )
                    ]
                ),
                repository_path=Mock(return_value=workspace_root),
                update_agent_session=Mock(),
            )
            app = types.SimpleNamespace(
                logger=Mock(),
                session_manager=rebooted_manager,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
            )

            _resume_streaming_sessions(app)

            # Exactly one new session spawned, and it inherits the
            # persisted agent_session_id as its resume target — proving
            # the chat picks up where the previous kato run left off.
            self.assertEqual(len(second_fakes), 1)
            self.assertEqual(second_fakes[0].resume_session_id, persisted_session_id)

    def test_resume_streaming_sessions_keeps_original_id_when_re_adopt_is_attempted(self) -> None:
        """Restart resume must use the first pinned session id."""
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from claude_core_lib.claude_core_lib.tests.session.test_manager import _FakeStreamingSession

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)

            # Manager 1: first session
            fakes_1: list = []
            mgr_1 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: fakes_1.append(_FakeStreamingSession(**kw)) or fakes_1[-1],
            )
            mgr_1.start_session(task_id='PROJ-1', task_summary='first run')
            persisted_session_id = mgr_1.get_record('PROJ-1').agent_session_id
            mgr_1.terminate_session('PROJ-1')

            # Manager 2 (simulated restart 1): a different adoption is refused.
            fakes_2: list = []
            mgr_2 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: fakes_2.append(_FakeStreamingSession(**kw)) or fakes_2[-1],
            )
            with self.assertRaises(RuntimeError):
                mgr_2.adopt_session_id('PROJ-1', agent_session_id='newer-session-uuid')
            latest_session_id = mgr_2.get_record('PROJ-1').agent_session_id
            self.assertEqual(latest_session_id, persisted_session_id)

            # Manager 3 (simulated restart 2): _resume_streaming_sessions
            # MUST pick up the original pinned id, not the rejected id.
            fakes_3: list = []
            mgr_3 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: fakes_3.append(_FakeStreamingSession(**kw)) or fakes_3[-1],
            )
            workspace_manager = types.SimpleNamespace(
                list_workspaces=Mock(
                    return_value=[
                        types.SimpleNamespace(
                            task_id='PROJ-1',
                            task_summary='first run',
                            status='active',
                            cwd='/repo',
                            repository_ids=['client'],
                        )
                    ]
                ),
                update_agent_session=Mock(),
            )
            app = types.SimpleNamespace(
                logger=Mock(),
                session_manager=mgr_3,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
            )

            _resume_streaming_sessions(app)

            self.assertEqual(len(fakes_3), 1)
            self.assertEqual(fakes_3[0].resume_session_id, persisted_session_id)

    def test_resume_streaming_sessions_seeds_from_workspace_metadata_before_spawn(self) -> None:
        """Empty manager state still resumes the id stored on workspace metadata."""
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from claude_core_lib.claude_core_lib.tests.session.test_manager import _FakeStreamingSession

        with tempfile.TemporaryDirectory() as tmp:
            fakes: list = []

            def factory(**kwargs):
                session = _FakeStreamingSession(**kwargs)
                fakes.append(session)
                return session

            manager = ClaudeSessionManager(
                state_dir=Path(tmp),
                session_factory=factory,
            )
            workspace_record = types.SimpleNamespace(
                task_id='PROJ-1',
                task_summary='from workspace',
                status='active',
                cwd='/repo',
                repository_ids=['client'],
                agent_session_id='workspace-session-id',
            )
            workspace_manager = types.SimpleNamespace(
                list_workspaces=Mock(return_value=[workspace_record]),
                update_agent_session=Mock(),
            )
            app = types.SimpleNamespace(
                logger=Mock(),
                session_manager=manager,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
            )

            _resume_streaming_sessions(app)

            self.assertEqual(len(fakes), 1)
            self.assertEqual(fakes[0].resume_session_id, 'workspace-session-id')
            self.assertEqual(
                manager.get_record('PROJ-1').agent_session_id,
                'workspace-session-id',
            )

    def test_main_returns_one_without_traceback_when_startup_validation_fails(self) -> None:
        configured_logger = Mock()
        env_error = ValueError('unsupported issue platform: linear')

        with patch('kato_core_lib.main.configure_logger', return_value=configured_logger), patch(
            'kato_core_lib.main.validate_environment',
            side_effect=env_error,
        ), patch(
            'kato_core_lib.main.KatoInstance.init',
        ) as mock_init:
            result = main(self.cfg)

        self.assertEqual(result, 1)
        configured_logger.error.assert_called_once_with('%s', env_error)
        mock_init.assert_not_called()

    def test_docker_mode_on_runs_sandbox_preflight(self) -> None:
        """``KATO_CLAUDE_DOCKER=true`` must run the sandbox daemon checks.

        Locks the Phase 2 gate at ``main.py:86``. If a future refactor
        reverts ``is_docker_mode_enabled()`` back to ``is_bypass_enabled()``,
        ``docker=true, bypass=false`` operators silently lose the docker
        daemon preflight — exactly the case this gate exists to catch.
        """
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_docker_mode_enabled',
            return_value=True,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_docker_or_exit'
        ) as mock_check_docker, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_gvisor_or_exit'
        ) as mock_check_gvisor, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available',
            return_value=True,
        ) as mock_gvisor_runtime, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.docker_running_rootless',
            return_value=True,
        ) as mock_rootless:
            main(self.cfg)

        mock_check_docker.assert_called_once()
        mock_check_gvisor.assert_called_once()
        mock_gvisor_runtime.assert_called_once()
        mock_rootless.assert_called_once()

    def test_docker_mode_off_skips_sandbox_preflight(self) -> None:
        """``KATO_CLAUDE_DOCKER`` unset → the four sandbox helpers must not run.

        Without this assertion, a regression that runs the sandbox
        preflight unconditionally would force every kato user to install
        Docker even when they're on the host-only path.
        """
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_docker_mode_enabled',
            return_value=False,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_docker_or_exit'
        ) as mock_check_docker, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_gvisor_or_exit'
        ) as mock_check_gvisor, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available'
        ) as mock_gvisor_runtime, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.docker_running_rootless'
        ) as mock_rootless:
            main(self.cfg)

        mock_check_docker.assert_not_called()
        mock_check_gvisor.assert_not_called()
        mock_gvisor_runtime.assert_not_called()
        mock_rootless.assert_not_called()


class MainTlsPinIntegrationTests(unittest.TestCase):
    """Locks the OG4 wiring: ``main()`` calls the TLS pin validator.

    The validator now implements a TOFU lifecycle (env var / opt-out
    / first-run / subsequent-run); the lifecycle's own behavior is
    tested in ``test_tls_pin.py``. This class only locks the
    ``main()`` ↔ validator wiring: that ``main()`` invokes the
    validator on every startup and propagates ``TlsPinError`` to a
    non-zero exit code.

    The opt-out path is the most convenient one to drive end-to-end
    here: it returns silently without touching the network or the
    filesystem, which keeps the test hermetic.
    """

    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        # Clear any inherited opt-out so each test below sets the
        # env explicitly. ``main()`` reads the live ``os.environ``.
        self._env_patch = patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': ''},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Drop the TLS env vars if a previous test or shell set them.
        for key in (
            'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256',
            'KATO_SANDBOX_ALLOW_NO_TLS_PIN',
        ):
            if key in os.environ:
                del os.environ[key]

    def _run_main_with_other_validators_mocked(self) -> int:
        """Run main with everything except the TLS pin validator mocked.

        Lets the test focus on whether the TLS pin validator actually
        fires, without setUp ordering / repository / job mocking
        noise.
        """
        app = types.SimpleNamespace(logger=Mock())
        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ):
            return main(self.cfg)

    def test_main_proceeds_when_optout_is_set(self) -> None:
        """``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` opts out — main proceeds."""
        os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN'] = 'true'
        try:
            result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN']
        self.assertEqual(result, 0)

    def test_main_invokes_tls_pin_validator(self) -> None:
        """Direct integration check: the validator function is called.

        Even when the validator's own decision is to return silently,
        the call MUST happen on every startup — its absence would
        silently disable the OG4 protection. Patches the validator
        at the ``kato_core_lib.main`` module to verify the call site.
        """
        os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN'] = 'true'
        try:
            with patch(
                'kato_core_lib.main.validate_anthropic_tls_pin_or_refuse',
            ) as mock_validator:
                self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN']
        mock_validator.assert_called_once()

    def test_main_returns_one_when_validator_raises(self) -> None:
        """Refusal path: a ``TlsPinError`` from the validator → exit 1.

        Locks the error-propagation half of the wiring. If a future
        refactor swallows the exception or returns 0 in the error
        path, this test fails. Uses the env-var ambiguity case (both
        env vars set → ``Pick one``) as the trigger because it's
        deterministic and doesn't need network or file mocking.
        """
        os.environ['KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256'] = (
            'QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE='  # 32 'A' bytes
        )
        os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN'] = 'true'
        try:
            result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256']
            del os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN']
        self.assertEqual(result, 1)


class MainReadOnlyToolsIntegrationTests(unittest.TestCase):
    """Locks the read-only-tools wiring: ``main()`` calls the gate.

    Without this test, ``validate_read_only_tools_requires_docker``
    is just a function in a module — a refactor that drops the call
    from ``main()`` would silently let
    ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` flow through to a
    host-mode spawn where pre-approved ``grep`` reads the operator's
    home directory.
    """

    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self._env_patch = patch.dict(
            'os.environ',
            {
                'KATO_IGNORED_REPOSITORY_FOLDERS': '',
                # Opt out of TLS pin so this class focuses on the
                # read-only gate, not the OG4 gate.
                'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
            },
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Drop the read-only flag if a previous test or shell set it.
        for key in (
            'KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS',
            'KATO_CLAUDE_DOCKER',
        ):
            if key in os.environ:
                del os.environ[key]

    def _run_main_with_other_validators_mocked(self) -> int:
        app = types.SimpleNamespace(logger=Mock())
        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.validate_anthropic_tls_pin_or_refuse'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ):
            return main(self.cfg)

    def test_main_refuses_when_read_only_set_without_docker(self) -> None:
        """Strict gate: read-only=true alone -> main() returns 1."""
        os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS'] = 'true'
        try:
            result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS']
        self.assertEqual(result, 1)

    def test_main_proceeds_when_both_set(self) -> None:
        """The valid combination: read-only=true + docker=true."""
        os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS'] = 'true'
        os.environ['KATO_CLAUDE_DOCKER'] = 'true'
        try:
            # ``check_docker_or_exit`` would otherwise probe the
            # daemon; patch it (and the gVisor probe) for the same
            # reason the existing main tests do.
            with patch(
                'sandbox_core_lib.sandbox_core_lib.manager.check_docker_or_exit'
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.check_gvisor_or_exit'
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available',
                return_value=False,
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.docker_running_rootless',
                return_value=False,
            ):
                result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS']
            del os.environ['KATO_CLAUDE_DOCKER']
        self.assertEqual(result, 0)

    def test_main_invokes_read_only_validator(self) -> None:
        """Direct integration check: the validator function is called."""
        with patch(
            'kato_core_lib.main.validate_read_only_tools_requires_docker',
        ) as mock_validator:
            self._run_main_with_other_validators_mocked()
        mock_validator.assert_called_once()


class CleanupDoneTasksAtBootTests(unittest.TestCase):
    """Boot-time prune so a restart never resurrects a done task's tab.

    The "task is back after restart" bug: cleanup only ran on a scan
    tick, so a stale ``~/.kato/sessions/<id>.json`` left a tab on
    screen until the first tick ~30s later. This runs the prune at
    boot, before the webserver serves the tab list.
    """

    def test_delegates_to_agent_service_cleanup(self) -> None:
        cleanup = Mock()
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(cleanup_done_tasks=cleanup),
        )
        _cleanup_done_tasks_at_boot(app)
        cleanup.assert_called_once_with()

    def test_noop_when_service_missing(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        _cleanup_done_tasks_at_boot(app)  # no raise

    def test_noop_when_service_lacks_method(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(), service=types.SimpleNamespace(),
        )
        _cleanup_done_tasks_at_boot(app)  # no raise

    def test_swallows_cleanup_exception(self) -> None:
        cleanup = Mock(side_effect=RuntimeError('platform down'))
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(cleanup_done_tasks=cleanup),
        )
        _cleanup_done_tasks_at_boot(app)  # must NOT raise — boot continues
        app.logger.exception.assert_called()

    def test_runs_before_the_planning_webserver_starts(self) -> None:
        # Ordering guard: a restart must prune BEFORE the webserver
        # serves the tab list, otherwise the done tab flashes back.
        import inspect
        from kato_core_lib import main as kato_main
        src = inspect.getsource(kato_main.main)
        boot_idx = src.index('_cleanup_done_tasks_at_boot(app)')
        web_idx = src.index('_start_planning_webserver_if_enabled(app)')
        self.assertLess(boot_idx, web_idx)


class ResetStuckWorkspaceStatusesTests(unittest.TestCase):
    """Tests for _reset_stuck_workspace_statuses (Fix 3 boot-time status repair)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name)
        from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib
        self._lib = WorkspaceCoreLib(
            root=self.workspace_root,
            max_parallel_tasks=2,
            metadata_filename='.kato-meta.json',
            preflight_log_filename='.kato-preflight.log',
        )
        self.workspace_manager = self._lib.workspaces

    def _make_app(self):
        return types.SimpleNamespace(
            logger=Mock(),
            workspace_manager=self.workspace_manager,
        )

    def _create_workspace(self, task_id, status, repo_ids=None):
        from workspace_core_lib.workspace_core_lib import (
            WORKSPACE_STATUS_PROVISIONING,
        )
        record = self.workspace_manager.create(
            task_id=task_id,
            task_summary='test task',
            repository_ids=repo_ids or [],
        )
        self.workspace_manager.update_status(task_id, status)
        return record

    def _add_git_repo(self, task_id, repo_id):
        repo_path = self.workspace_root / task_id / repo_id
        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / '.git').mkdir(exist_ok=True)

    def test_provisioning_with_git_repo_is_promoted_to_active(self) -> None:
        self._create_workspace('PROJ-1', 'provisioning', ['client'])
        self._add_git_repo('PROJ-1', 'client')
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-1')
        self.assertEqual(record.status, 'active')
        app.logger.info.assert_any_call(
            'workspace %s promoted from provisioning to active '
            '(repos were cloned before the previous kato process stopped)',
            'PROJ-1',
        )

    def test_provisioning_without_git_repo_is_not_promoted(self) -> None:
        self._create_workspace('PROJ-2', 'provisioning', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-2')
        self.assertEqual(record.status, 'provisioning')
        app.logger.warning.assert_any_call(
            'workspace %s is stuck in provisioning state with no valid '
            'git repos — the previous clone was incomplete. '
            'Re-run the task to provision it correctly.',
            'PROJ-2',
        )

    def test_errored_workspace_logs_warning_without_status_change(self) -> None:
        self._create_workspace('PROJ-3', 'errored', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-3')
        self.assertEqual(record.status, 'errored')
        app.logger.warning.assert_any_call(
            'workspace %s is in errored state from a previous run — '
            'operator may need to re-run the task or discard the workspace',
            'PROJ-3',
        )

    def test_active_workspace_is_left_unchanged(self) -> None:
        self._create_workspace('PROJ-4', 'active', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-4')
        self.assertEqual(record.status, 'active')
        app.logger.info.assert_not_called()

    def test_review_workspace_is_left_unchanged(self) -> None:
        self._create_workspace('PROJ-5', 'review', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-5')
        self.assertEqual(record.status, 'review')

    def test_noop_when_workspace_manager_is_none(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(),
            workspace_manager=None,
        )
        _reset_stuck_workspace_statuses(app)
        app.logger.info.assert_not_called()
        app.logger.warning.assert_not_called()

    def test_noop_when_workspace_manager_attribute_missing(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        _reset_stuck_workspace_statuses(app)
        app.logger.info.assert_not_called()

    def test_promotion_count_logged_when_multiple_workspaces_promoted(self) -> None:
        for i in (1, 2):
            self._create_workspace(f'PROJ-{i}', 'provisioning', ['client'])
            self._add_git_repo(f'PROJ-{i}', 'client')
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        app.logger.info.assert_any_call(
            'promoted %d workspace(s) from provisioning to active at boot',
            2,
        )


class RequeueStuckCommentsBootTests(unittest.TestCase):
    """_requeue_stuck_comments delegates to the service and logs a count."""

    def test_delegates_and_logs_when_comments_requeued(self) -> None:
        service = types.SimpleNamespace(
            requeue_stuck_in_progress_comments=Mock(return_value=[
                {'task_id': 'UNA-1', 'comment_id': 'c1'},
                {'task_id': 'UNA-2', 'comment_id': 'c2'},
            ]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _requeue_stuck_comments(app)

        service.requeue_stuck_in_progress_comments.assert_called_once_with()
        app.logger.info.assert_called_once_with(
            'requeued %d comment(s) stuck in-progress from the previous '
            'run; _start_pending_comment_work will dispatch them next',
            2,
        )

    def test_silent_when_nothing_requeued(self) -> None:
        service = types.SimpleNamespace(
            requeue_stuck_in_progress_comments=Mock(return_value=[]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _requeue_stuck_comments(app)

        app.logger.info.assert_not_called()

    def test_service_error_does_not_abort_boot(self) -> None:
        service = types.SimpleNamespace(
            requeue_stuck_in_progress_comments=Mock(
                side_effect=RuntimeError('boom'),
            ),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _requeue_stuck_comments(app)  # must not raise

        app.logger.exception.assert_called_once()

    def test_noop_when_service_missing_or_method_absent(self) -> None:
        _requeue_stuck_comments(types.SimpleNamespace(logger=Mock()))
        app = types.SimpleNamespace(logger=Mock(), service=object())
        _requeue_stuck_comments(app)
        app.logger.info.assert_not_called()

    def test_boot_order_requeue_runs_before_scan_loop(self) -> None:
        import inspect
        from kato_core_lib import main as main_module
        src = inspect.getsource(main_module.main)
        requeue_idx = src.index('_requeue_stuck_comments(app)')
        reset_idx = src.index('_reset_stuck_workspace_statuses(app)')
        scan_idx = src.index('_run_task_scan_loop(')
        # Requeue after the workspace status reset (so workspaces are
        # ACTIVE) and before the scan loop that drains the queue.
        self.assertLess(reset_idx, requeue_idx)
        self.assertLess(requeue_idx, scan_idx)


class StartPendingCommentWorkBootTests(unittest.TestCase):
    """_start_pending_comment_work dispatches queued comments at boot
    so the agent starts immediately, not on the first scan tick."""

    def test_delegates_and_logs_started_count(self) -> None:
        service = types.SimpleNamespace(
            drain_all_queued_task_comments=Mock(return_value=[
                {'task_id': 'UNA-1', 'started': True, 'comment_id': 'c1'},
                {'task_id': 'UNA-2', 'started': True, 'comment_id': 'c2'},
            ]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _start_pending_comment_work(app)

        service.drain_all_queued_task_comments.assert_called_once_with()
        app.logger.info.assert_called_once_with(
            'started agent work on %d task(s) with queued comments at boot',
            2,
        )

    def test_silent_when_nothing_queued(self) -> None:
        service = types.SimpleNamespace(
            drain_all_queued_task_comments=Mock(return_value=[]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _start_pending_comment_work(app)

        app.logger.info.assert_not_called()

    def test_service_error_does_not_abort_boot(self) -> None:
        service = types.SimpleNamespace(
            drain_all_queued_task_comments=Mock(
                side_effect=RuntimeError('boom'),
            ),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _start_pending_comment_work(app)  # must not raise

        app.logger.exception.assert_called_once()

    def test_noop_when_service_missing_or_method_absent(self) -> None:
        _start_pending_comment_work(types.SimpleNamespace(logger=Mock()))
        app = types.SimpleNamespace(logger=Mock(), service=object())
        _start_pending_comment_work(app)
        app.logger.info.assert_not_called()

    def test_boot_order_dispatch_is_deferred_until_after_webserver_start(self) -> None:
        import inspect
        from kato_core_lib import main as main_module
        src = inspect.getsource(main_module.main)
        requeue_idx = src.index('_requeue_stuck_comments(app)')
        webserver_idx = src.index('_start_planning_webserver_if_enabled(app)')
        start_idx = src.index('_start_pending_comment_work_after_ui(app)')
        scan_idx = src.index('_run_task_scan_loop(')
        # Stale IN_PROGRESS → QUEUED must happen before the deferred
        # dispatch, but the UI is started first so comment resume work
        # cannot delay the planning page.
        self.assertLess(requeue_idx, start_idx)
        self.assertLess(webserver_idx, start_idx)
        self.assertLess(start_idx, scan_idx)

    def test_deferred_dispatch_runs_in_background_thread(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        with patch('kato_core_lib.main.threading.Thread') as thread_cls:
            _start_pending_comment_work_after_ui(app)
        thread_cls.assert_called_once()
        self.assertEqual(
            thread_cls.call_args.kwargs['name'],
            'kato-start-pending-comments',
        )
        self.assertTrue(thread_cls.call_args.kwargs['daemon'])
        thread_cls.return_value.start.assert_called_once_with()

    def test_deferred_worker_waits_for_ui_healthz_before_dispatch(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(),
            planning_webserver_url='http://127.0.0.1:5050',
        )
        with patch(
            'kato_core_lib.main._wait_for_planning_ui_healthz',
        ) as wait, patch(
            'kato_core_lib.main._start_pending_comment_work',
        ) as start:
            _start_pending_comment_work_when_ui_ready(app)
        wait.assert_called_once_with(
            'http://127.0.0.1:5050', logger=app.logger,
        )
        start.assert_called_once_with(app)

    def test_deferred_worker_dispatches_immediately_without_ui_url(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        with patch(
            'kato_core_lib.main._wait_for_planning_ui_healthz',
        ) as wait, patch(
            'kato_core_lib.main._start_pending_comment_work',
        ) as start:
            _start_pending_comment_work_when_ui_ready(app)
        wait.assert_not_called()
        start.assert_called_once_with(app)


if __name__ == '__main__':
    unittest.main()
