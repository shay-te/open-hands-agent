import unittest
from unittest.mock import Mock, patch


from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.data.fields import EmailFields, PullRequestFields
from utils import build_task, build_test_cfg


class NotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self.email_core_lib = Mock()
        self.service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

    def test_notify_task_ready_for_review_sends_to_all_recipients(self) -> None:
        result = self.service.notify_task_ready_for_review(
            build_task(),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            },
        )

        self.assertTrue(result)
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        first_call = self.email_core_lib.send.call_args_list[0]
        self.assertEqual(first_call.args[0], '77')
        self.assertEqual(first_call.args[1][EmailFields.EMAIL], 'reviewers@example.com')
        self.assertEqual(first_call.args[1][EmailFields.TASK_ID], 'PROJ-1')
        self.assertEqual(
            first_call.args[1][EmailFields.MESSAGE],
            (
                'I am done with task PROJ-1: Fix bug.\n'
                'Please review it.\n\n'
                '- client: PROJ-1: Fix bug\n'
                'https://bitbucket/pr/17'
            ),
        )

    def test_init_allows_disabled_failure_email_config(self) -> None:
        self.cfg.openhands_agent.failure_email.enabled = False
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        self.assertIsInstance(service, NotificationService)

    def test_notify_failure_renders_message_from_template(self) -> None:
        result = self.service.notify_failure(
            'process_assigned_tasks',
            RuntimeError('boom'),
            {'task_id': 'PROJ-1'},
        )

        self.assertTrue(result)
        first_call = self.email_core_lib.send.call_args_list[0]
        self.assertEqual(
            first_call.args[1][EmailFields.MESSAGE],
            (
                'Operation: process_assigned_tasks\n\n'
                'Error:\n'
                'boom\n\n'
                'Context:\n'
                '{"task_id": "PROJ-1"}'
            ),
        )

    def test_notify_task_ready_for_review_continues_after_single_recipient_failure(self) -> None:
        self.email_core_lib.send.side_effect = [RuntimeError('smtp down'), True]

        result = self.service.notify_task_ready_for_review(
            build_task(),
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            },
        )

        self.assertTrue(result)
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_notify_task_ready_for_review_returns_false_when_all_recipients_fail(self) -> None:
        self.email_core_lib.send.side_effect = RuntimeError('smtp down')

        result = self.service.notify_task_ready_for_review(
            build_task(),
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            },
        )

        self.assertFalse(result)
        self.assertEqual(self.email_core_lib.send.call_count, 2)

    def test_init_allows_disabled_completion_email_config(self) -> None:
        self.cfg.openhands_agent.completion_email.enabled = False
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        self.assertIsInstance(service, NotificationService)

    def test_init_requires_email_core_lib(self) -> None:
        with self.assertRaisesRegex(AssertionError, 'email_core_lib is required'):
            NotificationService(
                app_name=self.cfg.core_lib.app.name,
                email_core_lib=None,
                failure_email_cfg=self.cfg.openhands_agent.failure_email,
                completion_email_cfg=self.cfg.openhands_agent.completion_email,
            )

    def test_notify_failure_returns_false_without_recipients(self) -> None:
        self.cfg.openhands_agent.failure_email.recipients = []
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_failure('process_assigned_tasks', RuntimeError('boom'))

        self.assertFalse(result)
        self.email_core_lib.send.assert_not_called()

    def test_notify_task_ready_for_review_returns_false_without_template_id(self) -> None:
        self.cfg.openhands_agent.completion_email.template_id = ''
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_task_ready_for_review(
            build_task(),
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            },
        )

        self.assertFalse(result)
        self.email_core_lib.send.assert_not_called()

    def test_notify_failure_uses_empty_sender_fields_when_sender_is_partial(self) -> None:
        self.cfg.openhands_agent.failure_email.sender = {'name': 'OpenHands Agent'}
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_failure('process_assigned_tasks', RuntimeError('boom'))

        self.assertTrue(result)
        self.assertEqual(
            self.email_core_lib.send.call_args.args[2],
            {'name': 'OpenHands Agent', 'email': ''},
        )

    def test_notify_failure_treats_string_recipient_as_single_recipient(self) -> None:
        self.cfg.openhands_agent.failure_email.recipients = 'ops@example.com'
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_failure('process_assigned_tasks', RuntimeError('boom'))

        self.assertTrue(result)
        self.email_core_lib.send.assert_called_once()
        self.assertEqual(
            self.email_core_lib.send.call_args.args[1][EmailFields.EMAIL],
            'ops@example.com',
        )

    def test_notify_failure_returns_false_for_invalid_recipient_container(self) -> None:
        self.cfg.openhands_agent.failure_email.recipients = 17
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_failure('process_assigned_tasks', RuntimeError('boom'))

        self.assertFalse(result)
        self.email_core_lib.send.assert_not_called()

    def test_notify_failure_uses_empty_message_when_template_file_is_missing(self) -> None:
        self.cfg.openhands_agent.failure_email.body_template = 'missing.j2'
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_failure('process_assigned_tasks', RuntimeError('boom'))

        self.assertTrue(result)
        self.assertEqual(self.email_core_lib.send.call_args.args[1][EmailFields.MESSAGE], '')

    def test_notify_failure_logs_when_template_file_is_missing(self) -> None:
        self.cfg.openhands_agent.failure_email.body_template = 'missing.j2'
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )
        service.logger = Mock()

        with patch.object(service, 'logger', service.logger):
            service.notify_failure('process_assigned_tasks', RuntimeError('boom'))

        service.logger.exception.assert_called_once()

    def test_notify_task_ready_for_review_uses_empty_message_when_template_file_is_missing(self) -> None:
        self.cfg.openhands_agent.completion_email.body_template = 'missing.j2'
        service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )

        result = service.notify_task_ready_for_review(
            build_task(),
            {
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
            },
        )

        self.assertTrue(result)
        self.assertEqual(self.email_core_lib.send.call_args.args[1][EmailFields.MESSAGE], '')

    def test_notify_task_ready_for_review_logs_send_failures(self) -> None:
        self.email_core_lib.send.side_effect = RuntimeError('smtp down')
        self.service.logger = Mock()

        with patch.object(self.service, 'logger', self.service.logger):
            self.service.notify_task_ready_for_review(
                build_task(),
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.TITLE: 'PROJ-1: Fix bug',
                    PullRequestFields.URL: 'https://bitbucket/pr/17',
                },
            )

        self.assertEqual(self.service.logger.exception.call_count, 2)

    def test_pull_request_summary_uses_one_block_per_pull_request(self) -> None:
        summary = self.service._pull_request_summary(
            [
                {
                    PullRequestFields.REPOSITORY_ID: 'client',
                    PullRequestFields.TITLE: 'Client fix',
                    PullRequestFields.URL: 'https://bitbucket/pr/17',
                },
                {
                    PullRequestFields.REPOSITORY_ID: 'backend',
                    PullRequestFields.TITLE: 'Backend fix',
                    PullRequestFields.URL: '',
                },
            ]
        )

        self.assertEqual(
            summary,
            '- client: Client fix\nhttps://bitbucket/pr/17\n\n- backend: Backend fix',
        )
