"""Coverage for ``kato_core_lib.main`` helper functions.

The ``main()`` entry point itself is hard to drive end-to-end without
booting the full orchestrator, so this file targets the testable
helpers: proxy classes, workspace reconcile/reset/resume, webserver
bootstrap, scan-loop helpers, shutdown hook, etc.
"""

from __future__ import annotations

import io
import signal
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib import main as main_module


class ProxyClassesTests(unittest.TestCase):
    """Lines 48-50, 56-58, 62-64: lazy-import proxies."""

    def test_process_assigned_tasks_job_proxy_delegates(self) -> None:
        proxy = main_module._ProcessAssignedTasksJobProxy()
        fake_module = MagicMock()
        fake_module.ProcessAssignedTasksJob.return_value = 'job-instance'
        with patch.dict(
            'sys.modules',
            {'kato_core_lib.jobs.process_assigned_tasks': fake_module},
            clear=False,
        ):
            result = proxy()
        self.assertEqual(result, 'job-instance')

    def test_kato_instance_proxy_init_delegates(self) -> None:
        fake_module = MagicMock()
        with patch.dict(
            'sys.modules',
            {'kato_core_lib.kato_instance': fake_module},
            clear=False,
        ):
            main_module._KatoInstanceProxy.init('fake-cfg')
        fake_module.KatoInstance.init.assert_called_once_with('fake-cfg')

    def test_kato_instance_proxy_get_delegates(self) -> None:
        fake_module = MagicMock()
        fake_module.KatoInstance.get.return_value = 'kato-instance'
        with patch.dict(
            'sys.modules',
            {'kato_core_lib.kato_instance': fake_module},
            clear=False,
        ):
            result = main_module._KatoInstanceProxy.get()
        self.assertEqual(result, 'kato-instance')


class ReconcileWorkspaceBranchesTests(unittest.TestCase):
    def test_returns_silently_when_workspace_manager_missing(self) -> None:
        # Line 199.
        app = SimpleNamespace(workspace_manager=None, logger=MagicMock())
        main_module._reconcile_workspace_branches(app)

    def test_swallows_import_error_for_webserver(self) -> None:
        # Lines 205-207.
        app = SimpleNamespace(
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )
        # Force the import to fail by removing kato_webserver from modules
        # and inserting an importer that raises.
        with patch.dict('sys.modules', {}, clear=False):
            import sys
            sys.modules.pop('kato_webserver.git_diff_utils', None)
            with patch.dict('sys.modules',
                            {'kato_webserver.git_diff_utils': None}):
                # Should return silently.
                main_module._reconcile_workspace_branches(app)

    def test_swallows_list_workspaces_exception(self) -> None:
        # Lines 209-212.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.side_effect = RuntimeError('fail')
        app = SimpleNamespace(
            workspace_manager=workspace_manager,
            logger=MagicMock(),
        )
        with patch.dict(
            'sys.modules',
            {'kato_webserver.git_diff_utils': MagicMock()},
            clear=False,
        ):
            main_module._reconcile_workspace_branches(app)
        app.logger.exception.assert_called()


class ResetStuckWorkspaceStatusesTests(unittest.TestCase):
    def test_returns_when_workspace_manager_missing(self) -> None:
        app = SimpleNamespace(workspace_manager=None, logger=MagicMock())
        main_module._reset_stuck_workspace_statuses(app)

    def test_swallows_list_workspaces_exception(self) -> None:
        # Lines 281-283.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.side_effect = RuntimeError('boom')
        app = SimpleNamespace(
            workspace_manager=workspace_manager,
            logger=MagicMock(),
        )
        main_module._reset_stuck_workspace_statuses(app)
        app.logger.exception.assert_called()

    def test_skips_blank_task_id(self) -> None:
        # Line 288.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(task_id='', status='provisioning'),
            SimpleNamespace(task_id='PROJ-1', status='errored'),
        ]
        app = SimpleNamespace(
            workspace_manager=workspace_manager,
            logger=MagicMock(),
        )
        main_module._reset_stuck_workspace_statuses(app)
        # The ERRORED workspace got the visible warning.
        app.logger.warning.assert_called()

    def test_promote_failure_is_logged(self) -> None:
        # Lines 311-312.
        record = SimpleNamespace(
            task_id='PROJ-1', status='provisioning',
            repository_ids=['repo-a'],
        )
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [record]
        # Returns a path with .git inside.
        with patch.object(
            main_module, '_provisioning_workspace_has_git_repo',
            return_value=True,
        ):
            workspace_manager.update_status.side_effect = RuntimeError(
                'promote fail',
            )
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            main_module._reset_stuck_workspace_statuses(app)
        app.logger.exception.assert_called()

    def test_warns_when_no_valid_git_repo_for_provisioning(self) -> None:
        # Lines 316-322.
        record = SimpleNamespace(
            task_id='PROJ-1', status='provisioning',
            repository_ids=['repo-a'],
        )
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [record]
        with patch.object(
            main_module, '_provisioning_workspace_has_git_repo',
            return_value=False,
        ):
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            main_module._reset_stuck_workspace_statuses(app)
        # Warning logged about stuck provisioning state.
        self.assertTrue(any(
            'stuck in provisioning' in str(c.args[0])
            for c in app.logger.warning.call_args_list
        ))


