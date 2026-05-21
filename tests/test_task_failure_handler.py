import types
import unittest
from unittest.mock import Mock, patch

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryIgnoredByConfigError,
)
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.data_layers.service.task_service import TaskService
from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    ScanReport,
    SecurityFinding,
    Severity,
)
from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
    SecurityScanBlocked,
)
from tests.utils import build_task


class TaskFailureHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_state_service = Mock(spec=TaskStateService)
        self.repository_service = Mock(spec=RepositoryService)
        self.notification_service = Mock(spec=NotificationService)
        self.handler = TaskFailureHandler(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
        )
        self.handler.logger = Mock()

    def test_handle_repository_resolution_failure_comments_skip_for_repository_detection_error(
        self,
    ) -> None:
        task = build_task(description='whats wrong with you please fix it')

        self.handler.handle_repository_resolution_failure(
            task,
            ValueError('no configured repository matched task PROJ-1'),
        )

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('could not detect which repository', comment)
        # Comment must walk the operator through the actual fix:
        # the ``kato:repo:<id>`` tag format and the picker that
        # lists the legal ids. Without these, the operator gets
        # "kato can't pick a repo" with no obvious next step.
        self.assertIn('kato:repo:<repository-id>', comment)
        self.assertIn('./kato approve-repo', comment)
        self.task_state_service.move_task_to_open.assert_not_called()
        self.notification_service.notify_failure.assert_not_called()

    def test_handle_repository_resolution_failure_comments_rejection_for_ignored_repo(
        self,
    ) -> None:
        # Tag points at an ignored folder → kato refuses, posts an
        # actionable comment, does NOT reopen the task or notify ops.
        # The next scan will hit the same rejection until the operator
        # fixes the tag or the ignore list.
        task = build_task(description='Multi-repo task with one ignored tag')

        self.handler.handle_repository_resolution_failure(
            task,
            RepositoryIgnoredByConfigError(
                'task PROJ-1 references repositories that are in '
                'KATO_IGNORED_REPOSITORY_FOLDERS: forbidden-repo. '
                'Either remove the kato:repo:<name> tag from the task or '
                'remove the folder from KATO_IGNORED_REPOSITORY_FOLDERS.'
            ),
        )

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('Kato refused to run this task', comment)
        self.assertIn('KATO_IGNORED_REPOSITORY_FOLDERS', comment)
        self.assertIn('forbidden-repo', comment)
        self.assertIn('kato:repo:<name>', comment)
        # Not a "stop and notify" failure — this is a config issue the
        # operator owns, not an outage to page on.
        self.task_state_service.move_task_to_open.assert_not_called()
        self.notification_service.notify_failure.assert_not_called()
        self.repository_service.restore_task_repositories.assert_not_called()

    def test_handle_task_failure_restores_repositories_and_notifies_without_reopening(self) -> None:
        prepared_task = types.SimpleNamespace(repositories=[types.SimpleNamespace(id='client')])
        task = build_task(description='whats wrong with you please fix it')

        self.handler.handle_task_failure(
            task,
            RuntimeError('repository service down'),
            prepared_task=prepared_task,
        )

        self.repository_service.restore_task_repositories.assert_called_once_with(
            prepared_task.repositories,
            force=True,
        )
        self.task_service.add_comment.assert_called_once()
        self.assertIn(
            'Kato agent could not safely process this task: repository service down',
            self.task_service.add_comment.call_args.args[1],
        )
        self.task_state_service.move_task_to_open.assert_not_called()
        self.notification_service.notify_failure.assert_called_once()
        notify_args = self.notification_service.notify_failure.call_args.args
        self.assertEqual(notify_args[0], 'process_assigned_task')
        self.assertEqual(str(notify_args[1]), 'repository service down')
        self.assertEqual(notify_args[2], {Task.id.key: task.id})

    def test_handle_started_task_failure_moves_task_back_to_open(self) -> None:
        prepared_task = types.SimpleNamespace(repositories=[types.SimpleNamespace(id='client')])
        task = build_task(description='whats wrong with you please fix it')

        self.handler.handle_started_task_failure(
            task,
            RuntimeError('push failed'),
            prepared_task=prepared_task,
        )

        self.repository_service.restore_task_repositories.assert_called_once_with(
            prepared_task.repositories,
            force=True,
        )
        self.task_state_service.move_task_to_open.assert_called_once_with(task.id)
        self.task_service.add_comment.assert_called_once()
        self.assertIn(
            'Kato agent stopped working on this task: push failed',
            self.task_service.add_comment.call_args.args[1],
        )
        self.notification_service.notify_failure.assert_called_once()
        notify_args = self.notification_service.notify_failure.call_args.args
        self.assertEqual(notify_args[0], 'process_assigned_task')
        self.assertEqual(str(notify_args[1]), 'push failed')
        self.assertEqual(notify_args[2], {Task.id.key: task.id})

    def test_handle_task_definition_failure_comments_skip_message(self) -> None:
        task = build_task(description='test')

        self.handler.handle_task_definition_failure(task)

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('task definition is too thin', comment)
        # Operator must see the bulleted what/why/how-to-tell rubric
        # and the "remove + re-add the kato:run tag" retry hint —
        # otherwise they re-edit the description ad hoc and the
        # next scan still rejects.
        self.assertIn('what', comment)
        self.assertIn('why', comment)
        self.assertIn('kato:run', comment)
        self.handler.logger.info.assert_any_call(
            'Mission %s: %s',
            task.id,
            'recording task-definition skip comment',
        )
        self.handler.logger.info.assert_any_call(
            'Mission %s: %s',
            task.id,
            'added task-definition skip comment',
        )


