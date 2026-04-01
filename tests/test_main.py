import types
import unittest
from unittest.mock import Mock, patch


from openhands_agent.main import main
from utils import build_test_cfg


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_main_returns_zero_on_success(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(
                get_assigned_tasks=Mock(return_value=['task-1']),
                process_assigned_task=Mock(return_value={'id': '17'}),
                get_new_pull_request_comments=Mock(return_value=['comment-1']),
                process_review_comment=Mock(return_value={'status': 'updated'}),
            ),
        )

        with patch('openhands_agent.main.OpenHandsAgentInstance.init') as mock_init, patch(
            'openhands_agent.main.OpenHandsAgentInstance.get',
            return_value=app,
        ):
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_init.assert_called_once_with(self.cfg)
        app.service.get_assigned_tasks.assert_called_once_with()
        app.service.process_assigned_task.assert_called_once_with('task-1')
        app.service.get_new_pull_request_comments.assert_called_once_with()
        app.service.process_review_comment.assert_called_once_with('comment-1')
        app.logger.info.assert_any_call('starting openhands agent')

    def test_main_sends_failure_notification_before_reraising(self) -> None:
        notification_service = types.SimpleNamespace(notify_failure=Mock())
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(
                get_assigned_tasks=Mock(side_effect=RuntimeError('service down')),
                get_new_pull_request_comments=Mock(return_value=[]),
                notification_service=notification_service,
            ),
        )

        with patch('openhands_agent.main.OpenHandsAgentInstance.init'), patch(
            'openhands_agent.main.OpenHandsAgentInstance.get',
            return_value=app,
        ):
            with self.assertRaisesRegex(RuntimeError, 'service down'):
                main(self.cfg)

        notification_service.notify_failure.assert_called_once()
        app.logger.exception.assert_called_once_with('failed to process assigned task')

    def test_main_preserves_original_error_when_failure_notification_breaks(self) -> None:
        notification_service = types.SimpleNamespace(
            notify_failure=Mock(side_effect=RuntimeError('mailer down'))
        )
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(
                get_assigned_tasks=Mock(side_effect=RuntimeError('service down')),
                get_new_pull_request_comments=Mock(return_value=[]),
                notification_service=notification_service,
            ),
        )

        with patch('openhands_agent.main.OpenHandsAgentInstance.init'), patch(
            'openhands_agent.main.OpenHandsAgentInstance.get',
            return_value=app,
        ):
            with self.assertRaisesRegex(RuntimeError, 'service down'):
                main(self.cfg)

        self.assertEqual(app.logger.exception.call_count, 2)

    def test_main_configures_logger_when_app_logger_is_missing(self) -> None:
        configured_logger = Mock()
        app = types.SimpleNamespace(
            logger=None,
            service=types.SimpleNamespace(
                get_assigned_tasks=Mock(return_value=[]),
                process_assigned_task=Mock(),
                get_new_pull_request_comments=Mock(return_value=[]),
                process_review_comment=Mock(),
            ),
        )

        with patch('openhands_agent.main.configure_logger', return_value=configured_logger), patch(
            'openhands_agent.main.OpenHandsAgentInstance.init'
        ), patch(
            'openhands_agent.main.OpenHandsAgentInstance.get',
            return_value=app,
        ):
            main(self.cfg)

        self.assertIs(app.logger, configured_logger)

    def test_main_returns_one_without_traceback_when_startup_validation_fails(self) -> None:
        configured_logger = Mock()
        startup_error = RuntimeError(
            '[Error] /workspace/project missing git permissions. cannot work.'
        )

        with patch('openhands_agent.main.configure_logger', return_value=configured_logger), patch(
            'openhands_agent.main.OpenHandsAgentInstance.init',
            side_effect=startup_error,
        ):
            result = main(self.cfg)

        self.assertEqual(result, 1)
        configured_logger.error.assert_called_once_with('%s', startup_error)