class ProvisioningWorkspaceHasGitRepoTests(unittest.TestCase):
    def test_returns_false_when_no_repo_ids(self) -> None:
        # Line 341.
        result = main_module._provisioning_workspace_has_git_repo(
            MagicMock(),
            'PROJ-1',
            SimpleNamespace(repository_ids=[]),
        )
        self.assertFalse(result)

    def test_returns_true_when_at_least_one_git_clone_present(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            git_path = Path(td) / 'repo-a' / '.git'
            git_path.mkdir(parents=True)
            workspace_manager = MagicMock()
            workspace_manager.repository_path.return_value = git_path.parent
            result = main_module._provisioning_workspace_has_git_repo(
                workspace_manager,
                'PROJ-1',
                SimpleNamespace(repository_ids=['repo-a']),
            )
        self.assertTrue(result)

    def test_swallows_repository_path_exception(self) -> None:
        # Lines 337-338.
        workspace_manager = MagicMock()
        workspace_manager.repository_path.side_effect = RuntimeError('fail')
        result = main_module._provisioning_workspace_has_git_repo(
            workspace_manager,
            'PROJ-1',
            SimpleNamespace(repository_ids=['repo-a']),
        )
        self.assertFalse(result)


class ResumeStreamingSessionsTests(unittest.TestCase):
    def test_returns_when_session_manager_missing(self) -> None:
        # Line 378.
        main_module._resume_streaming_sessions(SimpleNamespace(
            session_manager=None, workspace_manager=None,
            planning_session_runner=None, logger=MagicMock(),
        ))

    def test_swallows_list_workspaces_exception(self) -> None:
        # Lines 389-391.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.side_effect = RuntimeError('fail')
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=workspace_manager,
            planning_session_runner=None,
            logger=MagicMock(),
        )
        main_module._resume_streaming_sessions(app)
        app.logger.exception.assert_called()

    def test_skips_records_with_blank_task_id_or_non_active_status(self) -> None:
        # Lines 401, 404.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(task_id='', status='active'),  # skip blank
            SimpleNamespace(task_id='T1', status='done'),  # skip non-active
        ]
        session_manager = MagicMock()
        app = SimpleNamespace(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
            logger=MagicMock(),
        )
        main_module._resume_streaming_sessions(app)
        session_manager.start_session.assert_not_called()

    def test_falls_back_to_first_repo_path_when_cwd_missing(self) -> None:
        # Lines 408-417.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td) / 'repo-a'
            repo_dir.mkdir()
            workspace_manager = MagicMock()
            workspace_manager.list_workspaces.return_value = [
                SimpleNamespace(
                    task_id='T1', status='active',
                    cwd='', repository_ids=['repo-a'],
                    task_summary='', resume_on_startup=True,
                ),
            ]
            workspace_manager.repository_path.return_value = repo_dir
            session_manager = MagicMock()
            app = SimpleNamespace(
                session_manager=session_manager,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
                logger=MagicMock(),
            )
            main_module._resume_streaming_sessions(app)
        session_manager.start_session.assert_called_once()

    def test_skips_when_cwd_unresolvable(self) -> None:
        # Lines 418-420.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(
                task_id='T1', status='active',
                cwd='', repository_ids=[],  # no repos to fall back to
                task_summary='',
            ),
        ]
        session_manager = MagicMock()
        app = SimpleNamespace(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
            logger=MagicMock(),
        )
        main_module._resume_streaming_sessions(app)
        session_manager.start_session.assert_not_called()
        # The skipped task gets logged.
        app.logger.info.assert_called()

    def test_swallows_session_start_exception(self) -> None:
        # Lines 433-439.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(
                task_id='T1', status='active', cwd='/some/path',
                repository_ids=['repo-a'], task_summary='',
                resume_on_startup=True,
            ),
        ]
        session_manager = MagicMock()
        session_manager.start_session.side_effect = RuntimeError('fail')
        app = SimpleNamespace(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
            logger=MagicMock(),
        )
        main_module._resume_streaming_sessions(app)
        app.logger.warning.assert_called()


class PlanningSpawnDefaultsTests(unittest.TestCase):
    def test_returns_empty_when_runner_none(self) -> None:
        self.assertEqual(main_module._planning_spawn_defaults(None), {})

    def test_returns_empty_when_runner_lacks_defaults(self) -> None:
        # Lines 460-461.
        runner = SimpleNamespace(_defaults=None)
        self.assertEqual(main_module._planning_spawn_defaults(runner), {})

    def test_populates_defaults_dict(self) -> None:
        # Lines 462-475.
        defaults = SimpleNamespace(
            binary='claude', model='haiku', permission_mode='plan',
            permission_prompt_tool='', allowed_tools='', disallowed_tools='',
            effort='', max_turns=8,
        )
        result = main_module._planning_spawn_defaults(
            SimpleNamespace(_defaults=defaults),
        )
        self.assertEqual(result['binary'], 'claude')
        self.assertEqual(result['model'], 'haiku')
        self.assertEqual(result['max_turns'], 8)


class ResumePromptForWorkspaceTests(unittest.TestCase):
    def test_continue_prompt_when_resume_on_startup_true(self) -> None:
        # Line 479-482.
        prompt = main_module._resume_prompt_for_workspace(
            SimpleNamespace(resume_on_startup=True),
        )
        self.assertIn('Resume the interrupted task', prompt)

    def test_wait_prompt_when_resume_on_startup_false(self) -> None:
        # Line 483.
        prompt = main_module._resume_prompt_for_workspace(
            SimpleNamespace(resume_on_startup=False),
        )
        self.assertIn('no user', prompt.lower())


class RecoverOrphanWorkspacesTests(unittest.TestCase):
    def test_returns_when_recovery_service_missing(self) -> None:
        # Line 494.
        app = SimpleNamespace(
            workspace_recovery_service=None, logger=MagicMock(),
        )
        main_module._recover_orphan_workspaces(app)

    def test_swallows_recovery_exception(self) -> None:
        # Lines 495-499.
        recovery = MagicMock()
        recovery.recover_orphan_workspaces.side_effect = RuntimeError('fail')
        app = SimpleNamespace(
            workspace_recovery_service=recovery, logger=MagicMock(),
        )
        main_module._recover_orphan_workspaces(app)
        app.logger.exception.assert_called()

    def test_logs_count_when_adopted(self) -> None:
        # Lines 500-505.
        recovery = MagicMock()
        recovery.recover_orphan_workspaces.return_value = ['rec1', 'rec2']
        app = SimpleNamespace(
            workspace_recovery_service=recovery, logger=MagicMock(),
        )
        main_module._recover_orphan_workspaces(app)
        app.logger.info.assert_called()


class RecoverOrphanWorkspacesEmptyAdoptedTests(unittest.TestCase):
    """Line 575->exit partial: when ``recover_orphan_workspaces`` returns
    an empty/falsy list, the function exits without logging info."""

    def test_no_log_when_no_workspaces_adopted(self) -> None:
        recovery = MagicMock()
        # Empty list is falsy — the ``if adopted:`` branch falls through.
        recovery.recover_orphan_workspaces.return_value = []
        app = SimpleNamespace(
            workspace_recovery_service=recovery, logger=MagicMock(),
        )
        main_module._recover_orphan_workspaces(app)
        # Recovery was attempted but no info log because nothing was adopted.
        recovery.recover_orphan_workspaces.assert_called_once()
        app.logger.info.assert_not_called()

    def test_log_uses_singular_for_one_workspace(self) -> None:
        # Lines 577-580: covers the ``'' if len(adopted) == 1 else 's'``
        # singular branch.
        recovery = MagicMock()
        recovery.recover_orphan_workspaces.return_value = ['only_one']
        app = SimpleNamespace(
            workspace_recovery_service=recovery, logger=MagicMock(),
        )
        main_module._recover_orphan_workspaces(app)
        app.logger.info.assert_called_once()
        # The log includes the count "1".
        args = app.logger.info.call_args[0]
        self.assertEqual(args[1], 1)