def _build_blocked_report() -> ScanReport:
    findings = (
        SecurityFinding(
            tool='detect-secrets',
            severity=Severity.CRITICAL,
            rule_id='AWSKeyDetector',
            message='AWS access key committed in tracked file',
            path='src/config.py',
            line=12,
        ),
        SecurityFinding(
            tool='safety',
            severity=Severity.HIGH,
            rule_id='CVE-2023-12345',
            message='requests<2.31.0 has known CVE',
            path='requirements.txt',
            line=0,
        ),
    )
    return ScanReport(
        findings=findings,
        blocking=True,
        block_threshold=Severity.HIGH,
    )


class SecurityScanBlockedCommentTests(unittest.TestCase):
    """The failure comment must carry the detail breakdown, not just
    the one-line summary. Before this fix the YouTrack comment said
    "See ticket comment for details" but the only content WAS that
    short line — the operator had no idea what tripped which scanner."""

    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_state_service = Mock(spec=TaskStateService)
        self.repository_service = Mock(spec=RepositoryService)
        self.notification_service = Mock(spec=NotificationService)
        self.handler = TaskFailureHandler(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
        )
        self.handler.logger = Mock()

    def test_handle_task_failure_with_scan_block_includes_markdown_table(self) -> None:
        task = build_task(description='do the thing')
        error = SecurityScanBlocked(_build_blocked_report())

        self.handler.handle_task_failure(task, error)

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        # Short lead line still on top (notification preview relies on it).
        self.assertTrue(
            comment.startswith('Kato agent could not safely process this task: '),
            comment,
        )
        # Detail markdown follows — table headers + one row per finding.
        self.assertIn('| severity | tool | path | rule | message |', comment)
        self.assertIn('| critical | detect-secrets |', comment)
        self.assertIn('AWS access key committed', comment)
        self.assertIn('| high | safety |', comment)
        self.assertIn('CVE-2023-12345', comment)
        # And the gate-trip headline.
        self.assertIn('refused this task', comment)

    def test_handle_started_task_failure_with_scan_block_includes_detail(self) -> None:
        # Same enrichment must apply to the "stopped working" path
        # so post-start security blocks (rare but possible if a runner
        # is wired later) also explain themselves.
        task = build_task(description='do the thing')
        error = SecurityScanBlocked(_build_blocked_report())

        self.handler.handle_started_task_failure(task, error)

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertTrue(
            comment.startswith('Kato agent stopped working on this task: '),
            comment,
        )
        self.assertIn('| severity | tool | path | rule | message |', comment)
        self.assertIn('AWS access key committed', comment)

    def test_non_scan_error_unchanged(self) -> None:
        # Regression guard: a plain RuntimeError still produces the
        # original short comment with no spurious markdown.
        task = build_task(description='do the thing')

        self.handler.handle_task_failure(task, RuntimeError('boom'))

        comment = self.task_service.add_comment.call_args.args[1]
        self.assertEqual(
            comment,
            'Kato agent could not safely process this task: boom',
        )
        self.assertNotIn('| severity |', comment)


class SecurityScanDetailFallbackTests(unittest.TestCase):
    """Defensive branches in ``_security_scan_detail`` — covers
    ImportError when ``security_scanner_core_lib`` isn't importable,
    and the ``except Exception`` around ``summarize_for_ticket``."""

    def test_returns_empty_string_when_security_scanner_lib_missing(self) -> None:
        # Simulate the lib being unimportable (operator running kato
        # without the optional security_scanner_core_lib install).
        import builtins
        from kato_core_lib.data_layers.service.task_failure_handler import (
            _security_scan_detail,
        )
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if 'security_scanner_core_lib' in name:
                raise ImportError(f'no module named {name}')
            return real_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=fake_import):
            result = _security_scan_detail(RuntimeError('plain error'))
        self.assertEqual(result, '')

    def test_returns_empty_string_when_summarize_for_ticket_raises(self) -> None:
        # The formatter inside SecurityScannerService may itself fail
        # (template bug, malformed report). The handler must swallow
        # so the operator still gets the short-line comment.
        from kato_core_lib.data_layers.service.task_failure_handler import (
            _security_scan_detail,
        )
        error = SecurityScanBlocked(_build_blocked_report())
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.'
            'security_scanner_service.SecurityScannerService.summarize_for_ticket',
            side_effect=RuntimeError('formatter broke'),
        ):
            result = _security_scan_detail(error)
        self.assertEqual(result, '')
