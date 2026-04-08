import types
import unittest
from unittest.mock import Mock, call, patch


from kato.main import _run_task_scan_loop, main
from utils import build_test_cfg


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_main_returns_zero_on_success(self) -> None:
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato.main.KatoInstance.init') as mock_init, patch(
            'kato.main.KatoInstance.get',
            return_value=app,
        ), patch('kato.main._run_task_scan_loop') as mock_run_loop:
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_init.assert_called_once_with(self.cfg)
        mock_run_loop.assert_called_once_with(
            app,
            startup_delay_seconds=30.0,
            scan_interval_seconds=60.0,
        )
        app.logger.info.assert_any_call('starting kato agent')

    def test_main_configures_logger_when_app_logger_is_missing(self) -> None:
        configured_logger = Mock()
        app = types.SimpleNamespace(logger=None)

        with patch('kato.main.configure_logger', return_value=configured_logger), patch(
            'kato.main.KatoInstance.init'
        ), patch(
            'kato.main.KatoInstance.get',
            return_value=app,
        ), patch('kato.main._run_task_scan_loop'):
            main(self.cfg)

        self.assertIs(app.logger, configured_logger)

    def test_run_task_scan_loop_waits_before_first_scan_and_sleeps_between_cycles(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [None, None]

        with patch('kato.main.ProcessAssignedTasksJob', return_value=job) as mock_job_cls, patch(
            'kato.main.time.sleep'
        ) as mock_sleep:
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
        mock_sleep.assert_has_calls([call(30.0), call(60.0)])
        app.logger.info.assert_any_call(
            'waiting %s seconds for Kato to warm up before scanning tasks',
            30.0,
        )

    def test_run_task_scan_loop_continues_after_failure(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [RuntimeError('service down'), None]

        with patch('kato.main.ProcessAssignedTasksJob', return_value=job), patch(
            'kato.main.time.sleep'
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

    def test_main_returns_one_without_traceback_when_startup_validation_fails(self) -> None:
        configured_logger = Mock()
        startup_error = RuntimeError(
            '[Error] /workspace/project missing git permissions. cannot work.'
        )

        with patch('kato.main.configure_logger', return_value=configured_logger), patch(
            'kato.main.KatoInstance.init',
            side_effect=startup_error,
        ):
            result = main(self.cfg)

        self.assertEqual(result, 1)
        configured_logger.error.assert_called_once_with('%s', startup_error)