class ResumeStreamingSessionsCwdFallbackTests(unittest.TestCase):
    """Line 459 partial: ``candidate.is_dir()`` False path — the loop
    over ``repository_ids`` should continue to the next candidate when
    a repository_path returns a path that is not a directory."""

    def test_falls_through_when_first_repo_path_is_not_a_dir(self) -> None:
        # repository_ids = ['gone', 'present']. First .is_dir() returns
        # False (loop continues), second returns True (cwd captured).
        candidate_gone = SimpleNamespace(is_dir=MagicMock(return_value=False))
        candidate_present = SimpleNamespace(
            is_dir=MagicMock(return_value=True),
            __str__=lambda self: '/wks/PROJ-1/present',
        )
        # Wire a workspace_manager that returns different candidates per id.
        def repo_path(task_id, repo_id):
            return candidate_gone if repo_id == 'gone' else candidate_present

        workspace_manager = SimpleNamespace(
            list_workspaces=MagicMock(return_value=[
                SimpleNamespace(
                    task_id='PROJ-1',
                    task_summary='',
                    status='active',
                    cwd='',
                    repository_ids=['gone', 'present'],
                ),
            ]),
            repository_path=MagicMock(side_effect=repo_path),
        )
        session_manager = SimpleNamespace(start_session=MagicMock())
        app = SimpleNamespace(
            logger=MagicMock(),
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
        )
        main_module._resume_streaming_sessions(app)
        # start_session was called once with cwd pulled from the SECOND
        # candidate (the first one had is_dir=False).
        session_manager.start_session.assert_called_once()
        kwargs = session_manager.start_session.call_args.kwargs
        # cwd is str(present) — non-empty.
        self.assertTrue(kwargs['cwd'])
        # First candidate was inspected (is_dir called).
        candidate_gone.is_dir.assert_called()


class ShutdownHookWatcherTests(unittest.TestCase):
    """Lines 701-704 (main.py): the shutdown hook stops the resume_prompt
    watcher if one is attached, swallowing exceptions from .stop()."""

    def test_handler_stops_watcher_when_attached(self) -> None:
        watcher = MagicMock()
        app = SimpleNamespace(
            service=MagicMock(),
            logger=MagicMock(),
            resume_prompt_watcher=watcher,
        )
        original_sigint = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            handler = signal.getsignal(signal.SIGINT)
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
            watcher.stop.assert_called_once()
        finally:
            signal.signal(signal.SIGINT, original_sigint)

    def test_handler_swallows_watcher_stop_exception(self) -> None:
        # Lines 701-704: watcher.stop() blows up → exception logged
        # but the shutdown continues to service.shutdown().
        watcher = MagicMock()
        watcher.stop.side_effect = RuntimeError('watcher stop boom')
        service = MagicMock()
        app = SimpleNamespace(
            service=service,
            logger=MagicMock(),
            resume_prompt_watcher=watcher,
        )
        original_sigint = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            handler = signal.getsignal(signal.SIGINT)
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
            # The watcher.stop failure was logged.
            app.logger.exception.assert_any_call(
                'error stopping resume_prompt watcher',
            )
            # And shutdown still ran on the service.
            service.shutdown.assert_called_once()
        finally:
            signal.signal(signal.SIGINT, original_sigint)


class StartResumePromptWatcherTests(unittest.TestCase):
    """Lines 896-901: when ``build_and_start_resume_prompt_watcher``
    raises, the function logs via exception() and returns without
    setting ``app.resume_prompt_watcher``."""

    def test_no_op_when_session_manager_missing(self) -> None:
        # Lines 882-887: session_manager is None → info log + return.
        app = SimpleNamespace(
            session_manager=None,
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )
        main_module._start_resume_prompt_watcher(app)
        app.logger.info.assert_called_once()
        self.assertFalse(hasattr(app, 'resume_prompt_watcher'))

    def test_no_op_when_workspace_manager_missing(self) -> None:
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=None,
            logger=MagicMock(),
        )
        main_module._start_resume_prompt_watcher(app)
        app.logger.info.assert_called_once()
        self.assertFalse(hasattr(app, 'resume_prompt_watcher'))

    def test_attaches_watcher_to_app_on_success(self) -> None:
        # Happy path: builder returns a watcher → stored on app.
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )
        fake_watcher = MagicMock()
        with patch(
            'kato_core_lib.data_layers.service.resume_prompt_watcher'
            '.build_and_start_resume_prompt_watcher',
            return_value=fake_watcher,
        ):
            main_module._start_resume_prompt_watcher(app)
        self.assertIs(app.resume_prompt_watcher, fake_watcher)

    def test_exception_during_build_is_logged_and_swallowed(self) -> None:
        # Lines 896-901: builder raises → exception() log + return.
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )
        with patch(
            'kato_core_lib.data_layers.service.resume_prompt_watcher'
            '.build_and_start_resume_prompt_watcher',
            side_effect=RuntimeError('builder boom'),
        ):
            main_module._start_resume_prompt_watcher(app)
        # exception was logged and the attribute was NOT set.
        app.logger.exception.assert_called_once()
        self.assertFalse(hasattr(app, 'resume_prompt_watcher'))


class StartPlanningWebserverTests(unittest.TestCase):
    def test_skips_when_disabled_via_env(self) -> None:
        # Line 521-522.
        import os
        with patch.dict(os.environ, {'KATO_WEBSERVER_DISABLED': 'true'}):
            app = SimpleNamespace(
                session_manager=MagicMock(),
                workspace_manager=MagicMock(),
                logger=MagicMock(),
            )
            main_module._start_planning_webserver_if_enabled(app)
        app.logger.info.assert_called()

    def test_skips_when_no_managers(self) -> None:
        # Lines 527-533.
        import os
        with patch.dict(os.environ, {'KATO_WEBSERVER_DISABLED': ''}):
            app = SimpleNamespace(
                session_manager=None, workspace_manager=None,
                logger=MagicMock(),
            )
            main_module._start_planning_webserver_if_enabled(app)
        app.logger.info.assert_called()


class RegisterShutdownHookTests(unittest.TestCase):
    def test_registers_handler_and_handles_missing_sigterm(self) -> None:
        # Lines 632-639: register SIGINT, attempt SIGTERM, swallow.
        app = SimpleNamespace(
            service=MagicMock(), logger=MagicMock(),
        )
        original_sigint = signal.getsignal(signal.SIGINT)
        try:
            with patch.object(
                signal, 'signal', wraps=signal.signal,
            ) as fake_signal:
                # Force SIGTERM registration to raise.
                def selective(signum, handler):
                    if signum == signal.SIGINT:
                        return None
                    raise ValueError('SIGTERM not installable')
                fake_signal.side_effect = selective
                main_module._register_shutdown_hook(app)
            app.logger.debug.assert_called()
        finally:
            # Restore SIGINT.
            signal.signal(signal.SIGINT, original_sigint)


class WarmUpRepositoryInventoryTests(unittest.TestCase):
    def test_calls_warm_up_when_callable(self) -> None:
        # Line 646.
        warm_up = MagicMock()
        service = SimpleNamespace(warm_up_repository_inventory=warm_up)
        app = SimpleNamespace(service=service)
        main_module._warm_up_repository_inventory(app)
        warm_up.assert_called_once()

    def test_no_op_when_warm_up_missing(self) -> None:
        app = SimpleNamespace(service=SimpleNamespace())
        # No raise.
        main_module._warm_up_repository_inventory(app)


class TaskScanSettingsTests(unittest.TestCase):
    def test_reads_settings_with_defaults(self) -> None:
        # Default ``scan_interval_seconds=180`` (3 min): slow enough
        # that parallel PR-lookups don't trip provider rate limits,
        # fast enough that review-comment pickup stays responsive.
        # See ``_run_task_scan_loop`` for the guard that treats
        # ``<=0`` as "manual-only" mode.
        cfg = SimpleNamespace(kato=SimpleNamespace(
            get=lambda key, default=None: default,
        ))
        startup, scan = main_module._task_scan_settings(cfg)
        self.assertEqual(startup, 5.0)
        self.assertEqual(scan, 180.0)

    def test_reads_settings_from_config(self) -> None:
        cfg = SimpleNamespace(kato=SimpleNamespace(
            get=lambda key, default=None:
                {'startup_delay_seconds': 10, 'scan_interval_seconds': 60}
                if key == 'task_scan' else default,
        ))
        startup, scan = main_module._task_scan_settings(cfg)
        self.assertEqual(startup, 10.0)
        self.assertEqual(scan, 60.0)


class RunTaskScanLoopTests(unittest.TestCase):
    # ``scan_interval_seconds=0.01`` (any positive value) keeps the
    # loop running — ``<=0`` is now a sentinel meaning "manual-only
    # mode, don't enter the loop at all" (see _run_task_scan_loop).

    def test_runs_max_cycles_and_exits(self) -> None:
        # Cover the scan loop with bounded cycles.
        app = MagicMock()
        app.logger = MagicMock()
        with patch.object(main_module, 'ProcessAssignedTasksJob') as job_cls, \
             patch.object(main_module, 'supports_inline_status',
                          return_value=False):
            job = MagicMock()
            job_cls.return_value = job
            main_module._run_task_scan_loop(
                app,
                startup_delay_seconds=0,
                scan_interval_seconds=0.01,
                sleep_fn=lambda _s: None,
                max_cycles=2,
            )
        self.assertEqual(job.run.call_count, 2)

    def test_logs_warmup_message_for_non_tty(self) -> None:
        # Lines 674-679: non-TTY → plain log + sleep.
        app = MagicMock()
        app.logger = MagicMock()
        sleeper = MagicMock()
        with patch.object(main_module, 'ProcessAssignedTasksJob') as job_cls, \
             patch.object(main_module, 'supports_inline_status',
                          return_value=False):
            job_cls.return_value = MagicMock()
            main_module._run_task_scan_loop(
                app,
                startup_delay_seconds=2.0,
                scan_interval_seconds=0.01,
                sleep_fn=sleeper,
                max_cycles=1,
            )
        # The warmup sleep was issued.
        self.assertTrue(sleeper.called)

    def test_swallows_job_run_exception(self) -> None:
        # Lines 690-694: scan failure → warn + continue.
        app = MagicMock()
        app.logger = MagicMock()
        with patch.object(main_module, 'ProcessAssignedTasksJob') as job_cls:
            job = MagicMock()
            job.run.side_effect = RuntimeError('scan fail')
            job_cls.return_value = job
            main_module._run_task_scan_loop(
                app,
                startup_delay_seconds=0,
                scan_interval_seconds=0.01,
                sleep_fn=lambda _s: None,
                max_cycles=1,
            )
        app.logger.warning.assert_called()

    def test_zero_interval_disables_loop_entirely(self) -> None:
        # ``scan_interval_seconds=0`` is the "manual-only" sentinel:
        # the loop exits immediately, never invokes the job. Operator
        # has to trigger scans via the UI / /api/scan/trigger.
        app = MagicMock()
        app.logger = MagicMock()
        with patch.object(main_module, 'ProcessAssignedTasksJob') as job_cls:
            job = MagicMock()
            job_cls.return_value = job
            main_module._run_task_scan_loop(
                app,
                startup_delay_seconds=0,
                scan_interval_seconds=0,
                sleep_fn=lambda _s: None,
                max_cycles=5,  # ignored — guard short-circuits
            )
        job.run.assert_not_called()
        app.logger.info.assert_called()


class IdleWithHeartbeatTests(unittest.TestCase):
    def test_returns_immediately_for_non_positive_interval(self) -> None:
        # Line 733.
        sleeper = MagicMock()
        main_module._idle_with_heartbeat(
            0, logger=MagicMock(), sleep_fn=sleeper,
        )
        sleeper.assert_not_called()

    def test_force_scan_event_breaks_loop(self) -> None:
        # Lines 738-739.
        event = threading.Event()
        event.set()  # Already set — first iteration breaks immediately.
        sleeper = MagicMock()
        main_module._idle_with_heartbeat(
            10, logger=MagicMock(), sleep_fn=sleeper,
            force_scan_event=event,
        )
        # Sleep was never invoked because we broke out before it.
        sleeper.assert_not_called()

    def test_uses_inline_spinner_when_tty_supports_it(self) -> None:
        # Lines 749-757: when supports_inline_status() returns True,
        # use the countdown spinner.
        with patch.object(main_module, 'supports_inline_status',
                          return_value=True), \
             patch.object(main_module, 'sleep_with_countdown_spinner') as spin:
            main_module._idle_with_heartbeat(
                1.0, logger=MagicMock(), sleep_fn=lambda _s: None,
                heartbeat_seconds=1.0,
            )
        spin.assert_called()

    def test_uses_event_wait_when_no_tty_and_event_provided(self) -> None:
        # Line 762: ``force_scan_event.wait(timeout=chunk)``.
        event = MagicMock()
        event.is_set.return_value = False
        event.wait = MagicMock(return_value=None)
        with patch.object(main_module, 'supports_inline_status',
                          return_value=False):
            main_module._idle_with_heartbeat(
                1.0, logger=MagicMock(), sleep_fn=lambda _s: None,
                heartbeat_seconds=1.0, force_scan_event=event,
            )
        event.wait.assert_called()


class FormattedDurationTextTests(unittest.TestCase):
    def test_singular_second(self) -> None:
        self.assertEqual(
            main_module._formatted_duration_text(1.0),
            '1 second',
        )

    def test_plural_seconds(self) -> None:
        self.assertEqual(
            main_module._formatted_duration_text(5.0),
            '5 seconds',
        )

    def test_fractional_seconds(self) -> None:
        # Line 774.
        self.assertEqual(
            main_module._formatted_duration_text(2.5),
            '2.5 seconds',
        )


class MainBodyTests(unittest.TestCase):
    """Cover the gates inside ``main()`` — bypass refusal, tls-pin refusal,
    KatoInstance init refusal. The function is hard to drive end-to-end
    so we patch each gate individually."""

    def _cfg(self):
        return SimpleNamespace(
            core_lib=SimpleNamespace(app=SimpleNamespace(name='kato')),
            kato=SimpleNamespace(),
        )

    def _patches(self, **overrides):
        defaults = dict(
            validate_environment=MagicMock(),
            validate_bypass_permissions=MagicMock(),
            validate_read_only_tools_requires_docker=MagicMock(),
            validate_anthropic_tls_pin_or_refuse=MagicMock(),
            print_security_posture=MagicMock(),
            KatoInstance=MagicMock(),
            configure_logger=MagicMock(return_value=MagicMock()),
        )
        defaults.update(overrides)
        return defaults

    def _run_main(self, **overrides):
        patches = self._patches(**overrides)
        ctx = []
        for name, value in patches.items():
            p = patch.object(main_module, name, value)
            ctx.append(p)
            p.start()
        # Lazy-import gate inside main() also needs patching.
        bypass_mod = MagicMock()
        bypass_mod.is_docker_mode_enabled.return_value = False
        mods_patch = patch.dict('sys.modules', {
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator':
                bypass_mod,
        }, clear=False)
        # Don't replace the existing module — only override attributes
        # the test cares about. We just need is_docker_mode_enabled.
        from sandbox_core_lib.sandbox_core_lib import (
            bypass_permissions_validator as real_bypass,
        )
        real_bypass_orig = real_bypass.is_docker_mode_enabled
        real_bypass.is_docker_mode_enabled = lambda: False
        try:
            return main_module.main.__wrapped__(self._cfg())
        finally:
            real_bypass.is_docker_mode_enabled = real_bypass_orig
            for p in ctx:
                p.stop()

    def test_main_returns_1_on_environment_failure(self) -> None:
        # Lines 80-82: ``validate_environment`` raises ValueError → 1.
        env = MagicMock(side_effect=ValueError('bad env'))
        rc = self._run_main(validate_environment=env)
        self.assertEqual(rc, 1)

    def test_main_returns_1_on_bypass_refusal(self) -> None:
        # Lines 85-87.
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            BypassPermissionsRefused,
        )
        rc = self._run_main(
            validate_bypass_permissions=MagicMock(
                side_effect=BypassPermissionsRefused('refused'),
            ),
        )
        self.assertEqual(rc, 1)

    def test_main_returns_1_on_read_only_tools_refusal(self) -> None:
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            BypassPermissionsRefused,
        )
        rc = self._run_main(
            validate_read_only_tools_requires_docker=MagicMock(
                side_effect=BypassPermissionsRefused('refused'),
            ),
        )
        self.assertEqual(rc, 1)

    def test_main_returns_1_on_kato_instance_startup_validation_failure(self) -> None:
        # Lines 161-164: ``startup dependency validation failed:`` → 1.
        ki = MagicMock()
        ki.init.side_effect = RuntimeError(
            'startup dependency validation failed: missing token'
        )
        rc = self._run_main(KatoInstance=ki)
        self.assertEqual(rc, 1)

    def test_main_returns_1_on_error_prefix_kato_instance_failure(self) -> None:
        # Lines 161-164: ``[Error] ...`` is also re-raised as exit 1.
        ki = MagicMock()
        ki.init.side_effect = RuntimeError('[Error] git missing on PATH')
        rc = self._run_main(KatoInstance=ki)
        self.assertEqual(rc, 1)

    def test_main_re_raises_unknown_runtime_errors(self) -> None:
        # Line 165: re-raise unknown RuntimeError.
        ki = MagicMock()
        ki.init.side_effect = RuntimeError('something completely different')
        with self.assertRaisesRegex(RuntimeError, 'completely different'):
            self._run_main(KatoInstance=ki)

    def test_main_returns_1_on_tls_pin_failure(self) -> None:
        from sandbox_core_lib.sandbox_core_lib.tls_pin import TlsPinError
        rc = self._run_main(
            validate_anthropic_tls_pin_or_refuse=MagicMock(
                side_effect=TlsPinError('mismatch'),
            ),
        )
        self.assertEqual(rc, 1)


class OpenBrowserWhenReadyEnvDisableTests(unittest.TestCase):
    def test_skips_when_env_var_disables(self) -> None:
        # Line 593-594.
        import os
        with patch.dict(os.environ, {'KATO_OPEN_BROWSER': '0'}, clear=False), \
             patch('threading.Thread') as thread_cls:
            main_module._open_browser_when_ready('http://x', MagicMock())
        thread_cls.assert_not_called()

    def test_spawns_daemon_thread_when_enabled(self) -> None:
        # Lines 612-616 — thread spawn (we patch out so we don't poll).
        import os
        with patch.dict(os.environ, {'KATO_OPEN_BROWSER': '1'}, clear=False), \
             patch('threading.Thread') as thread_cls:
            main_module._open_browser_when_ready('http://x', MagicMock())
        thread_cls.assert_called_once()

    def test_wait_and_open_succeeds_when_healthz_responds(self) -> None:
        # Lines 596-608: success path inside the daemon thread.
        import os
        logger = MagicMock()
        captured = {}

        def capture_thread(*args, **kwargs):
            captured['fn'] = kwargs.get('target')
            return MagicMock()

        with patch.dict(os.environ, {'KATO_OPEN_BROWSER': '1'}, clear=False), \
             patch('threading.Thread', side_effect=capture_thread):
            main_module._open_browser_when_ready('http://localhost', logger)

        # Drive _wait_and_open directly. urlopen returns success → opens
        # the browser tab.
        with patch('urllib.request.urlopen') as urlopen, \
             patch('webbrowser.open_new_tab') as open_tab:
            urlopen.return_value.__enter__ = MagicMock()
            urlopen.return_value.__exit__ = MagicMock()
            captured['fn']()
        open_tab.assert_called_once_with('http://localhost')

    def test_wait_and_open_retries_then_succeeds(self) -> None:
        # Lines 602-603: ``except URLError: time.sleep(0.25)`` retry loop.
        import os
        import urllib.error
        logger = MagicMock()
        captured = {}

        def capture_thread(*args, **kwargs):
            captured['fn'] = kwargs.get('target')
            return MagicMock()

        with patch.dict(os.environ, {'KATO_OPEN_BROWSER': '1'}, clear=False), \
             patch('threading.Thread', side_effect=capture_thread):
            main_module._open_browser_when_ready('http://localhost', logger)

        # First urlopen call raises; second succeeds.
        successful_response = MagicMock()
        successful_response.__enter__ = MagicMock()
        successful_response.__exit__ = MagicMock()
        urlopen_iter = iter([
            urllib.error.URLError('refused'),
            successful_response,
        ])

        def fake_urlopen(*args, **kwargs):
            value = next(urlopen_iter)
            if isinstance(value, Exception):
                raise value
            return value

        with patch('urllib.request.urlopen', side_effect=fake_urlopen), \
             patch('time.monotonic', side_effect=[0.0, 1.0, 2.0]), \
             patch('time.sleep') as sleep_mock, \
             patch('webbrowser.open_new_tab') as open_tab:
            captured['fn']()
        sleep_mock.assert_called_with(0.25)  # the retry sleep fired
        open_tab.assert_called_once()

    def test_wait_and_open_logs_when_healthz_never_responds(self) -> None:
        # Lines 604-606: never-answering branch.
        import os
        import urllib.error
        logger = MagicMock()
        captured = {}

        def capture_thread(*args, **kwargs):
            captured['fn'] = kwargs.get('target')
            return MagicMock()

        with patch.dict(os.environ, {'KATO_OPEN_BROWSER': '1'}, clear=False), \
             patch('threading.Thread', side_effect=capture_thread):
            main_module._open_browser_when_ready('http://localhost', logger)

        # Make urlopen always raise and time.monotonic blow past the
        # deadline so the loop exits via the else branch.
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('refused')), \
             patch('time.monotonic', side_effect=[0.0, 100.0]), \
             patch('time.sleep'):
            captured['fn']()
        logger.warning.assert_called()

    def test_wait_and_open_swallows_browser_exception(self) -> None:
        # Lines 607-610: ``webbrowser.open_new_tab`` raises → log + skip.
        import os
        logger = MagicMock()
        captured = {}

        def capture_thread(*args, **kwargs):
            captured['fn'] = kwargs.get('target')
            return MagicMock()

        with patch.dict(os.environ, {'KATO_OPEN_BROWSER': '1'}, clear=False), \
             patch('threading.Thread', side_effect=capture_thread):
            main_module._open_browser_when_ready('http://localhost', logger)

        with patch('urllib.request.urlopen') as urlopen, \
             patch('webbrowser.open_new_tab',
                   side_effect=RuntimeError('no display')):
            urlopen.return_value.__enter__ = MagicMock()
            urlopen.return_value.__exit__ = MagicMock()
            captured['fn']()
        logger.exception.assert_called()


class RegisterShutdownHookFiringTests(unittest.TestCase):
    def test_handler_logs_signal_and_exits(self) -> None:
        # Lines 621-626: the handler body.
        app = SimpleNamespace(
            service=MagicMock(), logger=MagicMock(),
        )
        original_sigint = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            handler = signal.getsignal(signal.SIGINT)
            self.assertTrue(callable(handler))
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
            app.logger.info.assert_called()
            app.service.shutdown.assert_called()
        finally:
            signal.signal(signal.SIGINT, original_sigint)

    def test_handler_swallows_shutdown_exception(self) -> None:
        # Lines 623-625.
        service = MagicMock()
        service.shutdown.side_effect = RuntimeError('cleanup fail')
        app = SimpleNamespace(service=service, logger=MagicMock())
        original_sigint = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            handler = signal.getsignal(signal.SIGINT)
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
            app.logger.exception.assert_called()
        finally:
            signal.signal(signal.SIGINT, original_sigint)


class ReconcileWorkspaceBranchesRealignTests(unittest.TestCase):
    """Lines 213-249: the realign loop + the success / skip paths."""

    def test_realigns_workspaces_off_task_branch(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / 'PROJ-1' / 'repo-a'
            (clone_path / '.git').mkdir(parents=True)
            workspace_manager = MagicMock()
            workspace_manager.list_workspaces.return_value = [
                SimpleNamespace(task_id='PROJ-1', repository_ids=['repo-a']),
            ]
            workspace_manager.repository_path.return_value = clone_path
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            fake_git = MagicMock()
            fake_git.current_branch.return_value = 'master'  # off target branch
            fake_git.ensure_branch_checked_out.return_value = True
            with patch.dict('sys.modules', {
                'kato_webserver.git_diff_utils': fake_git,
            }, clear=False):
                main_module._reconcile_workspace_branches(app)
        # Realigned exactly one workspace clone.
        msgs = [str(c.args[0]) for c in app.logger.info.call_args_list]
        self.assertTrue(any('realigned' in m.lower() for m in msgs))

    def test_skips_workspace_with_blank_task_id(self) -> None:
        # Line 217-218.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(task_id='', repository_ids=['repo-a']),
        ]
        app = SimpleNamespace(
            workspace_manager=workspace_manager,
            logger=MagicMock(),
        )
        fake_git = MagicMock()
        with patch.dict('sys.modules', {
            'kato_webserver.git_diff_utils': fake_git,
        }, clear=False):
            main_module._reconcile_workspace_branches(app)
        fake_git.current_branch.assert_not_called()

    def test_skips_when_clone_path_missing_git(self) -> None:
        # Line 227: ``if not clone_path.is_dir() or not (clone_path / '.git')``
        # — workspace folder without a .git directory is skipped.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / 'PROJ-1' / 'repo-a'
            clone_path.mkdir(parents=True)  # exists but no .git inside
            workspace_manager = MagicMock()
            workspace_manager.list_workspaces.return_value = [
                SimpleNamespace(task_id='PROJ-1', repository_ids=['repo-a']),
            ]
            workspace_manager.repository_path.return_value = clone_path
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            fake_git = MagicMock()
            with patch.dict('sys.modules', {
                'kato_webserver.git_diff_utils': fake_git,
            }, clear=False):
                main_module._reconcile_workspace_branches(app)
        fake_git.current_branch.assert_not_called()

    def test_skips_when_already_on_target_branch(self) -> None:
        # Line 231: ``if on == task_id: continue``.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / 'PROJ-1' / 'repo-a'
            (clone_path / '.git').mkdir(parents=True)
            workspace_manager = MagicMock()
            workspace_manager.list_workspaces.return_value = [
                SimpleNamespace(task_id='PROJ-1', repository_ids=['repo-a']),
            ]
            workspace_manager.repository_path.return_value = clone_path
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            fake_git = MagicMock()
            fake_git.current_branch.return_value = 'PROJ-1'  # already on target
            with patch.dict('sys.modules', {
                'kato_webserver.git_diff_utils': fake_git,
            }, clear=False):
                main_module._reconcile_workspace_branches(app)
        fake_git.ensure_branch_checked_out.assert_not_called()

    def test_skips_repository_path_exception(self) -> None:
        # Lines 224-225.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(task_id='T1', repository_ids=['repo-a']),
        ]
        workspace_manager.repository_path.side_effect = RuntimeError('fail')
        app = SimpleNamespace(
            workspace_manager=workspace_manager,
            logger=MagicMock(),
        )
        fake_git = MagicMock()
        with patch.dict('sys.modules', {
            'kato_webserver.git_diff_utils': fake_git,
        }, clear=False):
            main_module._reconcile_workspace_branches(app)
        # No realign happened — the path error was swallowed.
        fake_git.current_branch.assert_not_called()

    def test_logs_skipped_when_branch_checkout_fails(self) -> None:
        # Lines 238-239 + 245-250.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / 'PROJ-1' / 'repo-a'
            (clone_path / '.git').mkdir(parents=True)
            workspace_manager = MagicMock()
            workspace_manager.list_workspaces.return_value = [
                SimpleNamespace(task_id='PROJ-1', repository_ids=['repo-a']),
            ]
            workspace_manager.repository_path.return_value = clone_path
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            fake_git = MagicMock()
            fake_git.current_branch.return_value = 'master'
            fake_git.ensure_branch_checked_out.return_value = False
            with patch.dict('sys.modules', {
                'kato_webserver.git_diff_utils': fake_git,
            }, clear=False):
                main_module._reconcile_workspace_branches(app)
        # Skipped warning was emitted.
        app.logger.warning.assert_called()


class ResetStuckPromoteSuccessTests(unittest.TestCase):
    """Lines 277-278: log when at least one workspace is promoted."""

    def test_logs_promoted_count(self) -> None:
        record = SimpleNamespace(
            task_id='PROJ-1', status='provisioning',
            repository_ids=['repo-a'],
        )
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [record]
        with patch.object(
            main_module, '_provisioning_workspace_has_git_repo',
            return_value=True,
        ):
            app = SimpleNamespace(
                workspace_manager=workspace_manager,
                logger=MagicMock(),
            )
            main_module._reset_stuck_workspace_statuses(app)
        # info call for the promoted count fires.
        info_calls = [c.args[0] for c in app.logger.info.call_args_list]
        self.assertTrue(any(
            'promoted' in str(m).lower() for m in info_calls
        ))


class ResumeStreamingSessionsImportErrorTests(unittest.TestCase):
    def test_swallows_workspace_manager_status_import_error(self) -> None:
        # Lines 385-386.
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=MagicMock(),
            planning_session_runner=None,
            logger=MagicMock(),
        )
        with patch.dict('sys.modules', {
            'kato_core_lib.data_layers.service.workspace_manager': None,
        }):
            main_module._resume_streaming_sessions(app)


class ResetStuckImportErrorTests(unittest.TestCase):
    def test_swallows_workspace_manager_import_error(self) -> None:
        # Lines 277-278.
        app = SimpleNamespace(
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )
        with patch.dict('sys.modules', {
            'kato_core_lib.data_layers.service.workspace_manager': None,
        }):
            main_module._reset_stuck_workspace_statuses(app)


class ResumeStreamingSessionsSkipsRepoPathExceptionTests(unittest.TestCase):
    def test_swallows_repo_path_lookup_exception_during_cwd_resolve(
        self,
    ) -> None:
        # Lines 413-414: ``except Exception: continue`` inside the cwd
        # fallback loop.
        workspace_manager = MagicMock()
        workspace_manager.list_workspaces.return_value = [
            SimpleNamespace(
                task_id='T1', status='active',
                cwd='', repository_ids=['repo-a'],
                task_summary='',
            ),
        ]
        workspace_manager.repository_path.side_effect = RuntimeError('fail')
        session_manager = MagicMock()
        app = SimpleNamespace(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
            logger=MagicMock(),
        )
        main_module._resume_streaming_sessions(app)
        # No session started; no cwd resolvable.
        session_manager.start_session.assert_not_called()


class StartPlanningWebserverFullPathTests(unittest.TestCase):
    def test_skips_when_webserver_import_fails(self) -> None:
        # Lines 536-542: ImportError → warn + return.
        import os
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=MagicMock(),
            planning_session_runner=None,
            service=MagicMock(),
            logger=MagicMock(),
        )
        with patch.dict(os.environ, {'KATO_WEBSERVER_DISABLED': ''}, clear=False), \
             patch.dict('sys.modules',
                        {'kato_webserver.app': None}, clear=False):
            main_module._start_planning_webserver_if_enabled(app)
        app.logger.warning.assert_called()

    def test_spawns_webserver_thread_on_success(self) -> None:
        # Lines 544-576.
        import os
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=MagicMock(),
            planning_session_runner=None,
            service=MagicMock(),
            logger=MagicMock(),
        )
        fake_webserver_module = MagicMock()
        fake_webserver_module.create_app.return_value = MagicMock()
        with patch.dict(os.environ, {'KATO_WEBSERVER_DISABLED': ''},
                        clear=False), \
             patch.dict('sys.modules',
                        {'kato_webserver.app': fake_webserver_module},
                        clear=False), \
             patch('threading.Thread') as thread_cls, \
             patch.object(main_module, '_open_browser_when_ready'):
            main_module._start_planning_webserver_if_enabled(app)
        thread_cls.assert_called()

    def test_serve_thread_swallows_flask_exception(self) -> None:
        # Lines 563-566: ``_serve`` inner function runs flask_app.run()
        # which may raise; the exception must be logged + swallowed so
        # the daemon thread dies cleanly.
        import os
        app = SimpleNamespace(
            session_manager=MagicMock(),
            workspace_manager=MagicMock(),
            planning_session_runner=None,
            service=MagicMock(),
            logger=MagicMock(),
        )
        fake_webserver_module = MagicMock()
        fake_flask = MagicMock()
        fake_flask.run.side_effect = RuntimeError('flask crashed')
        fake_webserver_module.create_app.return_value = fake_flask
        captured_target = {}

        def capture_thread(*args, **kwargs):
            # Save the target so we can call it directly in-thread.
            captured_target['fn'] = kwargs.get('target')
            return MagicMock()

        with patch.dict(os.environ, {'KATO_WEBSERVER_DISABLED': ''},
                        clear=False), \
             patch.dict('sys.modules',
                        {'kato_webserver.app': fake_webserver_module},
                        clear=False), \
             patch('threading.Thread', side_effect=capture_thread), \
             patch.object(main_module, '_open_browser_when_ready'):
            main_module._start_planning_webserver_if_enabled(app)

        # Now actually call _serve to drive the exception handler.
        captured_target['fn']()
        app.logger.exception.assert_called()


class ScanLoopExitConditionTests(unittest.TestCase):
    """Line 684: force_scan_event.clear() inside the loop."""

    def test_force_scan_event_clear_called_each_cycle(self) -> None:
        # ``scan_interval_seconds=0.01`` keeps the loop alive — ``<=0``
        # is now the "manual-only" sentinel that skips the loop.
        event = threading.Event()
        event.set()
        app = MagicMock()
        app.logger = MagicMock()
        with patch.object(main_module, 'ProcessAssignedTasksJob') as job_cls, \
             patch.object(main_module, 'supports_inline_status',
                          return_value=False):
            job_cls.return_value = MagicMock()
            main_module._run_task_scan_loop(
                app,
                startup_delay_seconds=0,
                scan_interval_seconds=0.01,
                sleep_fn=lambda _s: None,
                max_cycles=1,
                force_scan_event=event,
            )
        # Event was cleared at start of cycle.
        self.assertFalse(event.is_set())


class MainModuleScriptEntryTests(unittest.TestCase):
    """Line 778: ``if __name__ == '__main__': raise SystemExit(main())``."""

    def test_module_as_script_entry_point(self) -> None:
        # Line 778: ``if __name__ == '__main__': raise SystemExit(main())``.
        # ``main`` is wrapped by hydra so the actual exit code depends on
        # hydra internals; we just verify that running as __main__ raises
        # SystemExit.
        import runpy
        import sys
        old_argv = sys.argv
        sys.argv = ['main']
        try:
            with patch.object(main_module, 'main', return_value=0), \
                 self.assertRaises(SystemExit):
                runpy.run_module('kato_core_lib.main', run_name='__main__')
        finally:
            sys.argv = old_argv


class LoadHooksOrRefuseBranchTests(unittest.TestCase):
    """Lines 547-548: when the loaded hooks config has entries, log
    the configured points + counts."""

    def test_non_empty_hooks_config_logs_point_summary(self) -> None:
        from kato_core_lib.hooks.config import HookConfig, HookDefinition, HookPoint
        # Build a non-empty config with two points.
        h1 = HookDefinition(point=HookPoint.SESSION_END, command='echo', match={}, timeout_seconds=1.0)
        h2 = HookDefinition(point=HookPoint.SESSION_START, command='echo', match={}, timeout_seconds=1.0)
        config = HookConfig(hooks_by_point={
            HookPoint.SESSION_END: [h1],
            HookPoint.SESSION_START: [h2],
        })
        app = SimpleNamespace()
        logger = MagicMock()
        # The imports happen inside the function — patch where the
        # symbols actually live, not on the main module.
        with patch('kato_core_lib.hooks.config.load_hooks_config', return_value=config), \
             patch('kato_core_lib.hooks.runner.HookRunner'):
            main_module._load_hooks_or_refuse(app, logger)
        logger.info.assert_called_once()
        msg = logger.info.call_args[0][0]
        self.assertIn('hooks loaded', msg)


class LogKnownSessionIdsBranchTests(unittest.TestCase):
    """Lines 756-758, 760-761, 764-767, 769 — every branch of
    ``_log_known_session_ids``."""

    def _app_with_records(self, records_or_exc):
        session_manager = MagicMock()
        if isinstance(records_or_exc, Exception):
            session_manager.list_records.side_effect = records_or_exc
        else:
            session_manager.list_records.return_value = records_or_exc
        service = SimpleNamespace(_session_manager=session_manager)
        return SimpleNamespace(service=service, logger=MagicMock())

    def test_list_records_exception_logged_and_returns(self) -> None:
        app = self._app_with_records(RuntimeError('db gone'))
        main_module._log_known_session_ids(app)
        app.logger.exception.assert_called_once()

    def test_empty_records_logs_info_and_returns(self) -> None:
        app = self._app_with_records([])
        main_module._log_known_session_ids(app)
        # The "no known Claude session ids" message fires.
        msg = app.logger.info.call_args[0][0]
        self.assertIn('no known Claude session ids', msg)

    def test_records_with_ids_log_per_task_line(self) -> None:
        records = [
            SimpleNamespace(task_id='PROJ-1', agent_session_id='sess-a'),
            SimpleNamespace(task_id='PROJ-2', agent_session_id='sess-b'),
            SimpleNamespace(task_id='', agent_session_id='no-task'),  # skipped
        ]
        app = self._app_with_records(records)
        main_module._log_known_session_ids(app)
        # Last info call should be the multi-line "known Claude session ids" log.
        info_msg = app.logger.info.call_args[0][0]
        self.assertIn('known Claude session ids', info_msg)

    def test_records_present_but_all_skipped_logs_no_ids(self) -> None:
        # Records exist but every one is missing either task_id or agent_session_id
        # → ``lines`` ends empty → fall to the "no Claude session ids
        # recorded at startup" else branch.
        records = [
            SimpleNamespace(task_id='', agent_session_id='x'),
            SimpleNamespace(task_id='y', agent_session_id=''),
        ]
        app = self._app_with_records(records)
        main_module._log_known_session_ids(app)
        msg = app.logger.info.call_args[0][0]
        self.assertIn('no Claude session ids recorded', msg)


class WaitForPlanningUiHealthzTests(unittest.TestCase):
    """Lines 837, 839-840 in ``_wait_for_planning_ui_healthz``."""

    def test_healthz_success_returns_immediately(self) -> None:
        # Line 837: a successful urlopen returns from the function.
        logger = MagicMock()
        # Patch urllib.request.urlopen to context-manage successfully.
        ok_response = MagicMock()
        ok_response.__enter__ = MagicMock(return_value=ok_response)
        ok_response.__exit__ = MagicMock(return_value=False)
        with patch('urllib.request.urlopen', return_value=ok_response):
            main_module._wait_for_planning_ui_healthz('http://x', logger)
        # No warning logged on success.
        logger.warning.assert_not_called()

    def test_healthz_failure_then_success_eventually_returns(self) -> None:
        # Lines 838-839: first attempt raises (URLError), short sleep,
        # second attempt succeeds.
        import urllib.error
        logger = MagicMock()
        ok_response = MagicMock()
        ok_response.__enter__ = MagicMock(return_value=ok_response)
        ok_response.__exit__ = MagicMock(return_value=False)
        with patch(
            'urllib.request.urlopen',
            side_effect=[urllib.error.URLError('not yet'), ok_response],
        ), patch('time.sleep') as sleep_mock:
            main_module._wait_for_planning_ui_healthz('http://x', logger)
        sleep_mock.assert_called()
        logger.warning.assert_not_called()

    def test_healthz_deadline_exceeded_logs_warning(self) -> None:
        # Line 840: ``logger.warning(...)`` when the loop runs past
        # the 15s deadline without a single successful healthz.
        # Advance ``time.monotonic`` past the deadline on the second
        # check so the loop exits the while.
        import urllib.error
        logger = MagicMock()
        # Sequence: start (t=0), deadline check 1 (t=0, enter loop),
        # deadline check 2 (t=99, exit loop) → no successes.
        monotonic_values = iter([0.0, 0.0, 99.0])
        with patch(
            'time.monotonic',
            side_effect=lambda: next(monotonic_values),
        ), patch(
            'urllib.request.urlopen',
            side_effect=urllib.error.URLError('never up'),
        ), patch('time.sleep'):
            main_module._wait_for_planning_ui_healthz('http://x', logger)
        # The warning about queued comments dispatching anyway must fire.
        logger.warning.assert_called_once()
        msg = logger.warning.call_args[0][0]
        self.assertIn('did not answer', msg)


if __name__ == '__main__':
    unittest.main()
